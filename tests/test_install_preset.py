"""Tests for installing a preset file onto the amp."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("install_preset", SCRIPTS / "install_preset.py")
install_preset = importlib.util.module_from_spec(spec)
sys.modules["install_preset"] = install_preset
spec.loader.exec_module(install_preset)


class TestArguments:
    def test_slot_is_required(self, tmp_path):
        with pytest.raises(SystemExit):
            install_preset.main([str(tmp_path / "a.json")])

    def test_a_missing_file_is_reported_not_raised(self, tmp_path, capsys):
        assert install_preset.main([str(tmp_path / "nope.json"), "--slot", "31"]) == 1
        assert "error" in capsys.readouterr().err

    def test_malformed_json_is_reported_not_raised(self, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert install_preset.main([str(bad), "--slot", "31"]) == 1
        assert "error" in capsys.readouterr().err

    def test_a_preset_missing_its_graph_is_reported(self, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text('{"info": {"displayName": "X"}}')
        assert install_preset.main([str(bad), "--slot", "31"]) == 1
        assert "error" in capsys.readouterr().err


class TestGuardsAreNotBypassed:
    def test_it_uses_the_guarded_write(self):
        """A local write path would skip the slot range and backup checks."""
        source = (SCRIPTS / "install_preset.py").read_text()
        assert "from lt25_mcp.commands import" in source
        assert "write_preset" in source
        assert "savePresetAs" not in source, "must not build the message itself"

    def test_it_reads_the_slot_back(self):
        source = (SCRIPTS / "install_preset.py").read_text()
        assert source.count("read_preset") >= 2
