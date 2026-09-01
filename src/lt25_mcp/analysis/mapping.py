"""Turning measurements into a preset.

This is the weakest link in the pipeline and it is worth being honest about
why. Spectral features of a mixed, mastered, lossily-encoded recording reflect
the mix engineer and the codec as much as they reflect the amp. A rule table
over eighteen amp models cannot recover a signal chain from a spectrum.

So this aims for a defensible starting point, not a match. The intended use is
to audition the result, listen, and adjust - which is why `describe_settings`
exists: if the automatic choice is wrong, the numbers are still a better
starting point than the middle of every knob.

Measured against nine corpus clips recorded through known factory presets,
that caution is warranted, in a specific shape: the gain family is right on
8 of 9, but the exact model on only 2 of 9 - roughly what guessing within
the family would score. The centroid and midrange rules that pick between
models inside a family came from reputation, and on the two crunch clips
that test the midrange rule it chose backwards. Nine clips are far too few
to fit better per-model rules without fitting noise, so the rules stand as
written: treat the family as evidence and the specific model as a
suggestion to audition. See docs/measurements.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lt25_mcp.analysis.features import ToneFeatures
from lt25_mcp.preset import DISPLAY_NAME_LENGTH, Preset, PresetError

# Saturation is read from spectral flatness: distortion generates harmonics
# and intermodulation noise, which flattens the spectrum away from the few
# strong peaks a clean tone produces.
#
# Calibrated against ten clips recorded through this amp's own factory presets,
# where Fender's choice of model and gain is the label. Measured ranges:
#
#     clean       0.00001 - 0.00113
#     crunch      0.00089 - 0.00233
#     high gain   0.00285 - 0.00621
#
# These boundaries score 90% on that corpus. See docs/measurements.md.
#
# Crest factor was used here previously and does not work: over a whole take it
# measures the performance's dynamic range - the gaps between notes - more than
# the tone. On the same corpus its best achievable accuracy was 50%, and it
# never once predicted high gain.
CLEAN_FLATNESS = 0.00088
HIGH_GAIN_FLATNESS = 0.00259

# Retained for the audition loop's gain nudge, which compares two takes made
# the same way rather than classifying one in isolation.
CREST_DEADBAND_DB = 2.0

# Where 400-800 Hz stops being scooped and starts being forward. From
# reputation, not measurement - and on the two corpus crunch clips that test
# the forward boundary it chose backwards (the Plexi clip measured 24% mid,
# the Deluxe Dirt clip 44%). Two clips cannot justify a replacement rule, so
# it stands, with its trust recorded in evaluate_models' numbers.
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

# Below this a measured decay is a note's own release, not a room. It used to
# be read as "the source is dry", and the corpus falsified that: the one real
# clip that measured under it (0.52 s) was recorded through a spring reverb.
# A short decay means the take contained a gap, nothing more.
NOTE_DECAY_S = 0.6

# If the measured decay reaches this fraction of the clip, the signal never
# actually decayed and the number means nothing.
INCONCLUSIVE_DECAY_FRACTION = 0.95

# The longest real reverb tail worth believing. Physical spaces and studio
# plates run a few seconds; anything beyond this means the measurement
# caught something that is not reverb - a clip fading out, or a passage
# that simply sustains to the end.
MAX_PLAUSIBLE_DECAY_S = 8.0


# How far from a boundary a measurement must sit before the choice counts as
# confident. The whole flatness range across the corpus spans about 0.006, and
# the two boundaries are 0.0017 apart, so a third of that gap is meaningful.
CONFIDENT_MARGIN = 0.0006

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
    """One of 'clean', 'crunch', 'high_gain', from spectral flatness."""
    if features.spectral_flatness < CLEAN_FLATNESS:
        return "clean"
    if features.spectral_flatness >= HIGH_GAIN_FLATNESS:
        return "high_gain"
    return "crunch"


def _character_confidence(features: ToneFeatures) -> float:
    """How far the flatness sits from the nearest boundary, 0..1."""
    margin = min(
        abs(features.spectral_flatness - CLEAN_FLATNESS),
        abs(features.spectral_flatness - HIGH_GAIN_FLATNESS),
    )
    return _clamp(margin / CONFIDENT_MARGIN)


# The gain family of every model the rules below can choose - the grouping
# they pick within, made public so the corpus can score a near miss (Deluxe65
# for a Twin65 clip) differently from a wrong family (Jcm800 for it). The
# family assignment is solid: it matches how Fender's own factory presets use
# each model. The choice of model *within* a family is what scored 2 of 9.
MODEL_FAMILY: dict[str, str] = {
    "DUBS_Twin65": "clean",
    "DUBS_Princeton65": "clean",
    "DUBS_Deluxe65": "clean",
    "DUBS_Plexi87": "crunch",
    "DUBS_Bassman59": "crunch",
    "DUBS_Deluxe57": "crunch",
    "DUBS_Jcm800": "high_gain",
    "DUBS_MetalRect2": "high_gain",
    "DUBS_Rect2": "high_gain",
    "DUBS_MetalEvh3": "high_gain",
}


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
        f"spectral flatness {features.spectral_flatness:.5f} reads as "
        f"{character.replace('_', ' ')}; centroid "
        f"{features.spectral_centroid_hz:.0f} Hz and "
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
    """A reverb only when the source conclusively rings out; otherwise None.

    Validated against nine corpus clips whose true reverb is known from the
    preset each was recorded through (see docs/measurements.md). The
    measurement supports far less than this function used to claim:

    - Absence is unmeasurable. Every no-reverb clip saturated the decay
      measurement - continuous playing never falls 30 dB below its own peak -
      so a dry source looks exactly like an inconclusive one. This function
      therefore never returns PASSTHRU: it cannot strip a reverb the base
      preset has, only add one or leave it alone.
    - A short decay does not mean dry either. The one clip that measured
      under NOTE_DECAY_S was recorded through a spring reverb; a subtle
      tail sits inside the 30 dB window of the note's own release. Short is
      treated as no information, not as evidence of dryness.
    - Size cannot be read from the number. Measured decay adds note sustain
      and the phrase's fade to the reverb tail, so it overstates it: both
      small-room clips that measured conclusively (1.95 s and 3.52 s) landed
      past the old 1.6 s hall boundary and were called halls. A conclusive
      tail now claims only "some reverb is present" and gets the modest
      room, rather than a size fitted to two samples.

    Returns None whenever the measurement carries no information - saturated,
    implausibly long, or too short to be distinguishable from note decay - and
    the caller keeps whatever reverb the base preset already has.
    """
    if features.decay_time_s >= features.duration_s * INCONCLUSIVE_DECAY_FRACTION:
        return None
    if features.decay_time_s > MAX_PLAUSIBLE_DECAY_S:
        return None
    if features.decay_time_s < NOTE_DECAY_S:
        return None
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
