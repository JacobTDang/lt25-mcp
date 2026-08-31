"""Tests for the slot-range parser used by the restore script."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
# The script imports _bootstrap, which lives beside it and is only importable
# when the script's own directory is on the path - as it is when run directly.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("restore", SCRIPTS / "restore.py")
restore = importlib.util.module_from_spec(spec)
sys.modules["restore"] = restore
spec.loader.exec_module(restore)


class TestParseSlots:
    def test_single(self):
        assert restore.parse_slots("60") == [60]

    def test_range(self):
        assert restore.parse_slots("31-34") == [31, 32, 33, 34]

    def test_list(self):
        assert restore.parse_slots("31,35,60") == [31, 35, 60]

    def test_mixed_and_deduplicated(self):
        assert restore.parse_slots("31-33,32,60") == [31, 32, 33, 60]

    def test_whitespace_tolerated(self):
        assert restore.parse_slots(" 31 , 60 ") == [31, 60]

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            restore.parse_slots("sixty")
