"""Tests for the rig profile and its effect on a preset."""

import json

import pytest

from lt25_mcp.rig import (
    PEDAL_SLOTS,
    PICKUP_ADJUSTMENTS,
    PICKUP_RATIONALE,
    PICKUP_TYPES,
    Rig,
    RigError,
    adjust_for_rig,
    slots_to_leave_empty,
)


class TestValidation:
    def test_defaults_to_unknown_and_no_pedals(self):
        rig = Rig()
        assert rig.pickups == "unknown"
        assert rig.pedals == []

    def test_rejects_an_invented_pickup_type(self):
        with pytest.raises(RigError, match="not a known pickup type"):
            Rig(pickups="magnetic-ish")

    def test_rejects_an_invented_pedal_kind(self):
        with pytest.raises(RigError, match="unknown pedal kinds"):
            Rig(pedals=["theremin"])

    @pytest.mark.parametrize("pickups", PICKUP_TYPES)
    def test_every_declared_type_is_constructible(self, pickups):
        assert Rig(pickups=pickups).pickups == pickups


class TestPersistence:
    def test_round_trips_through_disk(self, tmp_path):
        rig = Rig(pickups="humbucker", guitar="Epiphone LP", pedals=["overdrive"])
        path = rig.save(tmp_path / "rig.json")
        assert Rig.load(path).to_dict() == rig.to_dict()

    def test_missing_file_gives_a_blank_rig(self, tmp_path):
        assert Rig.load(tmp_path / "nope.json").pickups == "unknown"

    def test_unknown_keys_in_a_saved_file_are_ignored(self, tmp_path):
        path = tmp_path / "rig.json"
        path.write_text(json.dumps({"pickups": "humbucker", "fromTheFuture": 1}))
        assert Rig.load(path).pickups == "humbucker"


class TestPickupAdjustment:
    BASE = {"gain": 5.0, "treb": 5.0, "mid": 5.0, "bass": 5.0}

    def test_unknown_pickups_change_nothing(self):
        adjusted, why = adjust_for_rig(self.BASE, Rig())
        assert adjusted == self.BASE
        assert why == []

    def test_humbuckers_need_less_gain_than_single_coils(self):
        hb, _ = adjust_for_rig(self.BASE, Rig(pickups="humbucker"))
        sc, _ = adjust_for_rig(self.BASE, Rig(pickups="single_coil"))
        assert hb["gain"] < self.BASE["gain"] < sc["gain"]

    def test_adjustment_is_explained(self):
        _, why = adjust_for_rig(self.BASE, Rig(pickups="humbucker"))
        assert why and "humbucker" in why[0]

    def test_knobs_stay_in_range(self):
        for pickups in PICKUP_TYPES:
            for extreme in ({"gain": 0.0, "treb": 0.0}, {"gain": 10.0, "treb": 10.0}):
                adjusted, _ = adjust_for_rig(extreme, Rig(pickups=pickups))
                assert all(0.0 <= v <= 10.0 for v in adjusted.values())

    def test_absent_controls_are_left_alone(self):
        adjusted, _ = adjust_for_rig({"mid": 5.0}, Rig(pickups="humbucker"))
        assert adjusted == {"mid": 5.0}

    def test_every_pickup_type_has_a_rationale(self):
        for pickups in PICKUP_TYPES:
            assert PICKUP_RATIONALE[pickups]
            assert pickups in PICKUP_ADJUSTMENTS

    def test_input_is_not_mutated(self):
        original = dict(self.BASE)
        adjust_for_rig(original, Rig(pickups="humbucker"))
        assert original == self.BASE


class TestPedalAwareness:
    def test_no_pedals_leaves_every_slot_available(self):
        assert slots_to_leave_empty(Rig()) == {}

    def test_a_real_overdrive_frees_the_stomp_slot(self):
        advice = slots_to_leave_empty(Rig(pedals=["overdrive"]))
        assert advice["stomp"]
        assert "overdrive" in advice["stomp"]

    def test_a_real_reverb_frees_the_reverb_slot(self):
        assert "reverb" in slots_to_leave_empty(Rig(pedals=["reverb"]))

    def test_occupied_slots_are_deduplicated(self):
        assert Rig(pedals=["overdrive", "distortion"]).occupied_slots == {"stomp"}

    def test_every_pedal_kind_maps_to_a_real_slot(self):
        from lt25_mcp.dsp_catalog import EFFECTS

        assert set(PEDAL_SLOTS.values()) <= set(EFFECTS)


class TestDescribe:
    def test_mentions_pickups_and_pedals(self):
        text = Rig(pickups="humbucker", pedals=["overdrive"]).describe()
        assert "humbucker" in text
        assert "overdrive" in text

    def test_says_none_when_there_are_no_pedals(self):
        assert "none" in Rig().describe()
