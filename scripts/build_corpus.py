"""Capture a labelled set of clips by playing through the amp's own presets.

    ./scripts/py scripts/build_corpus.py

The amp is the label. Fender chose the amp model and gain for each factory
preset, so a recording made through FENDER CLEAN is clean by construction and
one made through METAL LEAD is high gain. Same guitar, same room, same player -
the only thing that varies is the tone, which is exactly what the thresholds
are supposed to key on.

Each take waits until it can hear playing before it starts, so there is no
"begin now" to get wrong, and refuses a silent capture outright. Nine takes of
twenty seconds is about four minutes of playing.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import _bootstrap  # noqa: F401  (puts src/ on sys.path)

from lt25_mcp.analysis.capture import CaptureError, record, wait_for_playing
from lt25_mcp.analysis.corpus import Corpus
from lt25_mcp.commands import audition, exit_audition
from lt25_mcp.library import read_preset
from lt25_mcp.preset import Preset
from lt25_mcp.session import Session
from lt25_mcp.transport import TransportError, open_transport

# Chosen to span the range while staying unambiguous: the clean set is low gain
# through clean models, the high-gain set is a rectifier, an EVH and a cranked
# JCM800. Verified against the amp's own presets rather than picked by name.
PLAN = [
    (1, "clean", "FENDER CLEAN"),
    (17, "clean", "LITTLE CHAMP"),
    (6, "clean", "COUNTRY PICKING"),
    (4, "crunch", "CLASSIC ROCK"),
    (26, "crunch", "BLUES LEAD"),
    (3, "crunch", "CHICAGO BLUES"),
    (11, "high_gain", "METAL LEAD"),
    (14, "high_gain", "THRASH OVERKILL"),
    (22, "high_gain", "SUPER ROCK"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=20.0,
                        help="per take; classification reads crest factor and "
                             "harmonic ratio, which are stable at this length")
    parser.add_argument("--work-dir", type=Path,
                        default=Path.home() / ".config" / "lt25-mcp" / "corpus-audio")
    args = parser.parse_args(argv)
    args.work_dir.mkdir(parents=True, exist_ok=True)

    print(f"{len(PLAN)} takes of {args.seconds:.0f}s - about "
          f"{len(PLAN) * (args.seconds + 6) / 60:.0f} minutes of playing.\n")
    print("Play the SAME phrase through every preset. The amp will switch under")
    print("you; just keep going. Each take waits until it hears you, so there is")
    print("nothing to time.\n")

    corpus = Corpus.load()
    captured = 0
    try:
        with Session(open_transport()) as amp:
            try:
                for i, (slot, label, name) in enumerate(PLAN, 1):
                    preset = Preset.from_dict(read_preset(amp, slot))
                    audition(amp, preset)
                    print(f"[{i}/{len(PLAN)}] {name:16} {preset.amp_label:12} "
                          f"[{label}]", flush=True)
                    time.sleep(0.6)

                    try:
                        wait_for_playing(
                            timeout=90,
                            on_wait=lambda n: print("        waiting for you to play…",
                                                    flush=True) if n == 1 else None,
                        )
                    except CaptureError as exc:
                        print(f"        {exc}", file=sys.stderr)
                        return 1

                    dest = args.work_dir / f"slot{slot:02d}_{label}.wav"
                    record(dest, args.seconds, allow_short=True)
                    corpus.add(dest, label,
                               source=f"amp slot {slot} {name} ({preset.amp_label}), "
                                      "played live")
                    captured += 1
                    print(f"        captured", flush=True)
            finally:
                exit_audition(amp)
    except (TransportError, CaptureError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    finally:
        corpus.save()

    print(f"\ncaptured {captured} takes")
    print("corpus: " + ", ".join(f"{k} {v}" for k, v in corpus.counts().items()))
    if corpus.thin:
        print("still thin: " + ", ".join(corpus.thin))
    else:
        print("\nnext:  ./scripts/py scripts/calibrate.py sweep")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
