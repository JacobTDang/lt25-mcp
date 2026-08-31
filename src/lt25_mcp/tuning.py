"""Turning what a player says into which knob to move.

The gap this closes: an assistant can read a preset's numbers but has no idea
that "too fizzy" means drop the treble and the presence, or that "not heavy
enough" is mostly the cabinet and the midrange rather than more gain. Without
that, tuning degenerates into nudging every control at random and auditioning
until something sticks.

Each complaint maps to a small, ordered set of moves, on the amp's own 0-10
scale. They are starting points for one iteration, not a formula: apply one,
listen, and ask again.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Move:
    """One adjustment, expressed the way a player would describe it."""

    control: str
    delta: float
    """Change on the amp's 0-10 scale. Positive raises."""
    why: str


@dataclass(frozen=True)
class Remedy:
    complaint: str
    means: str
    moves: tuple[Move, ...]

    def describe(self) -> str:
        steps = "; ".join(
            f"{m.control} {m.delta:+.1f} ({m.why})" for m in self.moves
        )
        return f'"{self.complaint}" - {self.means}. Try: {steps}'


REMEDIES: tuple[Remedy, ...] = (
    Remedy(
        "too muddy", "low mids and bass are crowding the note definition",
        (
            Move("bass", -1.5, "clears the low end"),
            Move("mid", +0.5, "restores definition once the mud is gone"),
        ),
    ),
    Remedy(
        "too boomy", "the low end is overwhelming everything else",
        (Move("bass", -2.0, "the usual culprit"),),
    ),
    Remedy(
        "too thin", "not enough body behind the note",
        (
            Move("bass", +1.5, "adds weight"),
            Move("mid", +1.0, "adds body without mud"),
        ),
    ),
    Remedy(
        "too fizzy", "high-gain hiss sitting on top of the note",
        (
            Move("treb", -1.5, "cuts the fizz directly"),
            Move("presence", -1.0, "fizz usually lives above the treble control"),
            Move("gain", -0.5, "less drive means less hiss"),
        ),
    ),
    Remedy(
        "too harsh", "an ice-pick in the upper mids and treble",
        (
            Move("treb", -1.5, "softens the top"),
            Move("presence", -1.5, "where harshness usually lives"),
        ),
    ),
    Remedy(
        "too dark", "the tone is buried and lacks air",
        (
            Move("treb", +1.5, "opens the top"),
            Move("presence", +1.0, "adds air above the treble band"),
        ),
    ),
    Remedy(
        "no bite", "the attack does not cut through",
        (
            Move("presence", +1.5, "sharpens the leading edge"),
            Move("treb", +1.0, "adds pick definition"),
        ),
    ),
    Remedy(
        "too boxy", "a honky midrange peak, like playing into a box",
        (Move("mid", -2.0, "scoops the offending peak"),),
    ),
    Remedy(
        "gets lost in a mix", "scooped mids disappear behind everything else",
        (Move("mid", +2.0, "midrange is what cuts through a band"),),
    ),
    Remedy(
        "not heavy enough", "heaviness is cabinet and midrange, not just gain",
        (
            Move("mid", -1.0, "a modern scoop"),
            Move("bass", +1.0, "weight underneath"),
            Move("gain", +0.5, "a little more saturation, last not first"),
        ),
    ),
    Remedy(
        "too compressed", "the picking dynamics have been squashed out",
        (Move("gain", -1.5, "gain is what removes dynamics"),),
    ),
    Remedy(
        "not enough grit", "wants more breakup without losing the note",
        (Move("gain", +1.5, "more preamp drive"),),
    ),
    Remedy(
        "too loud", "level, not tone",
        (Move("volume", -2.0, "per-preset output, leaves the tone alone"),),
    ),
    Remedy(
        "too quiet", "level, not tone",
        (Move("volume", +2.0, "per-preset output, leaves the tone alone"),),
    ),
)

REMEDY_INDEX = {r.complaint: r for r in REMEDIES}


# Adjustments that need a different control entirely, not a knob nudge.
STRUCTURAL_ADVICE = (
    "If the tone is close but the *character* is wrong - a Fender combo where "
    "you wanted a British stack - change the amp model rather than fighting it "
    "with EQ.",
    "The cabinet simulation moves a tone further than any tone control. A "
    "4x12 makes anything bigger and more scooped; '65twn' and '65dlx' are open "
    "and airy; 'none' bypasses it entirely.",
    "Hiss between notes on a high-gain tone is the noise gate, not the EQ: "
    "raise gatePreset from 'off' towards 'super'.",
    "Ambience belongs in the reverb and delay slots, not in the amp EQ. If a "
    "tone sounds dry compared with a record, add reverb before touching tone "
    "controls.",
    "Dirt can come from the stomp slot instead of the amp's gain. An overdrive "
    "in front of a cleaner amp model keeps the note definition that cranking "
    "the amp's own gain would smear.",
)


def remedy_for(complaint: str) -> Remedy | None:
    """Look up a complaint, tolerating loose phrasing."""
    text = complaint.strip().lower()
    if text in REMEDY_INDEX:
        return REMEDY_INDEX[text]
    for key, remedy in REMEDY_INDEX.items():
        if key in text or text in key:
            return remedy
    return None


def catalogue() -> list[dict]:
    """Every remedy, for an assistant to consult before choosing a move."""
    return [
        {
            "complaint": r.complaint,
            "means": r.means,
            "moves": [
                {"control": m.control, "delta_on_0_to_10_scale": m.delta, "why": m.why}
                for m in r.moves
            ],
        }
        for r in REMEDIES
    ]
