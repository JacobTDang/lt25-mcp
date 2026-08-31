"""Tests for feature extraction against synthesized signals.

Real audio would beg the question: judging whether a measurement of a guitar
recording is "right" needs ears. Synthesized signals have known properties, so
the extractor can be checked objectively.
"""

import math

import numpy as np
import pytest
import soundfile as sf

from lt25_mcp.analysis.features import FeatureError, ToneFeatures, extract

SR = 22050
DURATION = 2.0


def write(tmp_path, samples, name="test.wav"):
    path = tmp_path / name
    sf.write(path, samples.astype(np.float32), SR)
    return path


def t():
    return np.linspace(0, DURATION, int(SR * DURATION), endpoint=False)


def sine(freq=440.0, amp=0.5):
    return amp * np.sin(2 * math.pi * freq * t())


def square(freq=440.0, amp=0.4):
    return amp * np.sign(np.sin(2 * math.pi * freq * t()))


def noise(amp=0.3, seed=0):
    return amp * np.random.default_rng(seed).standard_normal(int(SR * DURATION))


class TestBasics:
    def test_returns_tone_features(self, tmp_path):
        assert isinstance(extract(write(tmp_path, sine())), ToneFeatures)

    def test_duration_is_measured(self, tmp_path):
        assert extract(write(tmp_path, sine())).duration_s == pytest.approx(DURATION, abs=0.05)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FeatureError, match="no such audio file"):
            extract(tmp_path / "nope.wav")

    def test_silence_raises_rather_than_returning_zeros(self, tmp_path):
        with pytest.raises(FeatureError, match="silent"):
            extract(write(tmp_path, np.zeros(int(SR * 0.5))))


class TestBrightness:
    def test_sine_centroid_sits_near_its_frequency(self, tmp_path):
        centroid = extract(write(tmp_path, sine(440))).spectral_centroid_hz
        assert 300 < centroid < 700

    def test_higher_pitch_gives_higher_centroid(self, tmp_path):
        low = extract(write(tmp_path, sine(220), "low.wav")).spectral_centroid_hz
        high = extract(write(tmp_path, sine(1760), "high.wav")).spectral_centroid_hz
        assert high > low

    def test_noise_is_brighter_than_a_sine(self, tmp_path):
        s = extract(write(tmp_path, sine(440), "s.wav")).spectral_centroid_hz
        n = extract(write(tmp_path, noise(), "n.wav")).spectral_centroid_hz
        assert n > s

    def test_square_sits_between_sine_and_noise(self, tmp_path):
        s = extract(write(tmp_path, sine(440), "s.wav")).spectral_centroid_hz
        q = extract(write(tmp_path, square(440), "q.wav")).spectral_centroid_hz
        n = extract(write(tmp_path, noise(), "n.wav")).spectral_centroid_hz
        assert s < q < n


class TestHarmonicity:
    def test_sine_is_strongly_harmonic(self, tmp_path):
        assert extract(write(tmp_path, sine())).harmonic_ratio > 0.8

    def test_noise_is_less_harmonic_than_a_sine(self, tmp_path):
        s = extract(write(tmp_path, sine(), "s.wav")).harmonic_ratio
        n = extract(write(tmp_path, noise(), "n.wav")).harmonic_ratio
        assert n < s


class TestCrestFactor:
    def test_sine_crest_is_about_3_db(self, tmp_path):
        """A sine's peak-to-RMS ratio is sqrt(2), i.e. 3.01 dB."""
        assert extract(write(tmp_path, sine())).crest_factor_db == pytest.approx(3.01, abs=0.3)

    def test_square_crest_is_about_0_db(self, tmp_path):
        """A square wave is always at full amplitude, so peak equals RMS."""
        assert extract(write(tmp_path, square())).crest_factor_db == pytest.approx(0.0, abs=0.4)

    def test_square_is_more_compressed_than_sine(self, tmp_path):
        s = extract(write(tmp_path, sine(), "s.wav")).crest_factor_db
        q = extract(write(tmp_path, square(), "q.wav")).crest_factor_db
        assert q < s


class TestBandRatios:
    def test_ratios_are_fractions(self, tmp_path):
        f = extract(write(tmp_path, noise()))
        for ratio in (f.low_energy_ratio, f.mid_energy_ratio, f.high_energy_ratio):
            assert 0.0 <= ratio <= 1.0

    def test_low_tone_loads_the_low_band(self, tmp_path):
        assert extract(write(tmp_path, sine(80))).low_energy_ratio > 0.5

    def test_mid_tone_loads_the_mid_band(self, tmp_path):
        assert extract(write(tmp_path, sine(600))).mid_energy_ratio > 0.5

    def test_high_tone_loads_the_high_band(self, tmp_path):
        assert extract(write(tmp_path, sine(6000))).high_energy_ratio > 0.5


