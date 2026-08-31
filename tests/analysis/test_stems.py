"""Tests for stem isolation. The runner is injected; demucs never runs."""

import subprocess

import pytest

from lt25_mcp.analysis.stems import (
    GUITAR_STEM,
    MODEL,
    StemError,
    isolate_guitar,
    separate_argv,
    stem_path,
)


def ok(*_a, **_k):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def fail(*_a, **_k):
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no model")


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF")
    return path


class TestArgv:
    def test_selects_the_six_source_model(self, audio, tmp_path):
        argv = separate_argv(audio, tmp_path)
        assert argv[0] == "demucs"
        assert MODEL in argv

    def test_stem_path_follows_demucs_layout(self, audio, tmp_path):
        assert stem_path(tmp_path, audio, GUITAR_STEM) == (
            tmp_path / MODEL / "clip" / "guitar.wav"
        )


class TestIsolate:
    def _write_stem(self, tmp_path, audio, name):
        path = stem_path(tmp_path / "out", audio, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF")
        return path

    def test_returns_the_guitar_stem(self, audio, tmp_path):
        expected = self._write_stem(tmp_path, audio, GUITAR_STEM)
        assert isolate_guitar(audio, tmp_path / "out", runner=ok) == expected

    def test_missing_source_raises(self, tmp_path):
        with pytest.raises(StemError, match="no such audio file"):
            isolate_guitar(tmp_path / "nope.wav", tmp_path / "out", runner=ok)

    def test_demucs_failure_raises(self, audio, tmp_path):
        with pytest.raises(StemError, match="demucs failed"):
            isolate_guitar(audio, tmp_path / "out", runner=fail)

    def test_missing_guitar_stem_raises_rather_than_guessing(self, audio, tmp_path):
        self._write_stem(tmp_path, audio, "other")
        with pytest.raises(StemError, match="no guitar stem"):
            isolate_guitar(audio, tmp_path / "out", runner=ok)

    def test_fallback_is_opt_in(self, audio, tmp_path):
        expected = self._write_stem(tmp_path, audio, "other")
        got = isolate_guitar(audio, tmp_path / "out", allow_fallback=True, runner=ok)
        assert got == expected
