"""Tests for the stability harness itself."""

import math

import numpy as np
import pytest
import soundfile as sf

from lt25_mcp.analysis.features import ToneFeatures
from lt25_mcp.analysis.stability import (
    KNOB_TOLERANCE,
    StabilityReport,
    VariantResult,
    assess,
    knob_variance,
    perturb,
)

SR = 22050


def clip(tmp_path, drive=1.0, name="clip.wav", seconds=3.0):
    """A sustained harmonic stack; `drive` sets the saturation.

    Continuous rather than note-and-gap: silence has a flat spectrum, and
    classification reads spectral flatness, so gaps would swamp the thing under
    test.
    """
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    note = sum(np.sin(2 * math.pi * 220 * n * t) / n for n in range(1, 6))
    path = tmp_path / name
    sf.write(path, (np.tanh(note * drive) * 0.6).astype(np.float32), SR,
             subtype="FLOAT")
    return path


def result(name, amp, knobs=None, reverb=None, character="clean", confidence=1.0):
    return VariantResult(
        name=name,
        character=character,
        amp_model=amp,
        confidence=confidence,
        reverb=reverb,
        knobs=knobs or dict(gain=0.5, treb=0.5, mid=0.5, bass=0.5),
        features=ToneFeatures(
            spectral_centroid_hz=1000.0, spectral_rolloff_hz=3000.0,
            low_energy_ratio=0.25, mid_energy_ratio=0.45, high_energy_ratio=0.30,
            crest_factor_db=12.0, harmonic_ratio=0.9, onset_strength=1.0,
            decay_time_s=0.5, estimated_tempo_bpm=120.0, estimated_key="E",
            tuning_offset_semitones=0.0, duration_s=3.0, sample_rate_hz=44100,
        ),
    )


class TestReportArithmetic:
    def test_all_agreeing_is_stable(self):
        base = result("baseline", "DUBS_Twin65")
        report = StabilityReport(base, [result("a", "DUBS_Twin65"), result("b", "DUBS_Twin65")])
        assert report.amp_agreement == 1.0
        assert report.is_stable

    def test_one_disagreement_shows_up(self):
        base = result("baseline", "DUBS_Twin65")
        report = StabilityReport(base, [result("a", "DUBS_Twin65"), result("b", "DUBS_Jcm800")])
        assert report.amp_agreement == 0.5
        assert not report.is_stable

    def test_knob_spread_is_on_the_amp_scale(self):
        base = result("baseline", "X", knobs=dict(gain=0.2, treb=0.5, mid=0.5, bass=0.5))
        drifted = result("a", "X", knobs=dict(gain=0.5, treb=0.5, mid=0.5, bass=0.5))
        assert StabilityReport(base, [drifted]).knob_spread["gain"] == pytest.approx(3.0)

    def test_a_wandering_knob_is_unstable_even_with_the_same_amp(self):
        base = result("baseline", "X", knobs=dict(gain=0.1, treb=0.5, mid=0.5, bass=0.5))
        drifted = result("a", "X", knobs=dict(gain=0.9, treb=0.5, mid=0.5, bass=0.5))
        report = StabilityReport(base, [drifted])
        assert report.amp_agreement == 1.0
        assert not report.is_stable
        assert report.worst_knob[0] == "gain"

    def test_reverb_disagreement_counts(self):
        base = result("baseline", "X", reverb=None)
        other = result("a", "X", reverb="DUBS_LargeHallReverb")
        assert StabilityReport(base, [other]).reverb_agreement == 0.0

    def test_no_variants_is_trivially_stable(self):
        assert StabilityReport(result("baseline", "X")).is_stable

    def test_describe_flags_the_verdict(self):
        base = result("baseline", "DUBS_Twin65")
        stable = StabilityReport(base, [result("a", "DUBS_Twin65")])
        unstable = StabilityReport(base, [result("a", "DUBS_Jcm800")])
        assert "STABLE" in stable.describe()
        assert "UNSTABLE" in unstable.describe()

    def test_tolerance_is_documented_on_the_amp_scale(self):
        assert 0 < KNOB_TOLERANCE <= 2.0


