"""Tests for the labelled corpus and the threshold sweep."""

import math

import numpy as np
import pytest
import soundfile as sf

from lt25_mcp.analysis.corpus import (
    LABELS,
    MIN_PER_LABEL,
    Corpus,
    CorpusError,
    Sample,
    amp_model_from_source,
    evaluate,
    evaluate_models,
    sweep,
)
from lt25_mcp.analysis.features import ToneFeatures

SR = 22050


def tone(tmp_path, name, drive, seconds=2.0):
    """A sustained harmonic stack, soft-clipped. `drive` sets the saturation.

    Deliberately continuous. An earlier version left silence between notes,
    and silence has a flat spectrum, so it dominated the measurement: every
    drive from 0.2 to 40 produced a flatness of 0.1840 and the fixture tested
    nothing.
    """
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    note = sum(np.sin(2 * math.pi * 196 * n * t) / n for n in range(1, 7))
    sig = np.tanh(note * drive) * 0.6
    path = tmp_path / name
    sf.write(path, sig.astype(np.float32), SR, subtype="FLOAT")
    return path


@pytest.fixture
def corpus(tmp_path):
    c = Corpus()
    for i, drive in enumerate((0.2, 0.4, 0.6)):
        c.add(tone(tmp_path, f"clean{i}.wav", drive), "clean")
    for i, drive in enumerate((2.5, 3.5, 4.5)):
        c.add(tone(tmp_path, f"crunch{i}.wav", drive), "crunch")
    for i, drive in enumerate((20.0, 30.0, 40.0)):
        c.add(tone(tmp_path, f"metal{i}.wav", drive), "high_gain")
    return c


class TestCorpus:
    def test_rejects_an_unknown_label(self, tmp_path):
        with pytest.raises(CorpusError, match="not a known label"):
            Corpus().add(tmp_path / "a.wav", "spicy")

    def test_adding_the_same_path_twice_replaces_it(self, tmp_path):
        c = Corpus()
        c.add(tmp_path / "a.wav", "clean")
        c.add(tmp_path / "a.wav", "crunch")
        assert len(c.samples) == 1
        assert c.samples[0].label == "crunch"

    def test_counts_every_label(self, corpus):
        assert corpus.counts() == {"clean": 3, "crunch": 3, "high_gain": 3}

    def test_thin_labels_are_reported(self, tmp_path):
        c = Corpus()
        c.add(tmp_path / "a.wav", "clean")
        assert set(c.thin) == {"clean", "crunch", "high_gain"}
        assert MIN_PER_LABEL > 1

    def test_a_full_corpus_is_not_thin(self, corpus):
        assert corpus.thin == []

    def test_round_trips_through_disk(self, corpus, tmp_path):
        path = corpus.save(tmp_path / "corpus.json")
        assert Corpus.load(path).to_dict() == corpus.to_dict()

    def test_missing_file_gives_an_empty_corpus(self, tmp_path):
        assert Corpus.load(tmp_path / "nope.json").samples == []

    def test_a_missing_clip_is_reported_not_skipped(self, tmp_path):
        c = Corpus()
        c.add(tmp_path / "gone.wav", "clean")
        with pytest.raises(CorpusError, match="missing file"):
            evaluate(c)


class TestEvaluate:
    def test_reports_one_prediction_per_sample(self, corpus):
        assert len(evaluate(corpus).predictions) == 9

    def test_confusion_matrix_totals_the_corpus(self, corpus):
        report = evaluate(corpus)
        total = sum(sum(row.values()) for row in report.confusion.values())
        assert total == len(corpus.samples)

    def test_accuracy_is_a_fraction(self, corpus):
        assert 0.0 <= evaluate(corpus).accuracy <= 1.0

    def test_permissive_thresholds_call_everything_clean(self, corpus):
        # Flatness is never negative, so a huge clean boundary catches every clip.
        report = evaluate(corpus, clean_flat=1.0, high_gain_flat=2.0)
        assert {p.predicted for p in report.predictions} == {"clean"}

    def test_describe_mentions_accuracy_and_thresholds(self, corpus):
        text = evaluate(corpus).describe()
        assert "accuracy" in text and "thresholds" in text

    def test_every_label_is_reachable(self, corpus):
        """A rule that can never emit a label would be a silent dead branch."""
        # Synthesized tones are far peakier than a real amp, so these sit at
        # the fixture's own scale rather than the production thresholds.
        seen = set()
        for clean in (0.00002, 0.00005, 0.0002):
            for high in (0.00004, 0.0001, 0.0005):
                if high <= clean:
                    continue
                seen |= {p.predicted for p in evaluate(corpus, clean, high).predictions}
        assert seen == set(LABELS)


