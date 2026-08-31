"""Turning measurements into a preset.

This is the weakest link in the pipeline and it is worth being honest about
why. Spectral features of a mixed, mastered, lossily-encoded recording reflect
the mix engineer and the codec as much as they reflect the amp. A rule table
over eighteen amp models cannot recover a signal chain from a spectrum.

So this aims for a defensible starting point, not a match. The intended use is
to audition the result, listen, and adjust - which is why `describe_settings`
exists: if the automatic choice is wrong, the numbers are still a better
starting point than the middle of every knob.
"""

from __future__ import annotations

from lt25_mcp.analysis.features import ToneFeatures
from lt25_mcp.dsp_catalog import PASSTHRU
from lt25_mcp.preset import DISPLAY_NAME_LENGTH, Preset, PresetError

# Crest factor splits saturation levels. A clean guitar keeps its transients
# and so stays dynamic; distortion compresses the signal towards a square
# wave. For reference, a pure sine measures 3.0 dB and a square wave 0.0 dB.
#
# THESE THRESHOLDS ARE NOT CALIBRATED AGAINST REAL RECORDINGS. They were set
# from synthesized signals and sanity-checked by ear on nothing at all yet.
# Expect to move them once real clips have been through the pipeline.
CLEAN_CREST_DB = 9.5
HIGH_GAIN_CREST_DB = 4.5

# Above this share of harmonic energy the signal is holding together as pitched
# notes rather than dissolving into noise.
CLEAN_HARMONIC_RATIO = 0.78

# Where 400-800 Hz stops being scooped and starts being forward.
SCOOPED_MID = 0.18
FORWARD_MID = 0.38

BRIGHT_CENTROID_HZ = 2200.0
DARK_CENTROID_HZ = 1200.0

# Below this the source is effectively dry.
DRY_DECAY_S = 0.6
ROOM_DECAY_S = 1.6


class MappingError(Exception):
    """Raised when features cannot be turned into a preset."""


def gain_character(features: ToneFeatures) -> str:
    """One of 'clean', 'crunch', 'high_gain'."""
    if (
        features.crest_factor_db >= CLEAN_CREST_DB
        and features.harmonic_ratio >= CLEAN_HARMONIC_RATIO
    ):
        return "clean"
    if features.crest_factor_db < HIGH_GAIN_CREST_DB:
        return "high_gain"
    return "crunch"


def choose_amp_model(features: ToneFeatures) -> str:
    """Pick the amp model whose character best matches the measurements."""
    character = gain_character(features)
    centroid = features.spectral_centroid_hz
    mid = features.mid_energy_ratio

    if character == "clean":
        if centroid >= BRIGHT_CENTROID_HZ:
            return "DUBS_Twin65"
        if centroid <= DARK_CENTROID_HZ:
            return "DUBS_Princeton65"
        return "DUBS_Deluxe65"

    if character == "high_gain":
        if mid >= FORWARD_MID:
            return "DUBS_Jcm800"
        if mid <= SCOOPED_MID:
            return "DUBS_MetalRect2" if centroid >= BRIGHT_CENTROID_HZ else "DUBS_Rect2"
        return "DUBS_MetalEvh3"

    if mid >= FORWARD_MID:
        return "DUBS_Plexi87"
    if features.low_energy_ratio >= 0.30:
        return "DUBS_Bassman59"
    return "DUBS_Deluxe57"


def choose_reverb(features: ToneFeatures) -> str:
    """Reverb sized from how long the source rings out."""
    if features.decay_time_s < DRY_DECAY_S:
        return PASSTHRU
    if features.decay_time_s >= ROOM_DECAY_S:
        return "DUBS_LargeHallReverb"
    return "DUBS_SmallRoomReverb"


def _tone_controls(features: ToneFeatures) -> dict[str, float]:
    """Map measured band ratios onto the amp's 0..1 tone controls.

    Band ratios are small numbers; these scale factors spread a realistic
    range of inputs across most of each knob's travel rather than bunching
    everything around the middle.
    """
    character = gain_character(features)
    gain = {"clean": 0.30, "crunch": 0.55, "high_gain": 0.82}[character]
    # Nudge by how compressed the source actually is within its bucket.
    gain += max(-0.12, min(0.12, (10.0 - features.crest_factor_db) * 0.015))

    return {
        "gain": _clamp(gain),
        "bass": _clamp(features.low_energy_ratio * 2.2),
        "mid": _clamp(features.mid_energy_ratio * 1.9),
        "treb": _clamp(features.high_energy_ratio * 2.6),
    }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def build_preset(
    features: ToneFeatures, base: Preset, *, name: str | None = None
) -> Preset:
    """Clone `base` and shape it towards the measured tone.

    Always starts from a real preset read off the amp: parameter sets differ
    per model and inventing one produces something the amp will not accept.
    """
    preset = base.clone()
    try:
        preset.node("amp")
    except PresetError as exc:
        raise MappingError(f"base preset has no amp node: {exc}") from exc

    preset.amp_model = choose_amp_model(features)
    for knob, value in _tone_controls(features).items():
        if knob in preset.params("amp"):
            preset.set_param("amp", knob, value)

    reverb = choose_reverb(features)
    preset.set_effect("reverb", reverb)

    if name is not None:
        preset.display_name = name[:DISPLAY_NAME_LENGTH]
    return preset


def describe_settings(preset: Preset) -> str:
    """The preset as knob positions on the amp's own 0-10 scale.

    Useful on its own: if the automatic choice is close but not right, this is
    what you dial in by hand.
    """
    params = preset.params("amp")
    labels = [("gain", "Gain"), ("treb", "Treble"), ("mid", "Middle"), ("bass", "Bass")]
    lines = [f"Amp model: {preset.amp_label}"]
    for key, label in labels:
        if key in params:
            value = params[key]
            shown = f"{value * 10:.1f}/10" if 0.0 <= value <= 1.0 else f"{value:.2f}"
            lines.append(f"  {label:8} {shown}")
    from lt25_mcp.dsp_catalog import EFFECT_NODES, effect_label

    for node in EFFECT_NODES:
        if preset.has_effect(node):
            lines.append(f"  {node.capitalize():8} {effect_label(node, preset.unit(node))}")
    return "\n".join(lines)