class TestPerturb:
    def test_writes_several_variants(self, tmp_path):
        variants = perturb(clip(tmp_path), tmp_path / "out")
        assert len(variants) >= 5
        assert all(p.exists() for p in variants.values())

    def test_includes_level_and_section_changes(self, tmp_path):
        names = set(perturb(clip(tmp_path), tmp_path / "out"))
        assert any("dB" in n for n in names)
        assert any("half" in n for n in names)

    def test_skips_resampling_when_already_low(self, tmp_path):
        import numpy as np

        t = np.linspace(0, 2.0, 16000 * 2, endpoint=False)
        path = tmp_path / "low.wav"
        sf.write(path, (0.5 * np.sin(2 * math.pi * 220 * t)).astype(np.float32), 16000)
        assert "16 kHz" not in perturb(path, tmp_path / "out")


class TestAssessRealAudio:
    @pytest.mark.parametrize("drive", [0.2, 40.0])
    def test_an_unambiguous_clip_is_stable(self, tmp_path, drive):
        """Clearly clean and clearly saturated material must not wander.

        A borderline clip legitimately can: that is what the low confidence
        score is for, and demanding stability there would be demanding the
        rules pretend to a certainty they do not have.
        """
        report = assess(clip(tmp_path, drive=drive), tmp_path / "work")
        assert report.character_agreement == 1.0, report.describe()

    def test_confidence_is_reported_and_bounded(self, tmp_path):
        """A synthesized stack is far peakier than any real amp output - its
        flatness tops out around 0.00016 against a clean boundary of 0.00088 -
        so a genuine borderline case cannot be built here. That is covered in
        test_mapping against constructed features; this checks the value is
        produced and sane."""
        report = assess(clip(tmp_path, drive=1.0), tmp_path / "work")
        assert 0.0 <= report.baseline.confidence <= 1.0
        assert all(0.0 <= v.confidence <= 1.0 for v in report.variants)

    def test_variance_is_reported_per_knob(self, tmp_path):
        variance = knob_variance(assess(clip(tmp_path), tmp_path / "work"))
        assert set(variance) == {"gain", "treb", "mid", "bass"}
        assert all(v >= 0 for v in variance.values())


class TestPerturbationsDoNotClip:
    def test_boosting_does_not_clip_the_signal(self, tmp_path):
        """A clipped boost would change the crest factor and so the verdict."""
        import librosa

        variants = perturb(clip(tmp_path), tmp_path / "out")
        loud = [p for n, p in variants.items() if "+6dB" in n][0]
        original, _ = librosa.load(str(clip(tmp_path)), sr=None, mono=True)
        boosted, _ = librosa.load(str(loud), sr=None, mono=True)
        expected = float(np.max(np.abs(original))) * 10 ** (6 / 20)
        assert float(np.max(np.abs(boosted))) == pytest.approx(expected, rel=0.02)


class TestSampleRateIsNotCountedAsDisagreement:
    """Flatness depends on the sample rate, so a resampled variant measures
    something genuinely different rather than revealing instability."""

    def test_a_resampled_variant_is_excluded(self):
        from dataclasses import replace

        base = result("baseline", "DUBS_Twin65")
        other = result("16 kHz", "DUBS_Jcm800")
        other = VariantResult(
            name="16 kHz", character="high_gain", amp_model="DUBS_Jcm800",
            confidence=1.0, reverb=None, knobs=other.knobs,
            features=replace(other.features, sample_rate_hz=16000),
        )
        report = StabilityReport(base, [other])
        assert report.comparable == []
        assert report.amp_agreement == 1.0

    def test_comparable_variants_still_count(self):
        base = result("baseline", "DUBS_Twin65")
        same = result("quiet", "DUBS_Jcm800")
        report = StabilityReport(base, [same])
        assert len(report.comparable) == 1
        assert report.amp_agreement == 0.0
