"""Measure and tune the gain thresholds against labelled clips.

    ./scripts/py scripts/calibrate.py add <clip.wav> --label high_gain
    ./scripts/py scripts/calibrate.py list
    ./scripts/py scripts/calibrate.py evaluate
    ./scripts/py scripts/calibrate.py sweep
    ./scripts/py scripts/calibrate.py evaluate-models
    ./scripts/py scripts/calibrate.py reverb

`evaluate` scores the current thresholds. `sweep` searches for better ones and
prints what to change in mapping.py - it never edits the module itself, since a
threshold worth adopting is worth reading first.

`evaluate-models` scores the amp model chooser instead of the gain class:
clips recorded through a factory preset have a known true model, and the
report counts exact matches and near misses (right gain family, wrong model).
There is no sweep for this - nine clips over eighteen models is too little
evidence to fit per-model rules to.

`reverb` scores reverb inference on presence versus absence, against the clips
whose true reverb is recorded (pass `--reverb DUBS_...` to `add`, read from the
slot's backup).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (puts src/ on sys.path)

from lt25_mcp.analysis.corpus import (
    LABELS,
    MIN_PER_LABEL,
    Corpus,
    CorpusError,
    evaluate,
    evaluate_models,
    evaluate_reverb,
    sweep,
)
from lt25_mcp.analysis.mapping import CLEAN_FLATNESS, HIGH_GAIN_FLATNESS


def _warn_if_thin(corpus: Corpus) -> None:
    thin = corpus.thin
    if thin:
        counts = corpus.counts()
        print(
            f"note: fewer than {MIN_PER_LABEL} clips for "
            + ", ".join(f"{label} ({counts[label]})" for label in thin)
            + " - results below are indicative, not calibration.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="label a clip and add it to the corpus")
    add.add_argument("clip", type=Path)
    add.add_argument("--label", required=True, choices=LABELS)
    add.add_argument("--source", default="")
    add.add_argument("--notes", default="")
    add.add_argument("--reverb", default="",
                     help="FenderId of the reverb the clip was recorded through "
                          "(from the slot's backup); DUBS_Passthru for none")

    sub.add_parser("list", help="show the corpus")
    sub.add_parser("evaluate", help="score the current thresholds")
    sub.add_parser("sweep", help="search for better thresholds")
    sub.add_parser(
        "evaluate-models", help="score the amp model chooser against known presets"
    )
    sub.add_parser("reverb", help="score reverb inference on presence vs absence")

    remove = sub.add_parser("remove", help="drop a clip from the corpus")
    remove.add_argument("clip", type=Path)

    args = parser.parse_args(argv)

    try:
        corpus = Corpus.load()
        if args.command == "add":
            if not args.clip.exists():
                print(f"error: no such file: {args.clip}", file=sys.stderr)
                return 1
            corpus.add(args.clip.resolve(), args.label, args.source, args.notes,
                       reverb=args.reverb)
            # A source naming a factory preset pins the true amp model too.
            corpus.backfill_amp_models()
            corpus.save()
            counts = corpus.counts()
            print(f"added {args.clip.name} as {args.label}")
            print("corpus: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
            return 0

        if args.command == "remove":
            target = str(args.clip.resolve())
            before = len(corpus.samples)
            corpus.samples = [s for s in corpus.samples if s.path != target]
            corpus.save()
            print(f"removed {before - len(corpus.samples)} entry")
            return 0

        if args.command == "list":
            if not corpus.samples:
                print("corpus is empty")
                return 0
            for s in corpus.samples:
                print(f"  {s.label:10} {Path(s.path).name}"
                      + (f"   [{s.source}]" if s.source else ""))
            print("\ntotals: " + ", ".join(f"{k} {v}" for k, v in corpus.counts().items()))
            _warn_if_thin(corpus)
            return 0

        if not corpus.samples:
            print("corpus is empty - add some labelled clips first", file=sys.stderr)
            return 1

        if args.command == "evaluate":
            _warn_if_thin(corpus)
            print(evaluate(corpus).describe())
            return 0

        if args.command == "reverb":
            print(evaluate_reverb(corpus).describe())
            return 0

        if args.command == "evaluate-models":
            # Clips added before amp_model existed still name their preset in
            # the source string; recover those labels rather than demanding a
            # re-record.
            if corpus.backfill_amp_models():
                corpus.save()
            print(evaluate_models(corpus).describe())
            return 0

        if args.command == "sweep":
            _warn_if_thin(corpus)
            best, tried = sweep(corpus)
            current = evaluate(corpus)
            print(f"searched {len(tried)} threshold pairs\n")
            print(best.describe())
            print()
            if (best.clean_flat, best.high_gain_flat) == (CLEAN_FLATNESS, HIGH_GAIN_FLATNESS):
                print("the current thresholds are already the best fit")
            elif best.accuracy > current.accuracy:
                print("to adopt, edit src/lt25_mcp/analysis/mapping.py:")
                print(f"    CLEAN_FLATNESS = {best.clean_flat}")
                print(f"    HIGH_GAIN_FLATNESS = {best.high_gain_flat}")
                print(f"  accuracy {current.accuracy:.0%} -> {best.accuracy:.0%}")
            else:
                print(f"no improvement on the current {current.accuracy:.0%};"
                      " leave the thresholds alone")
            return 0
    except CorpusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
