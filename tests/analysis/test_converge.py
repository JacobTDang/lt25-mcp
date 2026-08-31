"""Tests for the target-versus-current comparison and the convergence loop."""

import pytest

from lt25_mcp.analysis.converge import (
    CONVERGED,
    MAX_STEP,
    Session,
    compare,
)
from lt25_mcp.analysis.features import ToneFeatures


def features(**overrides) -> ToneFeatures:
    base = dict(
        spectral_centroid_hz=1800.0,
        spectral_rolloff_hz=4200.0,
        low_energy_ratio=0.25,
        mid_energy_ratio=0.45,
        high_energy_ratio=0.30,
        crest_factor_db=12.0,
        harmonic_ratio=0.9,
        onset_strength=1.5,
        decay_time_s=0.8,
        estimated_tempo_bpm=120.0,
        estimated_key="E",
        tuning_offset_semitones=0.0,
        duration_s=10.0,
    )
    base.update(overrides)
    return ToneFeatures(**base)


class TestDistance:
    def test_identical_tones_are_converged(self):
        target = features()
        result = compare(target, target)
        assert result.distance == pytest.approx(0.0)
        assert result.converged

    def test_distance_grows_with_the_gap(self):
        target = features()
        near = compare(target, features(high_energy_ratio=0.33)).distance
        far = compare(target, features(high_energy_ratio=0.60)).distance
        assert 0 < near < far

    def test_distance_is_symmetric(self):
        a, b = features(), features(low_energy_ratio=0.40)
        assert compare(a, b).distance == pytest.approx(compare(b, a).distance)

    def test_a_small_gap_counts_as_converged(self):
        target = features()
        close = features(high_energy_ratio=0.31, low_energy_ratio=0.26)
        assert compare(target, close).distance < CONVERGED


class TestDirection:
    def test_too_much_treble_gets_treble_turned_down(self):
        result = compare(features(), features(high_energy_ratio=0.55))
        treb = [m for m in result.moves if m.control == "treb"]
        assert treb and treb[0].delta < 0

    def test_not_enough_bass_gets_bass_turned_up(self):
        result = compare(features(), features(low_energy_ratio=0.05))
        bass = [m for m in result.moves if m.control == "bass"]
        assert bass and bass[0].delta > 0

    def test_a_brighter_amp_than_target_gets_less_treble(self):
        result = compare(features(), features(spectral_centroid_hz=3600.0))
        treb = [m for m in result.moves if m.control == "treb"]
        assert treb and treb[0].delta < 0

    def test_a_more_compressed_amp_than_target_gets_less_gain(self):
        result = compare(features(), features(crest_factor_db=5.0))
        gain = [m for m in result.moves if m.control == "gain"]
        assert gain and gain[0].delta < 0

    def test_the_largest_gap_is_addressed_first(self):
        result = compare(
            features(),
            features(low_energy_ratio=0.60, high_energy_ratio=0.33),
        )
        assert result.moves[0].control == "bass"


class TestRestraint:
    def test_noise_sized_gaps_are_left_alone(self):
        result = compare(features(), features(high_energy_ratio=0.31))
        assert not any(m.control == "treb" for m in result.moves)

    def test_no_move_exceeds_the_step_limit(self):
        result = compare(
            features(),
            features(low_energy_ratio=0.99, high_energy_ratio=0.0,
                     spectral_centroid_hz=200.0, crest_factor_db=40.0),
        )
        assert all(abs(m.delta) <= MAX_STEP for m in result.moves)

    def test_converged_tones_suggest_nothing(self):
        target = features()
        assert compare(target, target).moves == []

    def test_treble_is_not_moved_twice_for_the_same_thing(self):
        """Band gap and centroid describe the same brightness; move once."""
        result = compare(
            features(),
            features(high_energy_ratio=0.55, spectral_centroid_hz=3600.0),
        )
        assert len([m for m in result.moves if m.control == "treb"]) == 1

    def test_absolute_level_is_never_compared(self):
        """A mastered recording's level says nothing; it must not drive a move."""
        import inspect

        from lt25_mcp.analysis import converge

        assert "output_dbfs" not in inspect.getsource(converge)


