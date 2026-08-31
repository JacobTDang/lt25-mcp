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

from dataclasses import dataclass, field

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

# A roughly typical electric guitar balance across the three bands.
REFERENCE_LOW = 0.25
REFERENCE_MID = 0.45
REFERENCE_HIGH = 0.30

BAND_TO_KNOB = 1.0

# Never drive a tone control to either stop on an automatic guess.
KNOB_MIN = 0.15
KNOB_MAX = 0.90

BRIGHT_CENTROID_HZ = 2200.0
DARK_CENTROID_HZ = 1200.0

# Below this the source is effectively dry.
DRY_DECAY_S = 0.6
ROOM_DECAY_S = 1.6

# If the measured decay reaches this fraction of the clip, the signal never
# actually decayed and the number means nothing.
INCONCLUSIVE_DECAY_FRACTION = 0.95


# How far from a decision boundary a measurement must sit before the choice
# counts as confident. Crest factor spans roughly 0-20 dB across clean to
# heavily saturated, so 3 dB is a meaningful margin.
CONFIDENT_MARGIN_DB = 3.0

# Below this, offer the neighbouring character's amp as an alternative.
CONFIDENT = 0.6


class MappingError(Exception):
    """Raised when features cannot be turned into a preset."""


@dataclass(frozen=True)
class AmpChoice:
    """An amp model choice, with how much to trust it.

    The rule table is uncalibrated against real recordings, so a bare answer
    would be false precision. Confidence reflects how far the measurement sits
    from the nearest decision boundary; `alternatives` names what it would have
    chosen just the other side of that boundary, which is what to audition next
    if the first answer is wrong.
    """

    amp_model: str
    confidence: float
    reason: str
    alternatives: list[str] = field(default_factory=list)


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


def _character_confidence(features: ToneFeatures) -> float:
    """How far the crest factor sits from the nearest gain boundary, 0..1."""
    margin = min(
        abs(features.crest_factor_db - CLEAN_CREST_DB),
        abs(features.crest_factor_db - HIGH_GAIN_CREST_DB),
    )
    return _clamp(margin / CONFIDENT_MARGIN_DB)


def _amp_for_character(character: str, features: ToneFeatures) -> str:
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


def _neighbouring_characters(character: str) -> list[str]:
    return {
        "clean": ["crunch"],
        "crunch": ["clean", "high_gain"],
        "high_gain": ["crunch"],
    }[character]


def choose_amp(features: ToneFeatures) -> AmpChoice:
    """Pick an amp model, and say how much to trust the pick."""
    character = gain_character(features)
    primary = _amp_for_character(character, features)
    confidence = _character_confidence(features)
    reason = (
        f"crest {features.crest_factor_db:.1f} dB and harmonic ratio "
        f"{features.harmonic_ratio:.2f} read as {character.replace('_', ' ')}; "
        f"centroid {features.spectral_centroid_hz:.0f} Hz and "
        f"{features.mid_energy_ratio:.0%} midrange chose the model"
    )
    alternatives: list[str] = []
    if confidence < CONFIDENT:
        for neighbour in _neighbouring_characters(character):
            candidate = _amp_for_character(neighbour, features)
            if candidate != primary and candidate not in alternatives:
                alternatives.append(candidate)
    return AmpChoice(primary, confidence, reason, alternatives)


def choose_amp_model(features: ToneFeatures) -> str:
    """Pick the amp model whose character best matches the measurements."""
    return choose_amp(features).amp_model



def choose_reverb(features: ToneFeatures) -> str | None:
    """Reverb sized from how long the source rings out.

    Returns None when the measurement carries no information: continuous music
    never falls 30 dB below its own peak, so the decay saturates at the clip
    length. In that case the caller should leave whatever reverb the base
    preset already has, rather than inventing a hall or stripping one out on
    no evidence.
    """
    if features.decay_time_s >= features.duration_s * INCONCLUSIVE_DECAY_FRACTION:
        return None
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

    # Knobs are set relative to a reference balance, not from the absolute
    # band shares. A lead line played high on the neck has almost no energy
    # below 250 Hz no matter how the amp's bass control is set, so mapping the
    # share straight onto the knob would bottom out the bass on every solo.
    # What the knobs should track is how the source *differs* from a typical
    # guitar balance.
    return {
        "gain": _clamp(gain),
        "bass": _knob(features.low_energy_ratio, REFERENCE_LOW),
        "mid": _knob(features.mid_energy_ratio, REFERENCE_MID),
        "treb": _knob(features.high_energy_ratio, REFERENCE_HIGH),
    }


def _knob(measured: float, reference: float) -> float:
    """Position a tone control by how far the source sits from typical."""
    return _clamp(0.5 + (measured - reference) * BAND_TO_KNOB, KNOB_MIN, KNOB_MAX)


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
    if reverb is not None:
        preset.set_effect("reverb", reverb)

    if name is not None:
        preset.display_name = name[:DISPLAY_NAME_LENGTH]
    return preset


def describe_settings(preset: Preset, *, choice: AmpChoice | None = None) -> str:
    """The preset as knob positions on the amp's own 0-10 scale.

    Useful on its own: if the automatic choice is close but not right, this is
    what you dial in by hand. Pass the `AmpChoice` to have the output say how
    much to trust the amp model, and what to try instead.
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

    if choice is not None:
        lines.append("")
        lines.append(f"Confidence in the amp model: {choice.confidence:.0%}")
        lines.append(f"  because {choice.reason}")
        if choice.alternatives:
            from lt25_mcp.dsp_catalog import amp_label as _label

            names = ", ".join(_label(a) for a in choice.alternatives)
            lines.append(f"  this one is borderline - also worth auditioning: {names}")
    return "\n".join(lines)
