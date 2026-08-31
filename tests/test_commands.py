"""Tests for auditioning and writing presets."""

import pytest

from lt25_mcp.commands import (
    WriteRefused,
    audition,
    audition_scope,
    exit_audition,
    write_preset,
)


class RecordingSession:
    """Records which message kinds were sent, answering everything."""

    def __init__(self, verify_preset=None):
        self.calls = []
        self.payloads = []
        self._verify_preset = verify_preset

    def request(self, *, expect=None, timeout_ms=3000, **payload):
        kind = next(iter(payload))
        self.calls.append(kind)
        self.payloads.append(payload[kind])

        outer = self

        class Reply:
            class auditionPresetStatus:
                pass

            class presetJSONMessage:
                import json as _json

                data = _json.dumps(outer._verify_preset or {})
                slotIndex = 0

        return Reply


class TestAudition:
    def test_audition_sends_preset_data(self, sample_preset):
        session = RecordingSession()
        audition(session, sample_preset)
        assert session.calls == ["auditionPreset"]
        assert "presetData" in session.payloads[0]

    def test_audition_sends_serialized_json(self, sample_preset):
        import json

        session = RecordingSession()
        audition(session, sample_preset)
        sent = json.loads(session.payloads[0]["presetData"])
        assert sent["info"]["displayName"] == sample_preset.to_dict()["info"]["displayName"]

    def test_exit_audition(self):
        session = RecordingSession()
        exit_audition(session)
        assert session.calls == ["exitAuditionPreset"]

    def test_scope_exits_on_success(self, sample_preset):
        session = RecordingSession()
        with audition_scope(session, sample_preset):
            pass
        assert session.calls == ["auditionPreset", "exitAuditionPreset"]

    def test_scope_exits_on_exception(self, sample_preset):
        session = RecordingSession()
        with pytest.raises(RuntimeError):
            with audition_scope(session, sample_preset):
                raise RuntimeError("boom")
        assert session.calls == ["auditionPreset", "exitAuditionPreset"]


class TestWriteGuards:
    @pytest.mark.parametrize("slot", [1, 15, 30])
    def test_refuses_factory_slots(self, sample_preset, fake_backup, slot):
        with pytest.raises(WriteRefused, match="31..60"):
            write_preset(
                RecordingSession(), sample_preset, slot, backup_root=fake_backup.parent
            )

    @pytest.mark.parametrize("slot", [0, -1, 61, 100])
    def test_refuses_invalid_slots(self, sample_preset, fake_backup, slot):
        with pytest.raises(WriteRefused):
            write_preset(
                RecordingSession(), sample_preset, slot, backup_root=fake_backup.parent
            )

    def test_refuses_without_a_backup(self, sample_preset, tmp_path):
        with pytest.raises(WriteRefused, match="no complete backup"):
            write_preset(RecordingSession(), sample_preset, 47, backup_root=tmp_path)

    def test_refuses_with_an_incomplete_backup(self, sample_preset, fake_backup):
        (fake_backup / "slot-42.json").unlink()
        with pytest.raises(WriteRefused, match="no complete backup"):
            write_preset(
                RecordingSession(), sample_preset, 47, backup_root=fake_backup.parent
            )


class TestWrite:
    def test_writes_to_a_permitted_slot(self, sample_preset, fake_backup):
        session = RecordingSession(verify_preset=sample_preset.to_dict())
        write_preset(session, sample_preset, 47, backup_root=fake_backup.parent)
        assert "savePresetAs" in session.calls

    def test_sends_the_target_slot(self, sample_preset, fake_backup):
        session = RecordingSession(verify_preset=sample_preset.to_dict())
        write_preset(session, sample_preset, 47, backup_root=fake_backup.parent)
        payload = session.payloads[session.calls.index("savePresetAs")]
        assert payload["presetSlot"] == 47

    def test_verifies_by_reading_back(self, sample_preset, fake_backup):
        """A write that does not land must fail loudly, not silently succeed."""
        session = RecordingSession(verify_preset={"info": {"displayName": "SOMETHING ELSE"}})
        with pytest.raises(WriteRefused, match="did not match"):
            write_preset(session, sample_preset, 47, backup_root=fake_backup.parent)

    def test_read_back_happens_after_the_write(self, sample_preset, fake_backup):
        session = RecordingSession(verify_preset=sample_preset.to_dict())
        write_preset(session, sample_preset, 47, backup_root=fake_backup.parent)
        assert session.calls.index("savePresetAs") < session.calls.index("retrievePreset")
