"""Tests for turning measurements into a preset.

These assert that the rule table fires as designed. They are not claims about
musical accuracy - that can only be judged by ear, which is why the pipeline
auditions rather than writing straight away.
"""

import pytest

from lt25_mcp.analysis.features import ToneFeatures
from lt25_mcp.analysis.mapping import (
    MappingError,
    build_preset,
    choose_amp_model,
    choose_reverb,
    describe_settings,
    gain_character,
)
from lt25_mcp.dsp_catalog import AMP_MODELS, PASSTHRU


def features(**overrides) -> ToneFeatures:
    """A neutral crunch tone, overridden per test."""
    base = dict(
        spectral_centroid_hz=1800.0,
        spectral_rolloff_hz=4200.0,
        low_energy_ratio=0.20,
        mid_energy_ratio=0.28,
        high_energy_ratio=0.15,
        crest_factor_db=11.0,
        spectral_flatness=0.0015,   # mid-crunch
        harmonic_ratio=0.70,
        onset_strength=1.5,
        decay_time_s=0.8,
        estimated_tempo_bpm=120.0,
        estimated_key="E",
        tuning_offset_semitones=0.0,
        duration_s=10.0,
    )
    base.update(overrides)
    return ToneFeatures(**base)


# Flatness values measured on real amp output: clean ran 0.00001-0.00113,
# crunch 0.00089-0.00233, high gain 0.00285-0.00621. These sit clear of the
# boundaries in mapping.py.
CLEAN = dict(spectral_flatness=0.0004, harmonic_ratio=0.9, spectral_centroid_hz=1400.0)
HIGH_GAIN = dict(spectral_flatness=0.0045, harmonic_ratio=0.45, spectral_centroid_hz=3200.0)


class TestGainCharacter:
    def test_a_peaky_spectrum_reads_clean(self):
        assert gain_character(features(**CLEAN)) == "clean"

    def test_a_flat_spectrum_reads_high_gain(self):
        assert gain_character(features(**HIGH_GAIN)) == "high_gain"

    def test_middle_ground_reads_crunch(self):
        assert gain_character(features()) == "crunch"

    def test_boundaries_are_stable(self):
        """A tone right at a threshold must land in exactly one bucket."""
        from lt25_mcp.analysis.mapping import CLEAN_FLATNESS, HIGH_GAIN_FLATNESS

        for flat in (CLEAN_FLATNESS - 1e-6, CLEAN_FLATNESS, CLEAN_FLATNESS + 1e-6,
                     HIGH_GAIN_FLATNESS - 1e-6, HIGH_GAIN_FLATNESS,
                     HIGH_GAIN_FLATNESS + 1e-6):
            assert gain_character(features(spectral_flatness=flat)) in {
                "clean", "crunch", "high_gain",
            }


class TestAmpChoice:
    def test_always_returns_a_catalogued_model(self):
        for overrides in ({}, CLEAN, HIGH_GAIN):
            assert choose_amp_model(features(**overrides)) in AMP_MODELS

    def test_clean_picks_a_clean_amp(self):
        assert choose_amp_model(features(**CLEAN)) in {
            "DUBS_Twin65",
            "DUBS_Deluxe65",
            "DUBS_Princeton65",
        }

    def test_high_gain_picks_a_high_gain_amp(self):
        assert choose_amp_model(features(**HIGH_GAIN)) in {
            "DUBS_MetalEvh3",
            "DUBS_MetalRect2",
            "DUBS_Rect2",
            "DUBS_Jcm800",
        }

    def test_scooped_mids_at_high_gain_picks_a_rectifier(self):
        chosen = choose_amp_model(features(**HIGH_GAIN, mid_energy_ratio=0.10))
        assert chosen in {"DUBS_MetalRect2", "DUBS_Rect2"}

    def test_forward_mids_at_high_gain_picks_a_marshall(self):
        assert choose_amp_model(features(**HIGH_GAIN, mid_energy_ratio=0.42)) == "DUBS_Jcm800"

    def test_bright_clean_differs_from_dark_clean(self):
        bright = choose_amp_model(features(**{**CLEAN, "spectral_centroid_hz": 2600.0}))
        dark = choose_amp_model(features(**{**CLEAN, "spectral_centroid_hz": 900.0}))
        assert bright != dark


