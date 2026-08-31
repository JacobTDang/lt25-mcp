"""Tests for the MCP tool surface.

Hardware is stubbed by patching the session factory; these tests check the
shape of what tools return, not the protocol.
"""

import json

import pytest

from lt25_mcp import server as srv
from lt25_mcp.dsp_catalog import AMP_MODELS, PASSTHRU


class StubSession:
    """Stands in for a live amp session."""

    instances = []

    def __init__(self, preset_raw):
        self._raw = preset_raw
        self.calls = []
        self.closed = False
        StubSession.instances.append(self)

    def request(self, *, expect=None, timeout_ms=3000, **payload):
        kind = next(iter(payload))
        self.calls.append(kind)
        raw = self._raw
        outer = self

        class Reply:
            class presetJSONMessage:
                data = json.dumps(raw)
                slotIndex = payload.get("retrievePreset", {}).get("slot", 0)

            class auditionStateStatus:
                isAuditioning = "auditionPreset" in outer.calls and (
                    "exitAuditionPreset" not in outer.calls
                )

            class firmwareVersionStatus:
                version = "2.1.4"

            class productIdentificationStatus:
                id = "mustang-lt-25"

        return Reply

    def firmware_version(self):
        return "2.1.4"

    def product_id(self):
        return "mustang-lt-25"

    def open(self):
        return self

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True


@pytest.fixture
def stub_amp(monkeypatch, sample_preset):
    StubSession.instances.clear()
    # A held audition session must not leak between tests.
    monkeypatch.setattr(srv, "_held", None)
    raw = sample_preset.to_dict()
    monkeypatch.setattr(srv, "_session", lambda: StubSession(raw))
    yield raw
    srv._held = None


def call(tool):
    """FastMCP-style decorators keep the original function on .fn."""
    return getattr(tool, "fn", tool)


class TestReadTools:
    def test_get_preset_returns_summary_and_raw(self, stub_amp):
        result = call(srv.get_preset)(1)
        assert result["slot"] == 1
        assert result["amp_model"] == "DUBS_Twin65"
        assert result["amp_label"] == "TWIN CLEAN"
        assert "summary" in result
        assert result["raw"] == stub_amp

    def test_get_preset_reports_effects_by_label(self, stub_amp):
        result = call(srv.get_preset)(1)
        assert PASSTHRU not in result["effects"].values()

    def test_list_presets_covers_all_sixty(self, stub_amp):
        assert len(call(srv.list_presets)()) == 60

    def test_amp_status_reports_firmware(self, stub_amp):
        status = call(srv.amp_status)()
        assert status["firmware_version"] == "2.1.4"
        assert status["product_id"] == "mustang-lt-25"


class TestCatalogTools:
    def test_amp_models_listed(self):
        assert call(srv.list_amp_models)()["DUBS_Twin65"] == "TWIN CLEAN"
        assert len(call(srv.list_amp_models)()) == len(AMP_MODELS)

    def test_effects_grouped_by_slot(self):
        effects = call(srv.list_effects)()
        assert set(effects) == {"stomp", "mod", "delay", "reverb"}
        assert "DUBS_Spring65" in effects["reverb"]


class TestAudition:
    def test_audition_sends_preset(self, stub_amp):
        result = call(srv.audition_preset)(json.dumps(stub_amp))
        assert result["auditioning"] is True
        assert "auditionPreset" in StubSession.instances[-1].calls

    def test_audition_rejects_malformed_json(self, stub_amp):
        with pytest.raises(json.JSONDecodeError):
            call(srv.audition_preset)("not json")


class TestSessionHygiene:
    def test_every_tool_closes_its_session(self, stub_amp):
        call(srv.get_preset)(1)
        assert StubSession.instances[-1].closed

    def test_tools_do_not_share_a_session(self, stub_amp):
        call(srv.get_preset)(1)
        call(srv.get_preset)(2)
        assert len(StubSession.instances) == 2


class TestAuditionHoldsTheSession:
    """The amp drops a session that stops sending heartbeats, so an audition
    only survives while its session is held open."""

    def test_audition_leaves_the_session_open(self, stub_amp):
        call(srv.audition_preset)(json.dumps(stub_amp))
        assert StubSession.instances[-1].closed is False

    def test_stop_audition_closes_the_held_session(self, stub_amp):
        call(srv.audition_preset)(json.dumps(stub_amp))
        held = StubSession.instances[-1]
        call(srv.stop_audition)()
        assert held.closed is True
        assert "exitAuditionPreset" in held.calls

    def test_a_second_audition_replaces_the_first(self, stub_amp):
        call(srv.audition_preset)(json.dumps(stub_amp))
        first = StubSession.instances[-1]
        call(srv.audition_preset)(json.dumps(stub_amp))
        assert first.closed is True
        assert StubSession.instances[-1] is not first
        assert StubSession.instances[-1].closed is False

    def test_other_tools_reuse_the_held_session(self, stub_amp):
        """Only one program can hold the amp, so tools must not open a second."""
        call(srv.audition_preset)(json.dumps(stub_amp))
        held = StubSession.instances[-1]
        before = len(StubSession.instances)
        call(srv.get_preset)(1)
        assert len(StubSession.instances) == before
        assert held.closed is False

    def test_other_tools_do_not_close_the_held_session(self, stub_amp):
        call(srv.audition_preset)(json.dumps(stub_amp))
        held = StubSession.instances[-1]
        call(srv.get_preset)(1)
        assert held.closed is False

    def test_stop_audition_with_nothing_held_still_works(self, stub_amp):
        result = call(srv.stop_audition)()
        assert result["auditioning"] is False
        assert StubSession.instances[-1].closed is True

    def test_normal_tools_still_close_when_nothing_is_held(self, stub_amp):
        call(srv.get_preset)(1)
        assert StubSession.instances[-1].closed is True
