"""One playing session that answers both open hardware questions.

    ./scripts/py scripts/session.py --name "squier strat"

Loads the linear reference preset, records several takes of the same phrase,
and uses them twice:

  * take 1 becomes the guitar's profile, so presets can adapt to this
    instrument instead of to an assumed pickup type
  * every take is compared against every other, with nothing changed between
    them, which measures how much of a convergence iteration is the playing
    rather than the amp

Nothing is written to the amp. The reference preset is auditioned, which the
amp forgets the moment the session ends.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

import _bootstrap  # noqa: F401  (puts src/ on sys.path)

from lt25_mcp.analysis.capture import CaptureError, record
from lt25_mcp.analysis.converge import CONVERGED, compare
from lt25_mcp.analysis.features import FeatureError, extract
from lt25_mcp.commands import audition, exit_audition
from lt25_mcp.guitar import (
    CALIBRATION_INSTRUCTIONS,
    REFERENCE_KNOBS,
    REFERENCE_PRESET_AMP,
    GuitarLibrary,
    profile_from_capture,
)
from lt25_mcp.preset import Preset
from lt25_mcp.rig import Rig
from lt25_mcp.session import Session
from lt25_mcp.transport import TransportError, open_transport

VERDICTS = (
    (0.05, "GOOD",
     "playing variance sits well below the moves the loop makes (0.1-0.3), so "
     "automatic convergence is measuring the amp"),
    (0.10, "MARGINAL",
     "comparable to a small move. Use longer takes and a fixed phrase, and "
     "treat a single iteration's change as noise"),
    (float("inf"), "TOO NOISY",
     "the loop would be measuring the player rather than the amp. Do not run it "
     "automatically - use it to show the gap and let the player choose"),
)


def reference_preset(base: Preset) -> Preset:
    """The fixed, near-linear preset every calibration take goes through."""
    preset = base.clone()
    preset.amp_model = REFERENCE_PRESET_AMP
    for knob, value in REFERENCE_KNOBS.items():
        if knob in preset.params("amp"):
            preset.set_knob(knob, value)
    preset.display_name = "CALIBRATION"
    return preset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="a name for this guitar")
    parser.add_argument("--takes", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="per take; below 30s the measurement is mostly playing")
    parser.add_argument("--base", type=Path, default=Path("tests/fixtures/clean.json"))
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--pickups", default="unknown")
    args = parser.parse_args(argv)

    if args.takes < 2:
        print("error: need at least two takes to measure variance", file=sys.stderr)
        return 1

    work = args.work_dir or Path(tempfile.mkdtemp(prefix="lt25-session-"))
    work.mkdir(parents=True, exist_ok=True)
    preset = reference_preset(Preset.from_dict(json.loads(args.base.read_text())))

    print(f"reference preset: {preset.amp_label} at "
          + ", ".join(f"{k} {v:.0f}" for k, v in preset.knobs().items()))
    print()
    print(CALIBRATION_INSTRUCTIONS)
    print()
    print("Play the SAME phrase for every take. Change nothing between them -")
    print("not the amp, not the guitar's volume or tone, not your picking hand.")
    print()

    takes: list[Path] = []
    try:
        with Session(open_transport()) as amp:
            audition(amp, preset)
            print("reference preset is now playing through the amp\n")
            try:
                for i in range(1, args.takes + 1):
                    input(f"take {i} of {args.takes} - press enter, then play "
                          f"for {args.seconds:.0f}s: ")
                    takes.append(record(work / f"take{i}.wav", args.seconds))
                    print(f"  captured take {i}")
            finally:
                exit_audition(amp)
                print("\naudition ended, amp restored")
    except (TransportError, CaptureError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nstopped", file=sys.stderr)
        return 1

    try:
        measured = [extract(p) for p in takes]
    except FeatureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # ---- what the guitar measures like -----------------------------------
    profile = profile_from_capture(args.name, takes[0], pickups=args.pickups)
    library = GuitarLibrary.load()
    library.add(profile)
    library.save()
    rig = Rig.load()
    rig.playing = args.name
    rig.save()

    print("\n" + "=" * 62)
    print("GUITAR PROFILE")
    print(profile.describe())
    if profile.is_reference:
        print("  reference guitar")
    else:
        against = library.reference.name if library.reference else "?"
        print(f"  compared against {against}")

    # ---- how much of a take is the playing -------------------------------
    print("\n" + "=" * 62)
    print("PLAYING VARIANCE  (nothing changed between takes)")
    distances = []
    for i in range(len(measured)):
        for j in range(i + 1, len(measured)):
            d = compare(measured[i], measured[j]).distance
            distances.append(d)
            print(f"  take {i + 1} vs take {j + 1}:  {d:.4f}")

    worst, median = max(distances), statistics.median(distances)
    print(f"\n  median {median:.4f}   worst {worst:.4f}")
    print(f"  (convergence threshold is {CONVERGED}; a suggested move shifts "
          "distance by 0.1-0.3)")
    for threshold, verdict, advice in VERDICTS:
        if worst < threshold:
            print(f"\n  {verdict}: {advice}")
            break

    print(f"\ntakes kept in {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
