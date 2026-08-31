"""Measuring whether the pipeline gives the same answer twice.

The mapping rules cannot be checked for correctness without labelled ground
truth, but they can be checked for *stability*, which needs no labels at all.
A clip that is the same performance at a different volume, or trimmed to a
different section, or re-encoded, should produce the same amp model and
similar knob positions. Where it does not, the pipeline is reading noise.

That is not a proxy for accuracy - a consistently wrong answer scores
perfectly here. It catches the other failure: an answer that changes when
nothing meaningful about the tone did. The 5-30s versus 5-to-end discrepancy
that produced two different reverbs on identical audio was exactly this, and
it was found by accident rather than by measurement.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from lt25_mcp.analysis.features import ToneFeatures, extract
from lt25_mcp.analysis.mapping import (
    AmpChoice,
    choose_amp,
    choose_reverb,
    gain_character,
)

KNOBS = ("gain", "treb", "mid", "bass")

# A knob that moves by more than this across perturbations of the same audio
# is being driven by noise. Expressed on the amp's own 0-10 scale.
KNOB_TOLERANCE = 1.0


@dataclass(frozen=True)
class VariantResult:
    name: str
    character: str
    amp_model: str
    confidence: float
    reverb: str | None
    knobs: dict[str, float]
    features: ToneFeatures


@dataclass
class StabilityReport:
    baseline: VariantResult
    variants: list[VariantResult] = field(default_factory=list)

    @property
    def amp_agreement(self) -> float:
        """Fraction of variants choosing the baseline's amp model."""
        if not self.variants:
            return 1.0
        agree = sum(v.amp_model == self.baseline.amp_model for v in self.variants)
        return agree / len(self.variants)

    @property
    def character_agreement(self) -> float:
        if not self.variants:
            return 1.0
        agree = sum(v.character == self.baseline.character for v in self.variants)
        return agree / len(self.variants)

    @property
    def reverb_agreement(self) -> float:
        if not self.variants:
            return 1.0
        agree = sum(v.reverb == self.baseline.reverb for v in self.variants)
        return agree / len(self.variants)

    @property
    def knob_spread(self) -> dict[str, float]:
        """Widest disagreement per knob, on the amp's 0-10 scale."""
        spread = {}
        for knob in KNOBS:
            values = [self.baseline.knobs[knob]] + [v.knobs[knob] for v in self.variants]
            spread[knob] = (max(values) - min(values)) * 10.0
        return spread

    @property
    def worst_knob(self) -> tuple[str, float]:
        spread = self.knob_spread
        name = max(spread, key=spread.get)
        return name, spread[name]

    @property
    def is_stable(self) -> bool:
        return (
            self.amp_agreement == 1.0
            and self.reverb_agreement == 1.0
            and self.worst_knob[1] <= KNOB_TOLERANCE
        )

    def describe(self) -> str:
        lines = [
            f"baseline: {self.baseline.amp_model} ({self.baseline.character}), "
            f"reverb {self.baseline.reverb or 'inherited'}",
            "",
            f"{'variant':16} {'character':10} {'amp':18} {'conf':>5}  reverb",
        ]
        for v in [self.baseline, *self.variants]:
            flag = " " if v.amp_model == self.baseline.amp_model else "!"
            lines.append(
                f"{flag}{v.name:15} {v.character:10} {v.amp_model:18} "
                f"{v.confidence:5.0%}  {v.reverb or 'inherited'}"
            )
        lines += [
            "",
            f"amp model agreement   {self.amp_agreement:.0%}",
            f"character agreement   {self.character_agreement:.0%}",
            f"reverb agreement      {self.reverb_agreement:.0%}",
            "knob spread (0-10):   "
            + "  ".join(f"{k} {v:.1f}" for k, v in self.knob_spread.items()),
            "",
            "STABLE" if self.is_stable else "UNSTABLE - the answer depends on the encoding",
        ]
        return "\n".join(lines)


def _measure(name: str, path: Path) -> VariantResult:
    from lt25_mcp.analysis.mapping import _tone_controls

    features = extract(path)
    choice: AmpChoice = choose_amp(features)
    return VariantResult(
        name=name,
        character=gain_character(features),
        amp_model=choice.amp_model,
        confidence=choice.confidence,
        reverb=choose_reverb(features),
        knobs=_tone_controls(features),
        features=features,
    )


def perturb(path: Path, out_dir: Path) -> dict[str, Path]:
    """Write variants of `path` that should not change the verdict.

    Level changes, section changes and a lower sample rate all preserve the
    tone being played. If any of them changes the chosen amp, the rules are
    keying on something other than the tone.
    """
    import librosa
    import numpy as np
    import soundfile as sf

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    y, sr = librosa.load(str(path), sr=None, mono=True)

    variants: dict[str, np.ndarray] = {
        "quiet -12dB": y * 10 ** (-12 / 20),
        "loud +6dB": y * 10 ** (6 / 20),
        "first half": y[: len(y) // 2],
        "second half": y[len(y) // 2 :],
        "middle 60%": y[int(len(y) * 0.2) : int(len(y) * 0.8)],
    }

    written = {}
    for name, signal in variants.items():
        dest = out_dir / f"{name.replace(' ', '_').replace('%', 'pct')}.wav"
        # Float subtype, not the 16-bit PCM default: a boosted variant would
        # otherwise clip, which changes the crest factor and so the verdict.
        # That would test clipping, not level invariance.
        sf.write(dest, signal.astype(np.float32), sr, subtype="FLOAT")
        written[name] = dest

    if sr > 16000:
        dest = out_dir / "resampled_16k.wav"
        sf.write(
            dest,
            librosa.resample(y, orig_sr=sr, target_sr=16000),
            16000,
            subtype="FLOAT",
        )
        written["16 kHz"] = dest
    return written


def assess(path: Path, work_dir: Path) -> StabilityReport:
    """Run the pipeline over perturbations of `path` and report agreement."""
    path = Path(path)
    baseline = _measure("baseline", path)
    variants = [_measure(name, p) for name, p in perturb(path, work_dir).items()]
    return StabilityReport(baseline=baseline, variants=variants)


def knob_variance(report: StabilityReport) -> dict[str, float]:
    """Standard deviation per knob across all variants, on the 0-10 scale."""
    out = {}
    for knob in KNOBS:
        values = [report.baseline.knobs[knob]] + [v.knobs[knob] for v in report.variants]
        out[knob] = statistics.pstdev(values) * 10.0 if len(values) > 1 else 0.0
    return out
