"""Check whether the pipeline gives the same answer twice.

    ./scripts/py scripts/stability.py path/to/guitar.wav

Runs the analysis over level, section and sample-rate variants of the same
audio. None of those change the tone being played, so a change in the chosen
amp model means the rules are keying on something else.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import _bootstrap  # noqa: F401  (puts src/ on sys.path)

from lt25_mcp.analysis.features import FeatureError
from lt25_mcp.analysis.stability import assess, knob_variance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)

    work = args.work_dir or Path(tempfile.mkdtemp(prefix="lt25-stability-"))
    try:
        report = assess(args.audio, work)
    except FeatureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(report.describe())
    print()
    print("knob std-dev (0-10): " + "  ".join(
        f"{k} {v:.2f}" for k, v in knob_variance(report).items()
    ))
    return 0 if report.is_stable else 2


if __name__ == "__main__":
    raise SystemExit(main())
