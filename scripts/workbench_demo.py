"""Drive the workbench with a real convergence run, no amp required.

    ./scripts/py scripts/workbench_demo.py --target <guitar-stem.wav>

Stands in for the amp by filtering the target audio: each iteration applies the
suggested knob move as an actual EQ change through ffmpeg, re-measures, and
feeds the result back. The loop, the measurements and the convergence are all
real - only the amp is simulated, so the page can be seen working before the
hardware is available.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import time
from pathlib import Path

import _bootstrap  # noqa: F401  (puts src/ on sys.path)

from lt25_mcp.analysis.converge import Session, compare
from lt25_mcp.analysis.features import extract, log_spectrum
from lt25_mcp.dashboard.chain import describe_chain
from lt25_mcp.dashboard.server import Dashboard, LiveState
from lt25_mcp.preset import Preset

STAGES = ["load target", "measure target", "apply knobs", "capture", "measure amp",
          "compare", "next move"]


def eq(src: Path, dest: Path, bass: float, mid: float, treb: float) -> Path:
    """Apply knob positions as an actual EQ, so the loop has something to move."""
    gains = [(120, bass), (700, mid), (3500, treb)]
    chain = ",".join(
        f"equalizer=f={f}:width_type=o:width=1.6:g={(v - 5.0) * 2.2:.2f}"
        for f, v in gains
    )
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-af", chain, "-ac", "1", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--preset", type=Path,
                        default=Path("tests/fixtures/clean.json"))
    parser.add_argument("--iterations", type=int, default=6)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--pause", type=float, default=1.2,
                        help="seconds between iterations, so the page is watchable")
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="lt25-workbench-"))
    state = LiveState()
    board = Dashboard(state, port=args.port)
    url = board.start(open_browser=not args.no_browser)
    print(f"workbench: {url}")

    try:
        state.status = "running"
        state.target_name = args.target.name
        state.amp_label = "simulated amp (ffmpeg EQ)"
        board.set_stages(STAGES)

        with board.stage("load target", args.target.name):
            if not args.target.exists():
                raise FileNotFoundError(args.target)
            import json as _json
            preset = Preset.from_dict(_json.loads(args.preset.read_text()))
            state.preset_name = preset.display_name
            state.amp_label = preset.amp_label + "  ·  simulated"
            state.chain = describe_chain(preset)

        with board.stage("measure target"):
            target = extract(args.target)
            hz, target_spec = log_spectrum(args.target)
            state.spectrum_hz, state.spectrum_target = hz, target_spec
        board.note(f"target: centroid {target.spectral_centroid_hz:.0f} Hz, "
                   f"crest {target.crest_factor_db:.1f} dB")

        # Start deliberately wrong so there is something to converge from.
        knobs = {"bass": 8.5, "mid": 2.0, "treb": 8.5}
        session = Session(target=target)

        for step in range(1, args.iterations + 1):
            with board.stage("apply knobs", ", ".join(
                    f"{k} {v:.1f}" for k, v in knobs.items())):
                candidate = eq(args.target, work / f"iter{step}.wav", **knobs)
            state.knobs = dict(knobs)
            for name, value in knobs.items():
                if name in preset.params("amp"):
                    preset.set_knob(name, value)
            state.chain = describe_chain(preset)

            with board.stage("capture", f"iteration {step}"):
                time.sleep(0.05)  # a real capture is where the player plays

            with board.stage("measure amp"):
                current = extract(candidate)
                state.spectrum_current = log_spectrum(candidate)[1]

            with board.stage("compare"):
                result = compare(target, current)
                state.distance = result.distance
                state.converged = result.converged
                state.band_gaps = result.band_gaps

            with board.stage("next move"):
                state.moves = [
                    {"control": m.control, "delta": m.delta, "why": m.why}
                    for m in result.moves
                ]
                iteration = session.step(knobs, result)
                knobs = dict(iteration.knobs)
                state.iterations = [
                    {"index": i.index, "distance": i.distance, "reverted": i.reverted}
                    for i in session.history
                ]
            board.publish()

            trend = "" if session.improving is None else (
                " (closer)" if session.improving else " (further)")
            note = f"iteration {step}: distance {result.distance:.4f}{trend}"
            if iteration.reverted:
                note += f" - reverted to best, step scale {session.step_scale:.2f}"
            board.note(note)

            if result.converged:
                board.note("converged - stopping")
                break
            if session.exhausted:
                board.note("no longer improving - stopping at the best result")
                break
            knobs = session.apply(knobs, result)
            time.sleep(args.pause)

        state.status = "finished"
        best = session.best
        if best:
            board.note(f"best: iteration {best.index} at {best.distance:.4f}")
        board.publish()
        print("run complete - the page stays up; ctrl-c to stop")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        state.status = "stopped"
        board.publish()
        board.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
