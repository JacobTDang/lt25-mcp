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


# A pure sine measures 3.0 dB crest and a square wave 0.0 dB, so these sit
# either side of the thresholds in mapping.py.
CLEAN = dict(crest_factor_db=13.0, harmonic_ratio=0.9, spectral_centroid_hz=1400.0)
HIGH_GAIN = dict(crest_factor_db=2.5, harmonic_ratio=0.45, spectral_centroid_hz=3200.0)


class TestGainCharacter:
    def test_dynamic_and_harmonic_reads_clean(self):
        assert gain_character(features(**CLEAN)) == "clean"

    def test_compressed_and_noisy_reads_high_gain(self):
        assert gain_character(features(**HIGH_GAIN)) == "high_gain"

    def test_middle_ground_reads_crunch(self):
        assert gain_character(features()) == "crunch"

    def test_boundaries_are_stable(self):
        """A tone right at a threshold must land in exactly one bucket."""
        for crest in (4.4, 4.5, 4.6, 9.4, 9.5, 9.6):
            assert gain_character(features(crest_factor_db=crest)) in {
                "clean",
                "crunch",
                "high_gain",
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


class TestReverbChoice:
    def test_dry_signal_gets_no_reverb(self):
        assert choose_reverb(features(decay_time_s=0.15)) == PASSTHRU

    def test_long_tail_gets_a_big_reverb(self):
        assert choose_reverb(features(decay_time_s=3.5)) in {
            "DUBS_LargeHallReverb",
            "DUBS_ArenaReverb",
        }

    def test_short_tail_gets_a_room(self):
        assert choose_reverb(features(decay_time_s=1.0)) == "DUBS_SmallRoomReverb"


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
