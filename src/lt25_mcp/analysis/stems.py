"""Isolating the guitar from a mix.

Analysing a full mix measures the drummer as much as the guitarist, so the
guitar is separated out first. Meta's Demucs ships a six-source model,
`htdemucs_6s`, which adds guitar and piano to the usual four stems. Meta
labels it experimental; where it fails, falling back to the catch-all `other`
stem is a caller's explicit decision, never automatic.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Sequence

MODEL = "htdemucs_6s"
GUITAR_STEM = "guitar"
FALLBACK_STEM = "other"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class StemError(Exception):
    """Raised when the guitar could not be separated out."""


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def separate_argv(src: Path, out_dir: Path, model: str = MODEL) -> list[str]:
    """The demucs invocation used to split a file into stems."""
    return [
        "demucs",
        "--name", model,
        "--out", str(out_dir),
        "--jobs", "1",
        str(src),
    ]


def stem_path(out_dir: Path, src: Path, stem: str, model: str = MODEL) -> Path:
    """Where demucs writes a given stem."""
    return Path(out_dir) / model / Path(src).stem / f"{stem}.wav"


def isolate_guitar(
    src: Path,
    out_dir: Path,
    *,
    model: str = MODEL,
    allow_fallback: bool = False,
    runner: Runner | None = None,
) -> Path:
    """Separate `src` and return the path to its guitar stem."""
    run = runner or _run
    src, out_dir = Path(src), Path(out_dir)
    if runner is None and shutil.which("demucs") is None:
        raise StemError("demucs is not installed. Install it with `uv add demucs`.")
    if not src.exists():
        raise StemError(f"no such audio file: {src}")
    out_dir.mkdir(parents=True, exist_ok=True)

    result = run(separate_argv(src, out_dir, model))
    if result.returncode != 0:
        raise StemError(f"demucs failed: {(result.stderr or '').strip()[-400:]}")

    guitar = stem_path(out_dir, src, GUITAR_STEM, model)
    if guitar.exists():
        return guitar

    fallback = stem_path(out_dir, src, FALLBACK_STEM, model)
    if allow_fallback and fallback.exists():
        return fallback
    raise StemError(
        f"{model} produced no guitar stem at {guitar}. "
        "Pass allow_fallback=True to analyse the 'other' stem instead, "
        "accepting that it also contains keys and anything else unclassified."
    )