class TestDecay:
    def test_sustained_tone_has_a_long_decay(self, tmp_path):
        assert extract(write(tmp_path, sine())).decay_time_s > 1.0

    def test_a_plucked_envelope_decays_faster_than_a_sustained_one(self, tmp_path):
        env = np.exp(-6.0 * t())
        plucked = extract(write(tmp_path, sine() * env, "p.wav")).decay_time_s
        sustained = extract(write(tmp_path, sine(), "s.wav")).decay_time_s
        assert plucked < sustained


class TestKeyAndTuning:
    def test_key_is_a_note_name(self, tmp_path):
        assert extract(write(tmp_path, sine(440))).estimated_key in {
            "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"
        }

    def test_a440_reports_near_zero_tuning_offset(self, tmp_path):
        offset = extract(write(tmp_path, sine(440))).tuning_offset_semitones
        assert abs(offset) < 0.6

    def test_describe_mentions_every_measurement(self, tmp_path):
        text = extract(write(tmp_path, sine())).describe()
        for label in ("centroid", "crest", "harmonic", "decay", "key", "tuning"):
            assert label in text.lower()


class TestDecayIgnoresFadeOuts:
    def test_a_fade_out_at_the_end_does_not_look_like_reverb(self, tmp_path):
        """A clip that simply stops must not read as a long reverb tail."""
        notes = np.zeros_like(t())
        step = int(0.4 * SR)
        for start in range(0, len(notes) - step, step):
            seg = t()[:step]
            notes[start : start + step] = np.sin(2 * math.pi * 220 * seg) * np.exp(-25 * seg)
        dry = extract(write(tmp_path, notes, "dry.wav")).decay_time_s

        faded = notes.copy()
        tail = int(0.5 * SR)
        faded[-tail:] *= np.linspace(1, 0, tail)
        assert extract(write(tmp_path, faded, "faded.wav")).decay_time_s == pytest.approx(
            dry, abs=0.15
        )

    def test_notes_with_a_tail_read_longer_than_dry_notes(self, tmp_path):
        step = int(0.5 * SR)
        dry = np.zeros_like(t())
        wet = np.zeros_like(t())
        for start in range(0, len(dry) - step, step):
            seg = t()[:step]
            dry[start : start + step] = np.sin(2 * math.pi * 220 * seg) * np.exp(-40 * seg)
            wet[start : start + step] = np.sin(2 * math.pi * 220 * seg) * np.exp(-4 * seg)
        assert (
            extract(write(tmp_path, wet, "wet.wav")).decay_time_s
            > extract(write(tmp_path, dry, "dry.wav")).decay_time_s
        )


class TestLowSampleRates:
    """Phone recordings, old uploads and some social clips arrive at 8-16 kHz."""

    def _write_at(self, tmp_path, rate, name):
        n = int(rate * 2.0)
        tt = np.linspace(0, 2.0, n, endpoint=False)
        sig = 0.5 * np.sin(2 * math.pi * 220 * tt) + 0.2 * np.sin(2 * math.pi * 660 * tt)
        path = tmp_path / name
        sf.write(path, sig.astype(np.float32), rate)
        return path

    @pytest.mark.parametrize("rate", [8000, 11025, 16000, 22050, 44100])
    def test_extraction_does_not_crash(self, tmp_path, rate):
        f = extract(self._write_at(tmp_path, rate, f"{rate}.wav"))
        assert f.duration_s == pytest.approx(2.0, abs=0.05)

    def test_sample_rate_is_reported(self, tmp_path):
        assert extract(self._write_at(tmp_path, 8000, "a.wav")).sample_rate_hz == 8000

    def test_truncated_high_band_is_flagged(self, tmp_path):
        """At 8 kHz the 2-8 kHz band is half empty, so treble is not measurable."""
        low = extract(self._write_at(tmp_path, 8000, "low.wav"))
        full = extract(self._write_at(tmp_path, 44100, "full.wav"))
        assert low.high_band_truncated is True
        assert full.high_band_truncated is False

    def test_describe_warns_about_truncation(self, tmp_path):
        text = extract(self._write_at(tmp_path, 8000, "a.wav")).describe()
        assert "truncat" in text.lower() or "nyquist" in text.lower()
