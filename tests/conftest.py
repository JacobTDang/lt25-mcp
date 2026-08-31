import json
from pathlib import Path

import pytest

from helpers import StubSession

from lt25_mcp import server as srv
from lt25_mcp.preset import Preset

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_preset() -> Preset:
    return Preset.from_dict(json.loads((FIXTURES / "clean.json").read_text()))


@pytest.fixture
def fake_backup(tmp_path) -> Path:
    """A structurally complete backup, so write guards are satisfied."""
    backup = tmp_path / "backup-20260101T000000Z"
    backup.mkdir()
    for slot in range(1, 61):
        (backup / f"slot-{slot:02d}.json").write_text("{}")
    (backup / "manifest.json").write_text(json.dumps({"slot_count": 60}))
    return backup


@pytest.fixture
def stub_amp(monkeypatch, sample_preset):
    StubSession.instances.clear()
    # A held audition session must not leak between tests.
    monkeypatch.setattr(srv, "_held", None)
    raw = sample_preset.to_dict()
    monkeypatch.setattr(srv, "_session", lambda: StubSession(raw))
    yield raw
    srv._held = None

