"""Closing the gap between a target tone and what the amp is actually making.

Analysis on its own is a single guess: measure a record, emit a preset, hope.
This is the other half. Record the player through a candidate preset, measure
that the same way the target was measured, and derive the next move from the
difference rather than from a rule table.

Only comparable things are compared. Band ratios are normalised against each
other and survive mastering reasonably, so they carry most of the signal.
Spectral centroid is compared as a ratio in octaves. Absolute level is *not*
compared: a mastered recording has been normalised and says nothing about how
hard anyone's pickups drive an amp. Crest factor is compared weakly and only
to nudge gain, because mastering compression flatters it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from lt25_mcp.analysis.features import ToneFeatures

# How much each measurement counts towards the distance score. Band balance is
# what a listener hears as "tone"; centroid is a summary of the same thing and
# is weighted lower to avoid counting it twice.
WEIGHTS = {"low": 1.0, "mid": 1.0, "high": 1.0, "centroid": 0.5}

# Knob movement per unit of measured gap, on the amp's 0-10 scale.
BAND_TO_KNOB = 8.0
CENTROID_TO_TREBLE = 1.5
CREST_TO_GAIN = 0.25

# Gaps smaller than this are within measurement noise; leave them alone.
BAND_DEADBAND = 0.03
CENTROID_DEADBAND_OCTAVES = 0.15
CREST_DEADBAND_DB = 2.0

# No single iteration moves a knob further than this.
MAX_STEP = 1.5

# Below this distance the tones are close enough that further automated moves
# are guessing at noise.
CONVERGED = 0.08


@dataclass(frozen=True)
class Move:
    control: str
    delta: float
    why: str


@dataclass
class Comparison:
    """How far the current tone sits from the target, and what to do about it."""

    distance: float
    band_gaps: dict[str, float]
    """current minus target, per band. Positive means the amp has too much."""

    centroid_octaves: float
    """How many octaves brighter the current tone is than the target."""

    crest_gap_db: float
    moves: list[Move] = field(default_factory=list)

    @property
    def converged(self) -> bool:
        return self.distance <= CONVERGED

    def describe(self) -> str:
        arrow = lambda v: "too much" if v > 0 else "too little"  # noqa: E731
        lines = [f"distance {self.distance:.3f}" + ("  CONVERGED" if self.converged else "")]
        for band, gap in self.band_gaps.items():
            if abs(gap) >= BAND_DEADBAND:
                lines.append(f"  {band:5} {gap:+.3f}  ({arrow(gap)})")
        if abs(self.centroid_octaves) >= CENTROID_DEADBAND_OCTAVES:
            direction = "brighter" if self.centroid_octaves > 0 else "darker"
            lines.append(f"  overall {abs(self.centroid_octaves):.2f} octaves {direction} than target")
        if self.moves:
            lines.append("  next:")
            for move in self.moves:
                lines.append(f"    {move.control} {move.delta:+.1f}  {move.why}")
        elif not self.converged:
            lines.append("  no move suggested: every gap is inside the deadband")
        return "\n".join(lines)


def compare(target: ToneFeatures, current: ToneFeatures) -> Comparison:
    """Measure the gap between a target tone and what the amp is producing."""
    band_gaps = {
        "low": current.low_energy_ratio - target.low_energy_ratio,
        "mid": current.mid_energy_ratio - target.mid_energy_ratio,
        "high": current.high_energy_ratio - target.high_energy_ratio,
    }

    if target.spectral_centroid_hz > 0 and current.spectral_centroid_hz > 0:
        centroid_octaves = math.log2(
            current.spectral_centroid_hz / target.spectral_centroid_hz
        )
    else:
        centroid_octaves = 0.0

    crest_gap = current.crest_factor_db - target.crest_factor_db

    distance = math.sqrt(
        sum(WEIGHTS[b] * gap**2 for b, gap in band_gaps.items())
        + WEIGHTS["centroid"] * (centroid_octaves / 3.0) ** 2
    )

    return Comparison(
        distance=distance,
        band_gaps=band_gaps,
        centroid_octaves=centroid_octaves,
        crest_gap_db=crest_gap,
        moves=_moves_for(band_gaps, centroid_octaves, crest_gap),
    )


def _moves_for(
    band_gaps: dict[str, float], centroid_octaves: float, crest_gap: float
) -> list[Move]:
    """Translate measured gaps into knob movements.

    One move per control, largest gap first, so an iteration changes a few
    things deliberately rather than everything at once.
    """
    moves: list[Move] = []
    band_to_knob = {"low": "bass", "mid": "mid", "high": "treb"}

    for band, gap in sorted(band_gaps.items(), key=lambda kv: -abs(kv[1])):
        if abs(gap) < BAND_DEADBAND:
            continue
        delta = _clamp(-gap * BAND_TO_KNOB)
        if abs(delta) < 0.1:
            continue
        direction = "too much" if gap > 0 else "not enough"
        moves.append(
            Move(
                band_to_knob[band],
                delta,
                f"{direction} {band} versus the target ({gap:+.3f} of the balance)",
            )
        )

    if abs(centroid_octaves) >= CENTROID_DEADBAND_OCTAVES and not any(
        m.control == "treb" for m in moves
    ):
        delta = _clamp(-centroid_octaves * CENTROID_TO_TREBLE)
        if abs(delta) >= 0.1:
            moves.append(
                Move(
                    "treb",
                    delta,
                    f"overall tone is {abs(centroid_octaves):.2f} octaves "
                    f"{'brighter' if centroid_octaves > 0 else 'darker'} than the target",
                )
            )

    if abs(crest_gap) >= CREST_DEADBAND_DB:
        delta = _clamp(crest_gap * CREST_TO_GAIN)
        if abs(delta) >= 0.1:
            moves.append(
                Move(
                    "gain",
                    delta,
                    f"the amp is {abs(crest_gap):.1f} dB "
                    f"{'more dynamic' if crest_gap > 0 else 'more compressed'} "
                    "than the target",
                )
            )
    return moves


def _clamp(delta: float) -> float:
    return max(-MAX_STEP, min(MAX_STEP, delta))


@dataclass
class Iteration:
    index: int
    distance: float
    knobs: dict[str, float]
    moves: list[Move]
    converged: bool


@dataclass
class Session:
    """A convergence run, so progress can be seen rather than assumed."""

    target: ToneFeatures
    history: list[Iteration] = field(default_factory=list)

    def record(self, comparison: Comparison, knobs: dict[str, float]) -> Iteration:
        iteration = Iteration(
            index=len(self.history) + 1,
            distance=comparison.distance,
            knobs=dict(knobs),
            moves=list(comparison.moves),
            converged=comparison.converged,
        )
        self.history.append(iteration)
        return iteration

    @property
    def improving(self) -> bool | None:
        """Whether the last iteration got closer. None until there are two."""
        if len(self.history) < 2:
            return None
        return self.history[-1].distance < self.history[-2].distance

    @property
    def best(self) -> Iteration | None:
        return min(self.history, key=lambda i: i.distance) if self.history else None

    def apply(self, knobs: dict[str, float], comparison: Comparison) -> dict[str, float]:
        """Apply an iteration's moves to knob positions, staying in range."""
        adjusted = dict(knobs)
        for move in comparison.moves:
            if move.control in adjusted:
                adjusted[move.control] = max(
                    0.0, min(10.0, adjusted[move.control] + move.delta)
                )
        return adjusted