class TestModelFamily:
    def test_every_choosable_model_belongs_to_the_family_that_chooses_it(self):
        """MODEL_FAMILY scores near misses, so it must never disagree with
        the rules: a model chosen for 'clean' that the table called 'crunch'
        would count the chooser wrong for agreeing with itself."""
        from lt25_mcp.analysis.mapping import MODEL_FAMILY, _amp_for_character

        for character in ("clean", "crunch", "high_gain"):
            for centroid in (900.0, 1800.0, 2300.0, 3400.0):
                for mid in (0.05, 0.28, 0.45):
                    for low in (0.10, 0.35):
                        f = features(
                            spectral_centroid_hz=centroid,
                            mid_energy_ratio=mid,
                            low_energy_ratio=low,
                        )
                        chosen = _amp_for_character(character, f)
                        assert MODEL_FAMILY[chosen] == character

    def test_families_are_corpus_labels(self):
        from lt25_mcp.analysis.corpus import LABELS
        from lt25_mcp.analysis.mapping import MODEL_FAMILY

        assert set(MODEL_FAMILY.values()) <= set(LABELS)


class TestReverbChoice:
    """Validated against nine corpus clips with known reverb; see
    docs/measurements.md. The measurement supports presence, not size, and
    never supports absence."""

    def test_a_short_decay_is_inconclusive_not_dry(self):
        """The one real clip that measured under the note-decay bound (0.52 s)
        was recorded through a spring reverb, so a short decay cannot certify
        dryness - it means the take contained a gap, nothing more."""
        assert choose_reverb(features(decay_time_s=0.15)) is None

    def test_a_conclusive_tail_gets_a_modest_room(self):
        assert choose_reverb(features(decay_time_s=1.0)) == "DUBS_SmallRoomReverb"

    def test_size_is_not_read_from_the_tail(self):
        """Both corpus clips with a small room measured 1.95 s and 3.52 s -
        past the old 1.6 s hall boundary - because measured decay adds note
        sustain to the reverb tail. A long tail is still just 'present'."""
        assert choose_reverb(features(decay_time_s=3.5)) == "DUBS_SmallRoomReverb"

    def test_reverb_is_never_stripped(self):
        """Absence is unmeasurable: every no-reverb corpus clip saturated the
        decay measurement rather than measuring short."""
        for decay in (0.05, 0.4, 1.0, 3.0, 7.9):
            assert choose_reverb(features(decay_time_s=decay)) != PASSTHRU


class TestBuildPreset:
    def test_starts_from_the_base_preset(self, sample_preset):
        built = build_preset(features(), sample_preset)
        assert built.to_dict() != sample_preset.to_dict()
        assert sample_preset.amp_model == "DUBS_Twin65"  # base untouched

    def test_sets_tone_controls_within_range(self, sample_preset):
        params = build_preset(features(), sample_preset).params("amp")
        for knob in ("gain", "treb", "mid", "bass"):
            assert 0.0 <= params[knob] <= 1.0

    def test_high_gain_source_produces_more_gain_than_clean(self, sample_preset):
        hot = build_preset(features(**HIGH_GAIN), sample_preset).params("amp")["gain"]
        cool = build_preset(features(**CLEAN), sample_preset).params("amp")["gain"]
        assert hot > cool

    def test_scooped_source_produces_lower_mid(self, sample_preset):
        scooped = build_preset(features(mid_energy_ratio=0.05), sample_preset)
        honky = build_preset(features(mid_energy_ratio=0.55), sample_preset)
        assert scooped.params("amp")["mid"] < honky.params("amp")["mid"]

    def test_names_the_preset(self, sample_preset):
        built = build_preset(features(), sample_preset, name="SURF LEAD")
        assert built.display_name == "SURF LEAD"

    def test_name_is_truncated_not_rejected(self, sample_preset):
        built = build_preset(features(), sample_preset, name="A" * 40)
        assert len(built.to_dict()["info"]["displayName"]) == 16

    def test_result_round_trips_as_json(self, sample_preset):
        import json

        built = build_preset(features(), sample_preset)
        assert json.loads(built.to_json())["info"]["displayName"]

    def test_base_without_an_amp_node_raises(self, sample_preset):
        broken = sample_preset.clone()
        broken._data["audioGraph"]["nodes"] = []
        with pytest.raises(MappingError, match="amp"):
            build_preset(features(), broken)


