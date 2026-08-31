"""What is actually plugged in, and how that changes a preset.

A preset is not a tone on its own. The same settings sound different through
different pickups, and a preset that loads an overdrive into the stomp slot is
wrong for a player who already has a real overdrive in front of the amp.

None of that is inferable from a recording of somebody else's guitar, so it is
declared once and applied afterwards. The adjustments are deliberately small:
they nudge a starting point, they do not overrule what was measured.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "lt25-mcp" / "rig.json"

PICKUP_TYPES = ("unknown", "single_coil", "humbucker", "p90", "active")

# Effect slots a pedal can make redundant. If the player already has the real
# thing in front of the amp, loading the modelled one as well stacks two of
# them, which is not what either was voiced for.
PEDAL_SLOTS = {
    "overdrive": "stomp",
    "distortion": "stomp",
    "fuzz": "stomp",
    "compressor": "stomp",
    "modulation": "mod",
    "delay": "delay",
    "reverb": "reverb",
}


class RigError(Exception):
    """Raised when a rig profile is not usable."""


@dataclass
class Rig:
    """The player's actual setup."""

    pickups: str = "unknown"
    guitar: str = ""
    pedals: list[str] = field(default_factory=list)
    """Pedal kinds in front of the amp, from PEDAL_SLOTS."""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.pickups not in PICKUP_TYPES:
            raise RigError(
                f"{self.pickups!r} is not a known pickup type; "
                f"choose one of: {', '.join(PICKUP_TYPES)}"
            )
        unknown = [p for p in self.pedals if p not in PEDAL_SLOTS]
        if unknown:
            raise RigError(
                f"unknown pedal kinds {unknown}; "
                f"choose from: {', '.join(sorted(PEDAL_SLOTS))}"
            )

    @property
    def occupied_slots(self) -> set[str]:
        """Amp effect slots the player's own pedals already cover."""
        return {PEDAL_SLOTS[p] for p in self.pedals}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Rig:
        allowed = {"pickups", "guitar", "pedals", "notes"}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def save(self, path: Path | None = None) -> Path:
        path = Path(path or DEFAULT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> Rig:
        """Load a saved rig, or a blank one if nothing has been declared."""
        path = Path(path or DEFAULT_PATH)
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))

    def describe(self) -> str:
        lines = [f"Pickups: {self.pickups.replace('_', ' ')}"]
        if self.guitar:
            lines.append(f"Guitar: {self.guitar}")
        lines.append(
            "Pedals in front: " + (", ".join(self.pedals) if self.pedals else "none")
        )
        if self.pedals:
            lines.append(
                "Amp slots left empty because a real pedal covers them: "
                + ", ".join(sorted(self.occupied_slots))
            )
        if self.notes:
            lines.append(f"Notes: {self.notes}")
        return "\n".join(lines)


# How a pickup type shifts a preset built from a recording of someone else's
# guitar. Humbuckers put out roughly twice the level of single-coils and are
# darker, so the same amp gain arrives hotter and duller.
#
# These are modest nudges on the amp's 0-10 scale, not a model of pickup
# physics, and they are applied only when the pickup type is actually known.
PICKUP_ADJUSTMENTS: dict[str, dict[str, float]] = {
    "single_coil": {"gain": +1.0, "treb": -0.5},
    "humbucker": {"gain": -1.0, "treb": +0.5},
    "p90": {"gain": -0.5, "treb": 0.0},
    "active": {"gain": -1.5, "treb": +0.5},
    "unknown": {},
}

PICKUP_RATIONALE = {
    "single_coil": (
        "Single-coils are lower output and brighter, so they need a little more "
        "gain to break up and a little less treble to stay civil"
    ),
    "humbucker": (
        "Humbuckers are roughly twice the output and darker, so the same setting "
        "arrives hotter and duller: back the gain off and open the treble up"
    ),
    "p90": "P90s sit between single-coils and humbuckers, closer to the latter in output",
    "active": (
        "Active pickups are hotter still and already EQ'd, so they need noticeably "
        "less amp gain"
    ),
    "unknown": (
        "Pickup type not declared, so no adjustment was made. Count the pickups on "
        "the guitar: two fat rectangles are humbuckers, three narrow ones are "
        "single-coils"
    ),
}


def adjust_for_rig(knobs: dict[str, float], rig: Rig) -> tuple[dict[str, float], list[str]]:
    """Nudge knob positions (0-10 scale) for the player's pickups.

    Returns the adjusted knobs and a note per change, so the reasoning is
    visible rather than silently baked in.
    """
    adjustments = PICKUP_ADJUSTMENTS.get(rig.pickups, {})
    adjusted = dict(knobs)
    explanations: list[str] = []
    for control, delta in adjustments.items():
        if control not in adjusted or delta == 0:
            continue
        before = adjusted[control]
        adjusted[control] = max(0.0, min(10.0, before + delta))
        if adjusted[control] != before:
            explanations.append(
                f"{control} {before:.1f} -> {adjusted[control]:.1f} for "
                f"{rig.pickups.replace('_', ' ')} pickups"
            )
    return adjusted, explanations


def slots_to_leave_empty(rig: Rig) -> dict[str, str]:
    """Amp slots that should stay empty because a real pedal covers them."""
    return {
        PEDAL_SLOTS[pedal]: f"you have a real {pedal} in front of the amp"
        for pedal in rig.pedals
    }
