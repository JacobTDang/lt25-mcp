"""Turn a video clip into a preset.

    ./scripts/py -m lt25_mcp.analysis.cli \
        --url "https://youtube.com/shorts/..." --start 3 --end 12 \
        --base tests/fixtures/clean.json --out tone.json

Emits a preset JSON file, a spectrogram, and the settings written out as knob
positions. It never touches the amp: writing is a separate, explicit step, so
that a bad analysis cannot reach hardware on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from lt25_mcp.analysis import acquire, features as features_mod, mapping, plots, stems
from lt25_mcp.preset import Preset


class PipelineError(Exception):
    """Raised when the pipeline cannot complete."""


@dataclass
class Result:
    preset: Preset
    features: features_mod.ToneFeatures
    choice: mapping.AmpChoice
    audio: Path
    stem: Path | None
    spectrogram: Path | None


def analyse(
    *,
    url: str | None = None,
    audio: Path | None = None,
    start: float | None = None,
    end: float | None = None,
    base: Preset,
    work_dir: Path,
    name: str | None = None,
    separate: bool = True,
    allow_fallback: bool = False,
    spectrogram: bool = True,
) -> Result:
    """Run the pipeline from a URL or a local file through to a preset."""
    if (url is None) == (audio is None):
        raise PipelineError("provide exactly one of url or audio")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    if url is not None:
        source = acquire.fetch_audio(url, work_dir / "source.wav")
    else:
        source = Path(audio)
        if not source.exists():
            raise PipelineError(f"no such audio file: {source}")

    if start is not None or end is not None:
        source = acquire.trim(source, start, end, work_dir / "clip.wav")

    stem = None
    if separate:
        stem = stems.isolate_guitar(
            source, work_dir / "stems", allow_fallback=allow_fallback
        )

    measured = features_mod.extract(stem or source)
    choice = mapping.choose_amp(measured)
    preset = mapping.build_preset(measured, base, name=name)

    image = None
    if spectrogram:
        image = plots.spectrogram(stem or source, work_dir / "spectrogram.png")

    return Result(
        preset=preset,
        features=measured,
        choice=choice,
        audio=source,
        stem=stem,
        spectrogram=image,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="video or audio link (YouTube, Shorts, Reels)")
    source.add_argument("--audio", type=Path, help="local audio file")
    parser.add_argument("--start", type=float, help="clip start in seconds")
    parser.add_argument("--end", type=float, help="clip end in seconds")
    parser.add_argument(
        "--base",
        type=Path,
        required=True,
        help="preset JSON to build from, e.g. one read off the amp",
    )
    parser.add_argument("--out", type=Path, default=Path("tone.json"))
    parser.add_argument("--work-dir", type=Path, default=Path(".analysis"))
    parser.add_argument("--name", help="preset display name, up to 16 characters")
    parser.add_argument(
        "--no-separate",
        action="store_true",
        help="skip guitar isolation and analyse the whole mix",
    )
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="if no guitar stem is produced, analyse the 'other' stem instead",
    )
    args = parser.parse_args(argv)

    try:
        base = Preset.from_dict(json.loads(args.base.read_text()))
        result = analyse(
            url=args.url,
            audio=args.audio,
            start=args.start,
            end=args.end,
            base=base,
            work_dir=args.work_dir,
            name=args.name,
            separate=not args.no_separate,
            allow_fallback=args.allow_fallback,
        )
    except (PipelineError, acquire.AcquisitionError, stems.StemError,
            features_mod.FeatureError, plots.PlotError, mapping.MappingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.out.write_text(json.dumps(result.preset.to_dict(), indent=2))

    print("measured:")
    print(result.features.describe())
    print()
    print(mapping.describe_settings(result.preset, choice=result.choice))
    print()
    print(f"preset written to {args.out}")
    if result.spectrogram:
        print(f"spectrogram  {result.spectrogram}")
    print()
    print("To hear it, audition it on the amp (nothing is saved):")
    print(f"  ./scripts/py scripts/audition.py {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
