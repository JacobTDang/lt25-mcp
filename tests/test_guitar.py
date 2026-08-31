"""Tests for measured guitar profiles and per-guitar adaptation."""

import json

import pytest

from lt25_mcp.guitar import (
    MAX_ADJUSTMENT,
    GuitarError,
    GuitarLibrary,
    GuitarProfile,
    adapt,
)


def profile(name, *, output=-18.0, centroid=1800.0, crest=12.0, sustain=1.0, **kw):
    return GuitarProfile(
        name=name, output_dbfs=output, centroid_hz=centroid,
        crest_factor_db=crest, sustain_s=sustain, **kw
    )


BASE = {"gain": 5.0, "treb": 5.0, "mid": 5.0, "bass": 5.0}


class TestProfile:
    def test_needs_a_name(self):
        with pytest.raises(GuitarError, match="needs a name"):
            profile("  ")

    def test_stamps_a_capture_time(self):
        assert profile("strat").captured_at

    def test_round_trips(self):
        p = profile("strat", pickups="single_coil")
        assert GuitarProfile.from_dict(p.to_dict()).to_dict() == p.to_dict()

    def test_describe_marks_the_reference(self):
        assert "reference" in profile("strat", is_reference=True).describe()


class TestLibrary:
    def test_first_guitar_becomes_the_reference(self, tmp_path):
        lib = GuitarLibrary()
        lib.add(profile("strat"))
        assert lib.reference.name == "strat"

    def test_second_guitar_does_not_steal_the_reference(self):
        lib = GuitarLibrary()
        lib.add(profile("strat"))
        lib.add(profile("les paul"))
        assert lib.reference.name == "strat"

    def test_reference_can_be_moved(self):
        lib = GuitarLibrary()
        lib.add(profile("strat"))
        lib.add(profile("les paul"))
        lib.set_reference("les paul")
        assert lib.reference.name == "les paul"
        assert lib.guitars["strat"].is_reference is False

    def test_moving_the_reference_to_an_unknown_guitar_raises(self):
        with pytest.raises(GuitarError, match="calibrate it first"):
            GuitarLibrary().set_reference("telecaster")

    def test_round_trips_through_disk(self, tmp_path):
        lib = GuitarLibrary()
        lib.add(profile("strat"))
        lib.add(profile("les paul"))
        path = lib.save(tmp_path / "guitars.json")
        assert GuitarLibrary.load(path).to_dict() == lib.to_dict()

    def test_missing_file_gives_an_empty_library(self, tmp_path):
        assert GuitarLibrary.load(tmp_path / "nope.json").guitars == {}

    def test_empty_library_has_no_reference(self):
        assert GuitarLibrary().reference is None


class TestAdapt:
    def test_the_reference_guitar_needs_no_adjustment(self):
        ref = profile("strat", is_reference=True)
        adjusted, notes = adapt(BASE, ref, ref)
        assert adjusted == BASE
        assert notes == []

    def test_no_reference_means_no_adjustment(self):
        adjusted, notes = adapt(BASE, profile("strat"), None)
        assert adjusted == BASE
        assert notes == []

    def test_a_hotter_guitar_gets_less_gain(self):
        ref = profile("strat", output=-20.0)
        hot = profile("les paul", output=-14.0)
        adjusted, notes = adapt(BASE, hot, ref)
        assert adjusted["gain"] < BASE["gain"]
        assert "hotter" in notes[0]

    def test_a_quieter_guitar_gets_more_gain(self):
        ref = profile("les paul", output=-14.0)
        quiet = profile("strat", output=-20.0)
        assert adapt(BASE, quiet, ref)[0]["gain"] > BASE["gain"]

    def test_a_brighter_guitar_gets_less_treble(self):
        ref = profile("les paul", centroid=1200.0)
        bright = profile("strat", centroid=2400.0)
        adjusted, notes = adapt(BASE, bright, ref)
        assert adjusted["treb"] < BASE["treb"]
        assert any("brighter" in n for n in notes)

    def test_a_darker_guitar_gets_more_treble(self):
        ref = profile("strat", centroid=2400.0)
        dark = profile("les paul", centroid=1200.0)
        assert adapt(BASE, dark, ref)[0]["treb"] > BASE["treb"]

    def test_adjustments_are_capped(self):
        ref = profile("quiet", output=-60.0, centroid=200.0)
        loud = profile("loud", output=0.0, centroid=8000.0)
        adjusted, _ = adapt(BASE, loud, ref)
        for knob in ("gain", "treb"):
            assert abs(adjusted[knob] - BASE[knob]) <= MAX_ADJUSTMENT + 1e-9

    def test_knobs_stay_within_the_amp_range(self):
        ref = profile("a", output=-60.0, centroid=200.0)
        other = profile("b", output=0.0, centroid=8000.0)
        for start in ({"gain": 0.0, "treb": 0.0}, {"gain": 10.0, "treb": 10.0}):
            adjusted, _ = adapt(start, other, ref)
            assert all(0.0 <= v <= 10.0 for v in adjusted.values())

    def test_input_is_not_mutated(self):
        original = dict(BASE)
        adapt(original, profile("b", output=-10.0), profile("a", output=-20.0))
        assert original == BASE

    def test_every_change_is_explained(self):
        _, notes = adapt(BASE, profile("b", output=-12.0, centroid=2400.0),
                         profile("a", output=-20.0, centroid=1200.0))
        assert len(notes) == 2
        assert all(" -> " in n for n in notes)

    def test_identical_guitars_produce_no_notes(self):
        a = profile("a")
        b = profile("b", output=a.output_dbfs, centroid=a.centroid_hz)
        assert adapt(BASE, b, a)[1] == []


class TestReferenceConsistency:
    def test_profiles_record_their_reference_amp(self):
        from lt25_mcp.guitar import REFERENCE_PRESET_AMP

        assert profile("strat").reference_amp == REFERENCE_PRESET_AMP

    def test_comparing_across_references_raises(self):
        from lt25_mcp.guitar import GuitarError

        a = profile("strat")
        b = profile("lp", output=-14.0)
        b.reference_amp = "DUBS_Jcm800"
        with pytest.raises(GuitarError, match="recapture"):
            adapt(BASE, b, a)

    def test_the_reference_amp_is_a_linear_model(self):
        """Saturation in the reference would distort every comparison."""
        from lt25_mcp.guitar import REFERENCE_PRESET_AMP

        assert REFERENCE_PRESET_AMP == "DUBS_LinearGain"