class TestDescribeSettings:
    def test_mentions_the_amp_and_the_knobs(self, sample_preset):
        text = describe_settings(build_preset(features(**CLEAN), sample_preset))
        assert "gain" in text.lower()
        for knob in ("treble", "middle", "bass"):
            assert knob in text.lower()

    def test_uses_the_amps_own_0_to_10_scale(self, sample_preset):
        """The amp shows 0-10; presets store 0-1. Humans need the former."""
        text = describe_settings(build_preset(features(), sample_preset))
        assert "/10" in text


class TestReverbInconclusive:
    def test_signal_that_never_decays_is_inconclusive(self):
        """Continuous music saturates the decay measurement; that is not a hall."""
        assert choose_reverb(features(decay_time_s=25.0, duration_s=25.0)) is None

    def test_near_saturation_also_counts_as_inconclusive(self):
        assert choose_reverb(features(decay_time_s=9.8, duration_s=10.0)) is None

    def test_inconclusive_leaves_the_base_reverb_alone(self, sample_preset):
        """Do not strip a reverb the base preset has on no evidence."""
        before = sample_preset.unit("reverb")
        built = build_preset(
            features(decay_time_s=25.0, duration_s=25.0), sample_preset
        )
        assert built.unit("reverb") == before

    def test_a_short_decay_also_leaves_the_base_reverb_alone(self, sample_preset):
        """The corpus counterexample: a spring reverb measured 0.52 s. Reading
        that as 'dry' would have stripped a reverb the recording audibly has."""
        before = sample_preset.unit("reverb")
        built = build_preset(features(decay_time_s=0.52), sample_preset)
        assert built.unit("reverb") == before

    def test_a_genuine_tail_still_gets_reverb(self):
        assert choose_reverb(features(decay_time_s=2.0, duration_s=25.0)) != PASSTHRU


class TestKnobsStayUsable:
    def test_no_knob_is_driven_to_a_stop(self):
        """An automatic guess should never bottom out or max out a control."""
        extremes = [
            features(low_energy_ratio=0.0, mid_energy_ratio=0.0, high_energy_ratio=1.0),
            features(low_energy_ratio=1.0, mid_energy_ratio=0.0, high_energy_ratio=0.0),
            features(low_energy_ratio=0.0, mid_energy_ratio=1.0, high_energy_ratio=0.0),
        ]
        from lt25_mcp.analysis.mapping import KNOB_MAX, KNOB_MIN, _tone_controls

        for f in extremes:
            for knob in ("bass", "mid", "treb"):
                assert KNOB_MIN <= _tone_controls(f)[knob] <= KNOB_MAX

    def test_a_typical_balance_lands_mid_travel(self):
        from lt25_mcp.analysis.mapping import (
            REFERENCE_HIGH, REFERENCE_LOW, REFERENCE_MID, _tone_controls,
        )

        f = features(
            low_energy_ratio=REFERENCE_LOW,
            mid_energy_ratio=REFERENCE_MID,
            high_energy_ratio=REFERENCE_HIGH,
        )
        for knob in ("bass", "mid", "treb"):
            assert _tone_controls(f)[knob] == pytest.approx(0.5, abs=0.01)

    def test_brighter_than_reference_raises_treble(self):
        from lt25_mcp.analysis.mapping import REFERENCE_HIGH, _tone_controls

        dull = _tone_controls(features(high_energy_ratio=REFERENCE_HIGH - 0.2))["treb"]
        bright = _tone_controls(features(high_energy_ratio=REFERENCE_HIGH + 0.2))["treb"]
        assert bright > dull


