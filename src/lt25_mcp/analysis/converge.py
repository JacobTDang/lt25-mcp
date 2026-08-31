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

# How much each measurement counts towards the distance score.
#
# These are weighted by how stable each measurement is across takes of the same
# performance, measured on a real amp: over three 20s windows of continuous
# playing with nothing changed, crest factor moved 5.9%, the harmonic ratio
# 9.1%, the centroid 16.3% - but the band ratios moved 30%, 55% and 99%.
#
# Band balance is still what a listener hears as "tone", so it cannot be
# dropped; it is down-weighted so a distance is not dominated by whichever
# notes happened to be played. See docs/measurements.md.
WEIGHTS = {"low": 0.5, "mid": 0.8, "high": 0.6, "centroid": 0.8}

# Knob movement per unit of measured gap, on the amp's 0-10 scale.
BAND_TO_KNOB = 8.0
CENTROID_TO_TREBLE = 1.5
CREST_TO_GAIN = 0.25

# Gaps smaller than these are inside the noise of playing the part twice, so
# acting on them chases the performance rather than the amp. Measured on a real
# amp rather than guessed: across 20s windows of the same continuous playing the
# low band moved by about 0.068 absolute, the mid by 0.095 and the high by 0.065.
#
# Per band, because they are not equally noisy - the low band is by far the
# worst, since playing higher up the neck empties it.
BAND_DEADBAND = {"low": 0.08, "mid": 0.10, "high": 0.07}

CENTROID_DEADBAND_OCTAVES = 0.25
CREST_DEADBAND_DB = 2.0

# The distance two takes of the same performance typically differ by, with
# nothing changed. Measured at 0.08 over 30s takes and 0.24 over 20s takes, so
# a shorter take is not worth running. A distance change smaller than this
# carries no information.
PLAYING_NOISE_FLOOR = 0.08
MIN_TAKE_SECONDS = 30.0

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
    noise_floor: float = PLAYING_NOISE_FLOOR

    @property
    def converged(self) -> bool:
        return self.distance <= CONVERGED

    @property
    def significant(self) -> bool:
        """Whether the gap is larger than the noise of playing the part twice.

        Below the floor the measurement cannot tell a tone difference from a
        different take, so any move would be chasing the performance.
        """
        return self.distance > self.noise_floor

    def describe(self) -> str:
        arrow = lambda v: "too much" if v > 0 else "too little"  # noqa: E731
        lines = [f"distance {self.distance:.3f}" + ("  CONVERGED" if self.converged else "")]
        for band, gap in self.band_gaps.items():
            if abs(gap) >= BAND_DEADBAND[band]:
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
        if not self.significant:
            lines.append(
                f"  NOTE: below the {self.noise_floor:.2f} noise floor of playing "
                "the part twice - this difference may be the performance, not the amp"
            )
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
        if abs(gap) < BAND_DEADBAND[band]:
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


# Consecutive iterations that fail to improve before the run gives up. Past
# this, the moves are not finding the target and more of them is noise.
MAX_REGRESSIONS = 3

# Every revert halves the step, so the loop closes in rather than oscillating.
REVERT_SCALE = 0.5


@dataclass
class Iteration:
    index: int
    distance: float
    knobs: dict[str, float]
    moves: list[Move]
    converged: bool
    reverted: bool = False
    """True when this iteration was worse than the best and was rolled back."""


@dataclass
class Session:
    """A convergence run, so progress can be seen rather than assumed."""

    target: ToneFeatures
    history: list[Iteration] = field(default_factory=list)
    step_scale: float = 1.0
    """Shrinks on every revert, so a loop that overshoots closes in."""
    regressions: int = 0

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

    @property
    def exhausted(self) -> bool:
        """Whether the run has stopped making progress and should stop."""
        return self.regressions >= MAX_REGRESSIONS

    def apply(self, knobs: dict[str, float], comparison: Comparison) -> dict[str, float]:
        """Apply an iteration's moves to knob positions, staying in range.

        Moves are scaled by `step_scale`, which halves each time the loop
        overshoots, so a run that goes past the target closes in on it instead
        of oscillating around it.
        """
        adjusted = dict(knobs)
        for move in comparison.moves:
            if move.control in adjusted:
                adjusted[move.control] = max(
                    0.0, min(10.0, adjusted[move.control] + move.delta * self.step_scale)
                )
        return adjusted

    def step(self, knobs: dict[str, float], comparison: Comparison) -> Iteration:
        """Record an iteration, rolling back if it made the tone worse.

        A move that increases distance is not a step towards the target, and
        applying the next move from that worse position compounds the mistake.
        The knobs revert to the best position seen and the step size halves.
        """
        best_before = self.best
        iteration = self.record(comparison, knobs)

        if best_before is not None and iteration.distance > best_before.distance:
            iteration.reverted = True
            iteration.knobs = dict(best_before.knobs)
            self.step_scale *= REVERT_SCALE
            self.regressions += 1
        else:
            self.regressions = 0
        return iteration
