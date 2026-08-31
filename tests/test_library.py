"""Tests for reading presets off the amp and backing up the whole library."""

import json
from pathlib import Path

import pytest

from lt25_mcp.library import (
    SLOT_MAX,
    SLOT_MIN,
    WRITABLE_MIN,
    SlotError,
    backup_all,
    latest_backup,
    read_preset,
)


class FakeSession:
    """Answers retrievePreset with a minimal but structurally real preset."""

    def __init__(self, fail_on=None):
        self.requested = []
        self._fail_on = fail_on

    def request(self, *, expect=None, timeout_ms=3000, **payload):
        slot = payload["retrievePreset"]["slot"]
        self.requested.append(slot)
        if slot == self._fail_on:
            raise TimeoutError(f"slot {slot} did not answer")
        preset = {
            "nodeType": "preset",
            "nodeId": "preset",
            "version": "1.1",
            "info": {"displayName": f"SLOT {slot:02d}".ljust(16)},
            "audioGraph": {"nodes": [], "connections": []},
        }

        class Reply:
            class presetJSONMessage:
                data = json.dumps(preset)
                slotIndex = slot

        return Reply

    def firmware_version(self):
        return "2.1.4"

    def product_id(self):
        return "mustang-lt-25"


class TestSlotBounds:
    def test_constants(self):
        assert (SLOT_MIN, SLOT_MAX, WRITABLE_MIN) == (1, 60, 31)

    @pytest.mark.parametrize("slot", [0, -1, 61, 999])
    def test_out_of_range_slot_raises(self, slot):
        with pytest.raises(SlotError, match="1..60"):
            read_preset(FakeSession(), slot)

    def test_non_integer_slot_raises(self):
        with pytest.raises(SlotError):
            read_preset(FakeSession(), "12")


class TestReadPreset:
    def test_returns_parsed_preset(self):
        preset = read_preset(FakeSession(), 5)
        assert preset["info"]["displayName"].startswith("SLOT 05")
        assert preset["version"] == "1.1"

    def test_boundary_slots_are_allowed(self):
        assert read_preset(FakeSession(), 1)
        assert read_preset(FakeSession(), 60)


class TestBackup:
    def test_writes_every_slot(self, tmp_path):
        session = FakeSession()
        out = backup_all(session, tmp_path)
        assert session.requested == list(range(1, 61))
        assert len(list(out.glob("slot-*.json"))) == 60

    def test_manifest_records_provenance(self, tmp_path):
        out = backup_all(FakeSession(), tmp_path)
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["slot_count"] == 60
        assert manifest["firmware_version"] == "2.1.4"
        assert manifest["product_id"] == "mustang-lt-25"
        assert manifest["created_at"]

    def test_slot_files_are_named_with_zero_padding(self, tmp_path):
        out = backup_all(FakeSession(), tmp_path)
        assert (out / "slot-01.json").exists()
        assert (out / "slot-60.json").exists()

    def test_partial_backup_is_not_left_behind(self, tmp_path):
        """A backup that fails halfway must not look like a valid restore point."""
        with pytest.raises(Exception):
            backup_all(FakeSession(fail_on=30), tmp_path)
        assert latest_backup(tmp_path) is None


class TestLatestBackup:
    def test_none_when_empty(self, tmp_path):
        assert latest_backup(tmp_path) is None

    def test_finds_the_backup(self, tmp_path):
        out = backup_all(FakeSession(), tmp_path)
        assert latest_backup(tmp_path) == out

    def test_ignores_incomplete_backups(self, tmp_path):
        out = backup_all(FakeSession(), tmp_path)
        (out / "slot-42.json").unlink()
        assert latest_backup(tmp_path) is None

    def test_picks_most_recent_of_several(self, tmp_path):
        first = backup_all(FakeSession(), tmp_path)
        second = backup_all(FakeSession(), tmp_path)
        assert first != second
        assert latest_backup(tmp_path) == second


class TestLoadBackup:
    def test_reads_every_slot(self, tmp_path):
        from lt25_mcp.library import load_backup

        out = backup_all(FakeSession(), tmp_path)
        loaded = load_backup(out)
        assert sorted(loaded) == list(range(1, 61))
        assert loaded[5]["info"]["displayName"].startswith("SLOT 05")

    def test_incomplete_backup_raises(self, tmp_path):
        from lt25_mcp.library import load_backup

        out = backup_all(FakeSession(), tmp_path)
        (out / "slot-07.json").unlink()
        with pytest.raises(SlotError, match="not a complete"):
            load_backup(out)


class TestBackupOrdering:
    def test_double_digit_suffix_sorts_after_single_digit(self, tmp_path):
        """backup-X-10 must not sort before backup-X-2."""
        from lt25_mcp.library import _backup_sort_key

        names = ["backup-20260101T000000Z", "backup-20260101T000000Z-2",
                 "backup-20260101T000000Z-10", "backup-20260101T000000Z-3"]
        ordered = sorted(names, key=lambda n: _backup_sort_key(Path(n)))
        assert ordered[-1].endswith("-10")

    def test_latest_picks_the_highest_suffix(self, tmp_path):
        """Eleven backups in one second: the 11th must win, not the 2nd."""
        made = [backup_all(FakeSession(), tmp_path) for _ in range(11)]
        assert latest_backup(tmp_path) == made[-1]

    def test_a_later_timestamp_beats_a_higher_suffix(self, tmp_path):
        from lt25_mcp.library import _backup_sort_key

        older = Path("backup-20260101T000000Z-10")
        newer = Path("backup-20260101T000001Z")
        assert _backup_sort_key(newer) > _backup_sort_key(older)