class TestSweep:
    def test_finds_thresholds_at_least_as_good_as_the_defaults(self, corpus):
        best, _ = sweep(corpus)
        assert best.accuracy >= evaluate(corpus).accuracy

    def test_never_returns_an_inverted_pair(self, corpus):
        best, all_reports = sweep(corpus)
        assert best.high_gain_flat > best.clean_flat
        assert all(r.high_gain_flat > r.clean_flat for r in all_reports)

    def test_an_empty_corpus_raises(self):
        with pytest.raises(CorpusError, match="empty corpus"):
            sweep(Corpus())

    def test_ties_prefer_the_wider_separation(self, corpus):
        best, all_reports = sweep(corpus)
        tied = [r for r in all_reports if r.accuracy == best.accuracy]
        widest = max(r.high_gain_flat - r.clean_flat for r in tied)
        assert best.high_gain_flat - best.clean_flat == widest

    def test_a_separable_corpus_is_classified_perfectly(self, corpus):
        """These fixtures are deliberately far apart; if this fails the rule is broken."""
        best, _ = sweep(corpus)
        assert best.accuracy == 1.0, best.describe()


def measured_tone(**overrides) -> ToneFeatures:
    """Hand-built features, so a model prediction is known without audio."""
    base = dict(
        spectral_centroid_hz=1800.0,
        spectral_rolloff_hz=4200.0,
        low_energy_ratio=0.20,
        mid_energy_ratio=0.28,
        high_energy_ratio=0.15,
        crest_factor_db=11.0,
        spectral_flatness=0.0015,
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


# Flatness 0.0004 reads clean; centroid 1400 Hz then chooses DUBS_Deluxe65.
CLEAN_MID_CENTROID = dict(spectral_flatness=0.0004, spectral_centroid_hz=1400.0)


class TestAmpModelTruth:
    def test_a_source_naming_a_factory_preset_yields_its_model(self):
        src = "amp slot 1 FENDER CLEAN (TWIN CLEAN), played live"
        assert amp_model_from_source(src) == "DUBS_Twin65"

    def test_a_source_without_an_amp_label_yields_nothing(self):
        assert amp_model_from_source("courage solo (TAB lesson), demucs stem") == ""

    def test_an_unknown_amp_model_is_rejected(self, tmp_path):
        with pytest.raises(CorpusError, match="not a known amp model"):
            Corpus().add(tmp_path / "a.wav", "clean", amp_model="DUBS_Imaginary")

    def test_backfill_fills_models_recoverable_from_sources(self, tmp_path):
        c = Corpus()
        c.add(tmp_path / "a.wav", "clean",
              source="amp slot 1 FENDER CLEAN (TWIN CLEAN), played live")
        c.add(tmp_path / "b.wav", "clean", source="a stem separated from a mix")
        assert c.backfill_amp_models() == 1
        assert c.samples[0].amp_model == "DUBS_Twin65"
        assert c.samples[1].amp_model == ""

    def test_backfill_leaves_an_explicit_model_alone(self, tmp_path):
        c = Corpus()
        c.add(tmp_path / "a.wav", "crunch",
              source="amp slot 4 CLASSIC ROCK (70S ROCK), played live",
              amp_model="DUBS_Bassman59")
        assert c.backfill_amp_models() == 0
        assert c.samples[0].amp_model == "DUBS_Bassman59"

    def test_amp_model_round_trips_through_disk(self, tmp_path):
        c = Corpus()
        c.add(tmp_path / "a.wav", "clean", amp_model="DUBS_Twin65")
        path = c.save(tmp_path / "corpus.json")
        assert Corpus.load(path).samples[0].amp_model == "DUBS_Twin65"

    def test_a_corpus_saved_before_the_field_existed_still_loads(self):
        data = {"samples": [{"path": "a.wav", "label": "clean",
                             "source": "", "notes": ""}]}
        assert Corpus.from_dict(data).samples[0].amp_model == ""

    def test_a_corpus_from_newer_code_is_refused_not_stripped(self):
        """Loading would drop the unknown field on the next save - a corpus
        written by newer code must be refused, not quietly truncated."""
        data = {"samples": [{"path": "a.wav", "label": "clean",
                             "sparkle": "yes"}]}
        with pytest.raises(CorpusError, match="sparkle"):
            Corpus.from_dict(data)


class TestEvaluateModels:
    def test_a_corpus_with_no_known_models_raises(self, corpus):
        with pytest.raises(CorpusError, match="amp model"):
            evaluate_models(corpus)

    def test_scores_only_clips_with_a_known_model(self):
        known = Sample("a.wav", "clean", amp_model="DUBS_Deluxe65")
        unknown = Sample("b.wav", "clean")
        report = evaluate_models(
            Corpus(samples=[known, unknown]),
            measured=[(known, measured_tone(**CLEAN_MID_CENTROID)),
                      (unknown, measured_tone())],
        )
        assert len(report.predictions) == 1
        assert report.skipped == 1

    def test_the_exact_model_counts_as_exact_and_family(self):
        sample = Sample("a.wav", "clean", amp_model="DUBS_Deluxe65")
        report = evaluate_models(
            Corpus(samples=[sample]),
            measured=[(sample, measured_tone(**CLEAN_MID_CENTROID))],
        )
        assert report.predictions[0].exact
        assert report.predictions[0].family
        assert report.exact_accuracy == 1.0
        assert report.family_accuracy == 1.0

    def test_the_right_family_wrong_model_is_a_near_miss(self):
        sample = Sample("a.wav", "clean", amp_model="DUBS_Twin65")
        report = evaluate_models(
            Corpus(samples=[sample]),
            measured=[(sample, measured_tone(**CLEAN_MID_CENTROID))],
        )
        assert not report.predictions[0].exact
        assert report.predictions[0].family
        assert report.exact_accuracy == 0.0
        assert report.family_accuracy == 1.0

    def test_the_wrong_family_is_neither(self):
        sample = Sample("a.wav", "high_gain", amp_model="DUBS_Jcm800")
        report = evaluate_models(
            Corpus(samples=[sample]),
            measured=[(sample, measured_tone(**CLEAN_MID_CENTROID))],
        )
        assert not report.predictions[0].exact
        assert not report.predictions[0].family
        assert report.family_accuracy == 0.0

    def test_confusion_counts_truth_to_predicted_pairs(self):
        samples = [Sample(f"{i}.wav", "clean", amp_model="DUBS_Twin65")
                   for i in range(2)]
        report = evaluate_models(
            Corpus(samples=list(samples)),
            measured=[(s, measured_tone(**CLEAN_MID_CENTROID)) for s in samples],
        )
        assert report.confusion == {"DUBS_Twin65": {"DUBS_Deluxe65": 2}}

    def test_describe_reports_both_accuracies_and_the_skipped(self):
        known = Sample("a.wav", "clean", amp_model="DUBS_Twin65")
        unknown = Sample("b.wav", "clean")
        text = evaluate_models(
            Corpus(samples=[known, unknown]),
            measured=[(known, measured_tone(**CLEAN_MID_CENTROID))],
        ).describe()
        assert "exact" in text
        assert "family" in text
        assert "skipped" in text

    def test_measures_the_audio_when_no_measurements_are_given(self, tmp_path):
        c = Corpus()
        c.add(tone(tmp_path, "a.wav", 0.2), "clean", amp_model="DUBS_Twin65")
        assert len(evaluate_models(c).predictions) == 1
