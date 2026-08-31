"""Measure how much the playing varies between takes, with nothing else changed.

    ./scripts/py scripts/playing_variance.py --takes 4 --seconds 20

The convergence loop assumes the only thing changing between iterations is the
amp. On this amp that assumption is untested: the USB output carries no dry
signal and accepts no playback, so every take is played by hand.

This records several takes back to back with the amp untouched and reports the
distance between them. That distance is the loop's noise floor - moves it
suggests are typically 0.1 to 0.3, so if the noise floor approaches that, the
loop is reading the player rather than the amp.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
from pathlib import Path

from lt25_mcp.analysis.capture import CaptureError, record
from lt25_mcp.analysis.converge import compare
from lt25_mcp.analysis.features import FeatureError, extract

PHRASE = """\
Play the SAME short phrase for every take - the same notes, the same dynamic.
Do not change anything on the amp or the guitar between takes."""

VERDICTS = (
    (0.05, "GOOD", "playing variance sits well below the moves the loop makes; "
                   "automatic convergence is sound"),
    (0.10, "MARGINAL", "comparable to a small move. Use longer takes and a fixed "
                       "phrase, and treat single-iteration changes as noise"),
    (float("inf"), "TOO NOISY", "the loop would be measuring the player, not the "
                                "amp. Do not run it automatically - use it to show "
                                "the gap and let the player choose the move"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--takes", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)

    if args.takes < 2:
        print("error: need at least two takes to compare", file=sys.stderr)
        return 1

    work = args.work_dir or Path(tempfile.mkdtemp(prefix="lt25-variance-"))
    work.mkdir(parents=True, exist_ok=True)
    print(PHRASE)
    print()

    takes = []
    try:
        for i in range(1, args.takes + 1):
            input(f"take {i} of {args.takes} - press enter, then play for "
                  f"{args.seconds:.0f}s: ")
            path = record(work / f"take{i}.wav", args.seconds)
            takes.append(extract(path))
            print(f"  captured take {i}")
    except (CaptureError, FeatureError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nstopped", file=sys.stderr)
        return 1

    print("\ndistance between takes, with nothing changed:")
    distances = []
    for i in range(len(takes)):
        for j in range(i + 1, len(takes)):
            d = compare(takes[i], takes[j]).distance
            distances.append(d)
            print(f"  take {i + 1} vs take {j + 1}:  {d:.4f}")

    worst = max(distances)
    median = statistics.median(distances)
    print(f"\nmedian {median:.4f}   worst {worst:.4f}")
    for threshold, verdict, advice in VERDICTS:
        if worst < threshold:
            print(f"\n{verdict}: {advice}")
            break
    print("\nFor reference, the loop's own convergence threshold is 0.08 and a "
          "typical suggested move shifts distance by 0.1 to 0.3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