class TestSession:
    def test_records_each_iteration(self):
        session = Session(target=features())
        session.record(compare(features(), features(low_energy_ratio=0.5)), {"bass": 5.0})
        session.record(compare(features(), features(low_energy_ratio=0.4)), {"bass": 4.0})
        assert [i.index for i in session.history] == [1, 2]

    def test_improving_is_unknown_until_two_iterations(self):
        session = Session(target=features())
        assert session.improving is None
        session.record(compare(features(), features(low_energy_ratio=0.5)), {})
        assert session.improving is None

    def test_detects_improvement(self):
        session = Session(target=features())
        session.record(compare(features(), features(low_energy_ratio=0.60)), {})
        session.record(compare(features(), features(low_energy_ratio=0.30)), {})
        assert session.improving is True

    def test_detects_getting_worse(self):
        session = Session(target=features())
        session.record(compare(features(), features(low_energy_ratio=0.30)), {})
        session.record(compare(features(), features(low_energy_ratio=0.60)), {})
        assert session.improving is False

    def test_best_iteration_is_the_closest_not_the_last(self):
        session = Session(target=features())
        session.record(compare(features(), features(low_energy_ratio=0.26)), {"bass": 5.0})
        session.record(compare(features(), features(low_energy_ratio=0.60)), {"bass": 9.0})
        assert session.best.index == 1

    def test_applying_moves_stays_in_range(self):
        session = Session(target=features())
        result = compare(features(), features(low_energy_ratio=0.99))
        for start in ({"bass": 0.0}, {"bass": 10.0}):
            assert 0.0 <= session.apply(start, result)["bass"] <= 10.0

    def test_applying_moves_actually_reduces_the_gap(self):
        """One iteration of the loop must move the knob the right way."""
        session = Session(target=features())
        result = compare(features(), features(low_energy_ratio=0.60))
        assert session.apply({"bass": 5.0}, result)["bass"] < 5.0


class TestDescribe:
    def test_reports_distance_and_the_next_move(self):
        text = compare(features(), features(high_energy_ratio=0.55)).describe()
        assert "distance" in text
        assert "treb" in text

    def test_says_when_converged(self):
        assert "CONVERGED" in compare(features(), features()).describe()


class TestBacktracking:
    """Computing `improving` and ignoring it means a bad move compounds."""

    def _session(self):
        return Session(target=features())

    def test_an_improving_step_is_kept(self):
        s = self._session()
        s.step({"bass": 5.0}, compare(features(), features(low_energy_ratio=0.50)))
        after = s.step({"bass": 4.0}, compare(features(), features(low_energy_ratio=0.30)))
        assert after.reverted is False
        assert after.knobs["bass"] == pytest.approx(4.0)

    def test_a_worsening_step_reverts_to_the_best(self):
        s = self._session()
        s.step({"bass": 5.0}, compare(features(), features(low_energy_ratio=0.28)))
        worse = s.step({"bass": 9.0}, compare(features(), features(low_energy_ratio=0.70)))
        assert worse.reverted is True
        assert worse.knobs["bass"] == pytest.approx(5.0)

    def test_reverting_halves_the_step_size(self):
        s = self._session()
        s.step({"bass": 5.0}, compare(features(), features(low_energy_ratio=0.28)))
        before = s.step_scale
        s.step({"bass": 9.0}, compare(features(), features(low_energy_ratio=0.70)))
        assert s.step_scale == pytest.approx(before / 2)

    def test_the_scale_shrinks_the_applied_move(self):
        s = self._session()
        result = compare(features(), features(low_energy_ratio=0.60))
        full = s.apply({"bass": 5.0}, result)["bass"]
        s.step_scale = 0.5
        half = s.apply({"bass": 5.0}, result)["bass"]
        assert abs(half - 5.0) == pytest.approx(abs(full - 5.0) / 2, abs=1e-6)

    def test_it_gives_up_after_repeated_regressions(self):
        s = self._session()
        s.step({"bass": 5.0}, compare(features(), features(low_energy_ratio=0.28)))
        for _ in range(3):
            s.step({"bass": 9.0}, compare(features(), features(low_energy_ratio=0.70)))
        assert s.exhausted

    def test_a_run_that_keeps_improving_is_not_exhausted(self):
        s = self._session()
        for ratio in (0.60, 0.45, 0.32, 0.27):
            s.step({"bass": 5.0}, compare(features(), features(low_energy_ratio=ratio)))
        assert not s.exhausted

    def test_best_is_reported_not_last(self):
        s = self._session()
        s.step({"bass": 5.0}, compare(features(), features(low_energy_ratio=0.28)))
        s.step({"bass": 9.0}, compare(features(), features(low_energy_ratio=0.70)))
        assert s.best.index == 1
