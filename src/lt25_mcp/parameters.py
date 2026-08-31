"""What each amp parameter means, and what it will accept.

A preset's parameter dict is opaque on its own. `{"treb": 0.34, "sag": "match",
"cabsimType": "65twn"}` says nothing about which of those is a front-panel
knob, what range it runs over, or what happens musically when it moves. This
module supplies that, so a caller can tune deliberately instead of guessing.

Two scales are in play and confusing them is the easiest mistake to make:
presets store tone controls as `0.0..1.0`, while the amp's display and its
physical knobs read `0..10`. Use `to_display` and `from_display` at the
boundary rather than multiplying by ten by hand.

Enum vocabularies were observed across the 60 factory presets on a real
Mustang LT 25. Where only a single value has ever been seen, the parameter is
left unconstrained rather than locked to that one value - absence of evidence
for other values is not evidence they are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["continuous", "enum", "boolean"]
Scale = Literal["normalized", "decibels", "raw"]


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    kind: Kind
    display: str
    description: str
    scale: Scale = "raw"
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    panel: bool = False
    """True if the parameter has a physical knob on the amp's control panel."""

    def to_display(self, value):
        """Convert a stored value to what the amp's screen would show."""
        if self.scale == "normalized" and isinstance(value, (int, float)):
            return round(float(value) * 10.0, 1)
        return value

    def from_display(self, value):
        """Convert a screen value (0-10) back to what the preset stores."""
        if self.scale == "normalized" and isinstance(value, (int, float)):
            return float(value) / 10.0
        return value

    def describe(self) -> str:
        if self.kind == "enum" and self.choices:
            allowed = ", ".join(self.choices)
        elif self.kind == "boolean":
            allowed = "true or false"
        elif self.scale == "normalized":
            allowed = "0.0-1.0 stored, shown on the amp as 0-10"
        elif self.scale == "decibels":
            allowed = f"{self.minimum} to {self.maximum} dB"
        else:
            allowed = "any value the model already uses"
        panel = " (front-panel knob)" if self.panel else ""
        return f"{self.display}{panel}: {self.description}. Accepts {allowed}."


# Cabinet simulations observed across the factory presets.
CABSIM_TYPES = (
    "none", "1x12ss", "2x12c", "4x12frd", "4x12g", "4x12g2", "4x12m", "4x12m2",
    "4x12r", "4x12v", "4x12v2", "57champ", "57dlx", "59bman", "65dlx",
    "65prince", "65twn", "exlsr",
)

GATE_PRESETS = ("off", "low", "mid", "high", "super")

AMP_PARAMETERS: dict[str, ParameterSpec] = {
    "gain": ParameterSpec(
        "gain", "continuous", "Gain", scale="normalized", panel=True,
        description=(
            "How hard the preamp is driven. Low is clean, high is saturated and "
            "compressed. The single biggest lever on the character of a tone"
        ),
    ),
    "volume": ParameterSpec(
        "volume", "continuous", "Volume", scale="decibels",
        minimum=-30.0, maximum=0.0,
        description=(
            "Per-preset output level in dB, always negative. Use it to match "
            "loudness between presets, not to shape tone"
        ),
    ),
    "treb": ParameterSpec(
        "treb", "continuous", "Treble", scale="normalized", panel=True,
        description="High frequencies. Raise for presence and pick attack, lower to tame fizz",
    ),
    "mid": ParameterSpec(
        "mid", "continuous", "Middle", scale="normalized",
        description=(
            "Midrange around 400-1200 Hz. Raise to cut through a mix, scoop for "
            "modern metal. There is no physical knob for this - it is reachable "
            "only through the encoder or from here"
        ),
    ),
    "bass": ParameterSpec(
        "bass", "continuous", "Bass", scale="normalized", panel=True,
        description="Low end. Raise for thump, lower to tighten a high-gain tone",
    ),
    "presence": ParameterSpec(
        "presence", "continuous", "Presence", scale="normalized",
        description=(
            "Upper treble in the power amp, above where the Treble control sits. "
            "Adds air and bite. Not on the amp's front panel"
        ),
    ),
    "master": ParameterSpec(
        "master", "continuous", "Master", scale="normalized",
        description=(
            "Power-amp level within the model, distinct from the amp's physical "
            "MASTER knob. On models that have it, raising it adds power-amp "
            "compression and grit"
        ),
    ),
    "blend": ParameterSpec(
        "blend", "continuous", "Blend", scale="normalized",
        description="Mix between the modelled path and a cleaner one, on models that offer it",
    ),
    "bias": ParameterSpec(
        "bias", "continuous", "Bias", scale="normalized",
        description="Power-tube bias. Lower is colder and tighter, higher is looser and warmer",
    ),
    "sag": ParameterSpec(
        "sag", "enum", "Sag",
        description=(
            "Power-supply sag - how much the amp dips under a hard attack. Only "
            "'match' has been observed on this amp, so other values are untested"
        ),
    ),
    "cut": ParameterSpec(
        "cut", "continuous", "Cut", scale="normalized",
        description="High-frequency cut in the power amp, on models that have it",
    ),
    "gain2": ParameterSpec(
        "gain2", "continuous", "Gain 2", scale="normalized",
        description="Second gain stage on models with cascading preamps",
    ),
    "bright": ParameterSpec(
        "bright", "boolean", "Bright switch",
        description="Bright cap on the input. Adds high end, most audible at low gain",
    ),
    "cabsimType": ParameterSpec(
        "cabsimType", "enum", "Cabinet", choices=CABSIM_TYPES,
        description=(
            "Speaker cabinet simulation. Changes the tone more than any EQ "
            "control: '4x12' options are big and scooped, '65twn' and '65dlx' "
            "are open-backed Fender combos, 'none' bypasses it"
        ),
    ),
    "gatePreset": ParameterSpec(
        "gatePreset", "enum", "Noise gate", choices=GATE_PRESETS,
        description=(
            "Noise gate strength. 'off' for clean tones, 'low' to 'super' for "
            "progressively tighter gating on high-gain tones"
        ),
    ),
    "gateDetectorPosition": ParameterSpec(
        "gateDetectorPosition", "enum", "Gate detector",
        description=(
            "Where the noise gate listens. Only 'jack' has been observed on this "
            "amp, so other values are untested"
        ),
    ),
}

PANEL_ORDER = ("gain", "volume", "treb", "mid", "bass")
"""The controls a player thinks in, in the order the amp lays them out."""


def spec_for(name: str) -> ParameterSpec | None:
    return AMP_PARAMETERS.get(name)


def describe_parameters(present: dict) -> list[dict]:
    """Describe the parameters a particular amp model actually exposes.

    Parameter sets differ per model, so this is driven by what a preset
    contains rather than by the full catalogue.
    """
    described = []
    for name, value in present.items():
        spec = spec_for(name)
        if spec is None:
            described.append(
                {
                    "name": name,
                    "value": value,
                    "kind": "unknown",
                    "description": "Undocumented parameter; change it only by copying a value from another preset.",
                }
            )
            continue
        entry = {
            "name": name,
            "display_name": spec.display,
            "value": value,
            "kind": spec.kind,
            "scale": spec.scale,
            "front_panel": spec.panel,
            "description": spec.description,
        }
        if spec.scale == "normalized":
            entry["value_on_amp_scale"] = spec.to_display(value)
            entry["range_on_amp_scale"] = [0.0, 10.0]
        if spec.choices:
            entry["choices"] = list(spec.choices)
        described.append(entry)
    return described
