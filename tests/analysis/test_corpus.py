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
    evaluate,
    sweep,
)

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
