"""Getting audio out of a video link and onto disk.

Downloads are delegated to yt-dlp, trimming to ffmpeg. Both are invoked
through an injectable runner so the argv can be tested without touching the
network.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Sequence

SAMPLE_RATE = 44100

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class AcquisitionError(Exception):
    """Raised when audio could not be fetched or converted."""


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise AcquisitionError(
            f"{tool} is not installed. Install it with `brew install {tool}`."
        )


def download_argv(url: str, dest: Path) -> list[str]:
    """The yt-dlp invocation used to fetch a clip's audio as mono WAV."""
    return [
        "yt-dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "wav",
        "--postprocessor-args", f"ffmpeg:-ac 1 -ar {SAMPLE_RATE}",
        "--output", str(dest),
        url,
    ]


def trim_argv(
    src: Path, start: float | None, end: float | None, dest: Path
) -> list[str]:
    """The ffmpeg invocation used to cut a segment out of a file.

    An omitted `end` means "to the end of the file"; passing a huge sentinel
    instead would put a nonsense timestamp on the command line.
    """
    start = 0.0 if start is None else float(start)
    if end is not None and float(end) <= start:
        raise AcquisitionError(f"end ({end}) must be after start ({start})")
    argv = ["ffmpeg", "-y", "-i", str(src), "-ss", f"{start}"]
    if end is not None:
        argv += ["-to", f"{float(end)}"]
    argv += ["-ac", "1", "-ar", str(SAMPLE_RATE), str(dest)]
    return argv


def fetch_audio(url: str, dest: Path, *, runner: Runner | None = None) -> Path:
    """Download a clip's audio to `dest` as mono WAV."""
    run = runner or _run
    if runner is None:
        _require("yt-dlp")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = run(download_argv(url, dest))
    if result.returncode != 0:
        raise AcquisitionError(f"yt-dlp failed for {url}: {(result.stderr or '').strip()[-400:]}")
    if runner is None and not dest.exists():
        raise AcquisitionError(f"yt-dlp reported success but {dest} was not written")
    return dest


def trim(
    src: Path,
    start: float | None,
    end: float | None,
    dest: Path,
    *,
    runner: Runner | None = None,
) -> Path:
    """Cut `src` down to the segment between `start` and `end` seconds."""
    run = runner or _run
    if runner is None:
        _require("ffmpeg")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = run(trim_argv(Path(src), start, end, dest))
    if result.returncode != 0:
        raise AcquisitionError(f"ffmpeg failed: {(result.stderr or '').strip()[-400:]}")
    return dest
