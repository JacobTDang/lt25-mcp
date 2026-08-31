"""Recording the player through the amp's USB audio interface.

The amp presents two USB devices: the HID control channel this project drives,
and an audio interface carrying what is actually being played. Capturing from
the latter is what makes the loop close - a preset can be auditioned, played,
recorded and measured without anything leaving the desk.

Capture goes through ffmpeg's avfoundation input rather than a Python audio
binding, because ffmpeg is already a dependency and this needs no callbacks.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Sequence

AMP_DEVICE_HINT = "Mustang LT"
SAMPLE_RATE = 44100

# Shorter takes measure the performance more than the tone. See
# docs/measurements.md - at 20s two takes of identical playing differ by
# 0.24, well above the 0.08 convergence threshold; at 30s they differ by
# 0.065, inside it.
MIN_TAKE_SECONDS = 30.0

# A capture this quiet is the amp idling, not someone playing. Measured: a
# real take peaks around 0.55 with RMS near 0.09, while nine consecutive
# captures of an idle amp peaked at 0.001-0.006 with RMS near 0.0002. Those
# nine went into a labelled corpus and produced a confident, wrong finding
# before anyone noticed they were silent.
SILENCE_PEAK = 0.02
SILENCE_RMS = 0.002

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class CaptureError(Exception):
    """Raised when audio could not be recorded."""


# How long past the requested duration to wait before giving up. macOS gates
# audio capture behind a Microphone permission, and a process that has not been
# granted it blocks indefinitely with no output at all rather than failing.
CAPTURE_GRACE_S = 15.0


def _run(argv: Sequence[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, check=False, timeout=timeout
    )


def list_devices_argv() -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-f", "avfoundation",
        "-list_devices", "true", "-i", "",
    ]


def parse_devices(output: str) -> dict[int, str]:
    """Pull the audio device list out of ffmpeg's stderr.

    ffmpeg prints video devices first, then audio, both numbered from zero, so
    only lines after the audio header count.
    """
    devices: dict[int, str] = {}
    in_audio = False
    for line in output.splitlines():
        if "audio devices" in line.lower():
            in_audio = True
            continue
        if "video devices" in line.lower():
            in_audio = False
            continue
        if not in_audio:
            continue
        match = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
        if match:
            devices[int(match.group(1))] = match.group(2)
    return devices


def find_amp_device(*, runner: Runner | None = None) -> tuple[int, str]:
    """Locate the amp's audio input among the system's capture devices."""
    run = runner or _run
    result = run(list_devices_argv())
    # ffmpeg exits non-zero after listing devices, which is expected.
    devices = parse_devices((result.stderr or "") + (result.stdout or ""))
    for index, name in devices.items():
        if AMP_DEVICE_HINT.lower() in name.lower():
            return index, name
    raise CaptureError(
        f"no audio input matching {AMP_DEVICE_HINT!r}. Available: "
        + (", ".join(f"[{i}] {n}" for i, n in sorted(devices.items())) or "none")
        + ". Is the amp connected by USB and powered on?"
    )


def capture_argv(device_index: int, seconds: float, dest: Path) -> list[str]:
    return [
        "ffmpeg", "-y", "-hide_banner",
        "-f", "avfoundation",
        "-i", f":{device_index}",
        "-t", f"{seconds}",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        str(dest),
    ]


def record(
    dest: Path,
    seconds: float = MIN_TAKE_SECONDS,
    *,
    device_index: int | None = None,
    runner: Runner | None = None,
    allow_short: bool = False,
    allow_silent: bool = False,
) -> Path:
    """Record the amp's output to `dest`."""
    if seconds <= 0:
        raise CaptureError(f"seconds must be positive, got {seconds}")
    if not allow_short and seconds < MIN_TAKE_SECONDS:
        raise CaptureError(
            f"a {seconds:.0f}s take is too short to measure tone from. Two takes "
            f"of the same playing differ by about 0.24 at 20s but only 0.065 at "
            f"{MIN_TAKE_SECONDS:.0f}s, which is inside the noise floor - a short "
            "take produces knob moves that are chasing the performance. Pass "
            "allow_short=True only for a device check."
        )
    # Injected runners stay argv-only; the timeout belongs to the real one.
    deadline = seconds + CAPTURE_GRACE_S
    run = runner or (lambda argv: _run(argv, timeout=deadline))
    if device_index is None:
        device_index, _name = find_amp_device(runner=runner)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run(capture_argv(device_index, seconds, dest))
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(
            f"capture produced nothing after {deadline:.0f}s. "
            "On macOS this is almost always the Microphone permission: the "
            "terminal running this needs it granted under System Settings > "
            "Privacy & Security > Microphone. Run a capture from your own "
            "shell once to trigger the prompt."
        ) from exc
    if result.returncode != 0:
        raise CaptureError(f"ffmpeg capture failed: {(result.stderr or '').strip()[-400:]}")
    if runner is None and not dest.exists():
        raise CaptureError(f"ffmpeg reported success but {dest} was not written")
    if runner is None and not allow_silent:
        check_has_signal(dest)
    return dest


def check_has_signal(path: Path) -> tuple[float, float]:
    """Refuse a capture that is just the amp idling.

    Silence is worse than a failure here: it parses, it measures, and it
    produces plausible-looking numbers that are entirely noise floor.
    """
    import librosa
    import numpy as np

    y, _sr = librosa.load(str(path), sr=None, mono=True)
    if y.size == 0:
        raise CaptureError(f"{path} contains no audio")
    peak = float(np.max(np.abs(y)))
    rms = float(np.sqrt(np.mean(y**2)))
    if peak < SILENCE_PEAK or rms < SILENCE_RMS:
        raise CaptureError(
            f"captured near-silence (peak {peak:.4f}, rms {rms:.5f}) - the amp "
            "was idling, nobody was playing. A real take peaks around 0.5. "
            "Check the guitar is plugged in, the amp volume is up, and that "
            "someone is playing before the capture starts."
        )
    return peak, rms
