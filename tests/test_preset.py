"""Tests for the preset model.

Fixtures are real presets read off a Mustang LT 25, so they are the spec:
if validation rejects them, validation is wrong.
"""

import json
from pathlib import Path

import pytest

from lt25_mcp.dsp_catalog import AMP_MODELS, EFFECTS, PASSTHRU
from lt25_mcp.preset import Preset, PresetError

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Preset:
    return Preset.from_dict(json.loads((FIXTURES / name).read_text()))


class TestRoundTrip:
    @pytest.mark.parametrize(
        "name", ["clean.json", "high_gain.json", "empty.json", "classic_rock.json"]
    )
    def test_real_presets_survive_a_round_trip(self, name):
        raw = json.loads((FIXTURES / name).read_text())
        assert Preset.from_dict(raw).to_dict() == raw

    def test_classic_rock_negative_mid_is_accepted(self):
        """Real factory data carries mid=-4.4; a 0..1 assumption would reject it."""
        assert load("classic_rock.json").node("amp")["dspUnitParameters"]["mid"] == -4.400125

    def test_integer_valued_parameter_is_preserved(self):
        """CLASSIC ROCK stores treb as an int, not a float."""
        assert load("classic_rock.json").to_dict()["audioGraph"]["nodes"][2][
            "dspUnitParameters"
        ]["treb"] == 1


class TestDisplayName:
    def test_reads_stripped(self):
        assert load("clean.json").display_name == "FENDER  CLEAN"

    def test_setting_pads_to_16(self):
        p = load("clean.json")
        p.display_name = "JAZZ"
        assert p.to_dict()["info"]["displayName"] == "JAZZ" + " " * 12

    def test_exactly_16_is_allowed(self):
        p = load("clean.json")
        p.display_name = "A" * 16
        assert p.to_dict()["info"]["displayName"] == "A" * 16

    def test_over_16_raises(self):
        with pytest.raises(PresetError, match="16"):
            load("clean.json").display_name = "A" * 17

    def test_non_ascii_raises(self):
        with pytest.raises(PresetError, match="ASCII"):
            load("clean.json").display_name = "CAFÉ"


class TestClone:
    def test_clone_is_independent(self):
        original = load("clean.json")
        copy = original.clone()
        copy.set_param("amp", "gain", 0.9)
        assert original.node("amp")["dspUnitParameters"]["gain"] != 0.9

    def test_clone_equals_original(self):
        original = load("clean.json")
        assert original.clone().to_dict() == original.to_dict()


class TestSetParam:
    def test_sets_an_existing_parameter(self):
        p = load("clean.json")
        p.set_param("amp", "gain", 0.75)
        assert p.node("amp")["dspUnitParameters"]["gain"] == 0.75

    def test_unknown_parameter_raises(self):
        with pytest.raises(PresetError, match="not a parameter"):
            load("clean.json").set_param("amp", "nonsense", 0.5)

    def test_parameter_from_a_different_model_raises(self):
        """TWIN CLEAN has no 'master'; METAL LEAD does. Do not invent one."""
        with pytest.raises(PresetError, match="not a parameter"):
            load("clean.json").set_param("amp", "master", 0.5)

    def test_type_change_raises(self):
        with pytest.raises(PresetError, match="expects"):
            load("clean.json").set_param("amp", "gain", "loud")

    def test_bool_parameter_accepts_bool(self):
        p = load("clean.json")
        p.set_param("amp", "bright", False)
        assert p.node("amp")["dspUnitParameters"]["bright"] is False

    def test_string_parameter_accepts_string(self):
        p = load("clean.json")
        p.set_param("amp", "gatePreset", "low")
        assert p.node("amp")["dspUnitParameters"]["gatePreset"] == "low"

    def test_int_parameter_accepts_float(self):
        """treb is stored as int 1 but is a continuous control."""
        p = load("classic_rock.json")
        p.set_param("amp", "treb", 0.5)
        assert p.node("amp")["dspUnitParameters"]["treb"] == 0.5

    def test_unknown_node_raises(self):
        with pytest.raises(PresetError, match="no node"):
            load("clean.json").set_param("flanger", "gain", 0.5)


