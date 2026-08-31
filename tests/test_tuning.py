"""Tests for the complaint-to-move mapping and the tuning tool surface."""

import json

import pytest

from lt25_mcp import server as srv
from lt25_mcp.parameters import AMP_PARAMETERS, GATE_PRESETS, describe_parameters
from lt25_mcp.tuning import REMEDIES, catalogue, remedy_for
from helpers import call


class TestRemedies:
    def test_every_remedy_moves_a_real_parameter(self):
        for remedy in REMEDIES:
            for move in remedy.moves:
                assert move.control in AMP_PARAMETERS, move.control

    def test_every_move_has_a_direction_and_a_reason(self):
        for remedy in REMEDIES:
            assert remedy.moves
            for move in remedy.moves:
                assert move.delta != 0
                assert move.why

    def test_opposite_complaints_move_opposite_ways(self):
        dark = {m.control: m.delta for m in remedy_for("too dark").moves}
        harsh = {m.control: m.delta for m in remedy_for("too harsh").moves}
        assert dark["treb"] > 0 > harsh["treb"]

    def test_thin_and_muddy_disagree_about_bass(self):
        thin = {m.control: m.delta for m in remedy_for("too thin").moves}
        muddy = {m.control: m.delta for m in remedy_for("too muddy").moves}
        assert thin["bass"] > 0 > muddy["bass"]

    def test_loose_phrasing_matches(self):
        assert remedy_for("it is way too fizzy for me").complaint == "too fizzy"
        assert remedy_for("TOO DARK").complaint == "too dark"

    def test_unknown_complaint_returns_none(self):
        assert remedy_for("tastes purple") is None

    def test_catalogue_is_serializable(self):
        json.dumps(catalogue())


class TestParameterDescriptions:
    def test_describes_a_real_parameter_set(self, sample_preset):
        described = {d["name"]: d for d in describe_parameters(sample_preset.params("amp"))}
        assert described["gain"]["front_panel"] is True
        assert described["mid"]["front_panel"] is False
        assert described["gain"]["value_on_amp_scale"] == pytest.approx(3.4, abs=0.1)

    def test_enum_parameters_list_their_choices(self, sample_preset):
        described = {d["name"]: d for d in describe_parameters(sample_preset.params("amp"))}
        assert set(described["gatePreset"]["choices"]) == set(GATE_PRESETS)

    def test_undocumented_parameters_are_flagged_not_hidden(self):
        described = describe_parameters({"somethingNew": 0.5})
        assert described[0]["kind"] == "unknown"


class TestTuneTool:
    def test_knobs_use_the_amp_scale(self, stub_amp):
        result = call(srv.tune_preset)(json.dumps(stub_amp), knobs={"gain": 7.5})
        assert json.loads(result["preset_json"])["audioGraph"]["nodes"][2][
            "dspUnitParameters"
        ]["gain"] == pytest.approx(0.75)

    def test_reports_what_changed(self, stub_amp):
        result = call(srv.tune_preset)(json.dumps(stub_amp), knobs={"treb": 6.0})
        assert any("treb" in c for c in result["changes"])

    def test_does_not_mutate_the_input(self, stub_amp):
        before = json.dumps(stub_amp)
        call(srv.tune_preset)(before, knobs={"gain": 9.0})
        assert json.dumps(stub_amp) == before

    def test_swapping_the_amp_model_works(self, stub_amp):
        result = call(srv.tune_preset)(json.dumps(stub_amp), amp_model="DUBS_Deluxe65")
        assert json.loads(result["preset_json"])["audioGraph"]["nodes"][2][
            "FenderId"
        ] == "DUBS_Deluxe65"

    def test_an_out_of_range_knob_is_refused(self, stub_amp):
        with pytest.raises(ValueError, match="0..10"):
            call(srv.tune_preset)(json.dumps(stub_amp), knobs={"gain": 50})

    def test_a_knob_this_model_lacks_is_refused(self, stub_amp):
        with pytest.raises(ValueError, match="not a parameter"):
            call(srv.tune_preset)(json.dumps(stub_amp), knobs={"master": 5.0})

    def test_an_unknown_amp_model_is_refused(self, stub_amp):
        with pytest.raises(ValueError, match="unknown amp model"):
            call(srv.tune_preset)(json.dumps(stub_amp), amp_model="DUBS_Nope")

    def test_tune_does_not_touch_the_amp(self, stub_amp):
        before = len(srv.__dict__ and []) or 0
        from tests.test_server import StubSession

        n = len(StubSession.instances)
        call(srv.tune_preset)(json.dumps(stub_amp), knobs={"gain": 5.0})
        assert len(StubSession.instances) == n


