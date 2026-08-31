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

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class CaptureError(Exception):
    """Raised when audio could not be recorded."""


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


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
    seconds: float = 20.0,
    *,
    device_index: int | None = None,
    runner: Runner | None = None,
) -> Path:
    """Record the amp's output to `dest`."""
    run = runner or _run
    if seconds <= 0:
        raise CaptureError(f"seconds must be positive, got {seconds}")
    if device_index is None:
        device_index, _name = find_amp_device(runner=runner)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = run(capture_argv(device_index, seconds, dest))
    if result.returncode != 0:
        raise CaptureError(f"ffmpeg capture failed: {(result.stderr or '').strip()[-400:]}")
    if runner is None and not dest.exists():
        raise CaptureError(f"ffmpeg reported success but {dest} was not written")
    return dest