class TestAmpModel:
    def test_reads_fender_id(self):
        assert load("clean.json").amp_model == "DUBS_Twin65"

    def test_label_lookup(self):
        assert load("clean.json").amp_label == "TWIN CLEAN"

    def test_unknown_model_raises(self):
        with pytest.raises(PresetError, match="unknown amp model"):
            load("clean.json").amp_model = "DUBS_NotAnAmp"

    def test_switching_model_replaces_parameters(self):
        """Each model has its own parameter set; carrying the old one over is wrong."""
        p = load("clean.json")
        p.amp_model = "DUBS_MetalRect2"
        params = p.node("amp")["dspUnitParameters"]
        assert p.amp_model == "DUBS_MetalRect2"
        assert "bright" not in params  # belonged to Twin65


class TestEffects:
    def test_empty_slot_is_passthru(self):
        assert load("empty.json").node("mod")["FenderId"] == PASSTHRU

    def test_has_effect_reports_occupancy(self):
        assert load("empty.json").has_effect("mod") is False
        assert load("clean.json").has_effect("reverb") is True

    def test_effects_listed_per_category(self):
        assert PASSTHRU not in EFFECTS["reverb"]
        assert "DUBS_Spring65" in EFFECTS["reverb"]
        assert "DUBS_Spring65" not in EFFECTS["delay"]


class TestCatalogCoverage:
    def test_every_fixture_amp_is_catalogued(self):
        for path in FIXTURES.glob("*.json"):
            amp = Preset.from_dict(json.loads(path.read_text())).amp_model
            assert amp in AMP_MODELS, f"{path.name} uses uncatalogued amp {amp}"

    def test_labels_are_unique(self):
        labels = list(AMP_MODELS.values())
        assert len(labels) == len(set(labels))


class TestEnumValidation:
    def test_a_real_cabsim_is_accepted(self):
        p = load("clean.json")
        p.set_param("amp", "cabsimType", "4x12v")
        assert p.params("amp")["cabsimType"] == "4x12v"

    def test_an_invented_cabsim_is_rejected(self):
        with pytest.raises(PresetError, match="cabsimType"):
            load("clean.json").set_param("amp", "cabsimType", "banana")

    def test_an_invented_gate_preset_is_rejected(self):
        with pytest.raises(PresetError, match="gatePreset"):
            load("clean.json").set_param("amp", "gatePreset", "maximum")

    def test_every_gate_preset_is_accepted(self):
        from lt25_mcp.parameters import GATE_PRESETS

        for value in GATE_PRESETS:
            p = load("clean.json")
            p.set_param("amp", "gatePreset", value)
            assert p.params("amp")["gatePreset"] == value

    def test_unconstrained_enums_stay_permissive(self):
        """Only one value of `sag` has ever been observed; do not lock to it."""
        p = load("clean.json")
        p.set_param("amp", "sag", "something-else")
        assert p.params("amp")["sag"] == "something-else"

    def test_every_fixture_value_passes_its_own_validation(self):
        """Validation must accept the amp's own factory data."""
        for path in FIXTURES.glob("*.json"):
            p = Preset.from_dict(json.loads(path.read_text()))
            for name, value in list(p.params("amp").items()):
                p.set_param("amp", name, value)


class TestKnobScale:
    def test_set_knob_uses_the_amp_scale(self):
        p = load("clean.json")
        p.set_knob("gain", 7.5)
        assert p.params("amp")["gain"] == pytest.approx(0.75)

    def test_get_knob_reads_back_on_the_amp_scale(self):
        p = load("clean.json")
        p.set_knob("treb", 6.0)
        assert p.knob("treb") == pytest.approx(6.0)

    def test_out_of_range_knob_raises(self):
        with pytest.raises(PresetError, match="0..10"):
            load("clean.json").set_knob("gain", 11.0)

    def test_negative_knob_raises(self):
        with pytest.raises(PresetError, match="0..10"):
            load("clean.json").set_knob("gain", -1.0)

    def test_knob_absent_on_this_model_raises(self):
        with pytest.raises(PresetError, match="not a parameter"):
            load("clean.json").set_knob("master", 5.0)

    def test_knobs_lists_what_this_model_exposes(self):
        knobs = load("clean.json").knobs()
        assert "gain" in knobs and "treb" in knobs
        assert "master" not in knobs  # Twin65 has no master
        assert all(0.0 <= v <= 10.0 for v in knobs.values())