class TestConfidence:
    def test_a_clear_clean_tone_is_confident(self):
        from lt25_mcp.analysis.mapping import choose_amp

        choice = choose_amp(features(spectral_flatness=0.00001, harmonic_ratio=0.95))
        assert choice.confidence > 0.8

    def test_a_tone_on_the_boundary_is_not_confident(self):
        from lt25_mcp.analysis.mapping import CLEAN_FLATNESS, choose_amp

        choice = choose_amp(features(spectral_flatness=CLEAN_FLATNESS))
        assert choice.confidence < 0.35

    def test_confidence_is_a_fraction(self):
        from lt25_mcp.analysis.mapping import choose_amp

        for flat in (0.0, 0.0005, 0.001, 0.002, 0.003, 0.006, 0.05):
            c = choose_amp(features(spectral_flatness=flat))
            assert 0.0 <= c.confidence <= 1.0

    def test_choice_names_the_model_and_a_reason(self):
        from lt25_mcp.analysis.mapping import choose_amp

        choice = choose_amp(features(**CLEAN))
        assert choice.amp_model in AMP_MODELS
        assert choice.reason

    def test_borderline_offers_the_neighbouring_character(self):
        """If it could plausibly be crunch, say which amp that would be."""
        from lt25_mcp.analysis.mapping import CLEAN_FLATNESS, choose_amp

        choice = choose_amp(features(spectral_flatness=CLEAN_FLATNESS + 1e-6))
        assert choice.alternatives
        assert all(a in AMP_MODELS for a in choice.alternatives)
        assert choice.amp_model not in choice.alternatives

    def test_a_confident_choice_offers_no_alternatives(self):
        from lt25_mcp.analysis.mapping import choose_amp

        choice = choose_amp(features(spectral_flatness=0.00001, harmonic_ratio=0.99))
        assert choice.alternatives == []

    def test_choose_amp_model_still_returns_the_primary(self):
        from lt25_mcp.analysis.mapping import choose_amp

        f = features(**HIGH_GAIN)
        assert choose_amp_model(f) == choose_amp(f).amp_model


class TestDescribeReportsUncertainty:
    def test_describe_settings_mentions_low_confidence(self, sample_preset):
        from lt25_mcp.analysis.mapping import CLEAN_FLATNESS, choose_amp

        f = features(spectral_flatness=CLEAN_FLATNESS)
        text = describe_settings(build_preset(f, sample_preset), choice=choose_amp(f))
        assert "confiden" in text.lower() or "uncertain" in text.lower()

    def test_describe_settings_works_without_a_choice(self, sample_preset):
        assert describe_settings(build_preset(features(), sample_preset))


class TestImplausibleDecay:
    def test_a_27_second_tail_is_not_reverb(self):
        """Measured on a real clip that faded out; no room rings for 27 seconds."""
        assert choose_reverb(features(decay_time_s=26.94, duration_s=30.0)) is None

    def test_the_boundary_is_physical_not_proportional(self):
        from lt25_mcp.analysis.mapping import MAX_PLAUSIBLE_DECAY_S

        long_clip = features(decay_time_s=MAX_PLAUSIBLE_DECAY_S + 1, duration_s=600.0)
        assert choose_reverb(long_clip) is None

    def test_a_plausible_tail_in_a_long_clip_still_counts(self):
        assert choose_reverb(features(decay_time_s=2.5, duration_s=600.0)) is not None