class TestGuideTool:
    def test_targeted_lookup(self):
        result = call(srv.tuning_guide)("too fizzy")
        assert result["matched"] == "too fizzy"
        assert any(m["control"] == "treb" for m in result["moves"])

    def test_unmatched_falls_back_to_the_catalogue(self):
        result = call(srv.tuning_guide)("tastes purple")
        assert result["matched"] is None
        assert result["catalogue"]

    def test_no_complaint_returns_everything(self):
        assert len(call(srv.tuning_guide)()["catalogue"]) == len(REMEDIES)

    def test_structural_advice_is_always_offered(self):
        assert call(srv.tuning_guide)("too dark")["structural_advice"]


class TestDescribeTool:
    def test_reports_knobs_and_signal_path(self, stub_amp):
        result = call(srv.describe_preset)(json.dumps(stub_amp))
        assert "gain" in result["knobs_on_amp_scale"]
        assert "stomp" in result["signal_path"]

    def test_lists_every_effect_slot(self, stub_amp):
        result = call(srv.describe_preset)(json.dumps(stub_amp))
        assert set(result["effects"]) == {"stomp", "mod", "delay", "reverb"}


class TestAnalysisToolsExposed:
    """The point of the server is a clip-to-tone workflow; amp control alone
    leaves the analysis half reachable only from a shell."""

    def test_the_pipeline_is_reachable(self):
        import asyncio

        names = {t.name for t in asyncio.run(srv.server.list_tools())}
        assert {"analyse_clip", "measure_audio", "compare_to_target",
                "record_take"} <= names

    def test_measure_audio_reports_measurements(self, tmp_path):
        import math

        import numpy as np
        import soundfile as sf

        t = np.linspace(0, 1.5, 33075, endpoint=False)
        path = tmp_path / "a.wav"
        sf.write(path, (0.5 * np.sin(2 * math.pi * 440 * t)).astype(np.float32), 22050)
        out = call(srv.measure_audio)(str(path))
        assert "spectral_centroid_hz" in out["measurements"]
        assert "centroid" in out["summary"]

    def test_measure_audio_reports_a_missing_file_cleanly(self, tmp_path):
        with pytest.raises(ValueError, match="no such audio file"):
            call(srv.measure_audio)(str(tmp_path / "nope.wav"))

    def test_compare_needs_two_real_files(self, tmp_path):
        with pytest.raises(ValueError, match="no such audio file"):
            call(srv.compare_to_target)(str(tmp_path / "a.wav"), str(tmp_path / "b.wav"))

    def test_compare_reports_moves_and_the_playing_caveat(self, tmp_path):
        import math

        import numpy as np
        import soundfile as sf

        t = np.linspace(0, 1.5, 33075, endpoint=False)
        for name, freq in (("a.wav", 300), ("b.wav", 2500)):
            sf.write(tmp_path / name,
                     (0.5 * np.sin(2 * math.pi * freq * t)).astype(np.float32), 22050)
        out = call(srv.compare_to_target)(str(tmp_path / "a.wav"), str(tmp_path / "b.wav"))
        assert out["distance"] > 0
        assert out["moves"]
        assert "playing" in out["caveat"]

    def test_analyse_clip_refuses_both_url_and_path(self, stub_amp, tmp_path):
        with pytest.raises(ValueError, match="exactly one"):
            call(srv.analyse_clip)(json.dumps(stub_amp), url="https://x",
                                   audio_path=str(tmp_path / "a.wav"))

    def test_analysis_tools_never_open_the_amp(self, stub_amp, tmp_path):
        """Analysis must not contend for the amp's single control channel."""
        import math

        import numpy as np
        import soundfile as sf
        from helpers import StubSession

        t = np.linspace(0, 1.5, 33075, endpoint=False)
        path = tmp_path / "a.wav"
        sf.write(path, (0.5 * np.sin(2 * math.pi * 440 * t)).astype(np.float32), 22050)
        before = len(StubSession.instances)
        call(srv.measure_audio)(str(path))
        assert len(StubSession.instances) == before
