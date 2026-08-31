"""Tests for audio acquisition. No network: the runner is injected."""

import subprocess
from pathlib import Path

import pytest

from lt25_mcp.analysis.acquire import (
    SAMPLE_RATE,
    AcquisitionError,
    download_argv,
    fetch_audio,
    trim,
    trim_argv,
)


def ok(*_args, **_kwargs):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def fail(*_args, **_kwargs):
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")


class TestDownloadArgv:
    def test_uses_yt_dlp(self):
        assert download_argv("https://x", Path("/tmp/a.wav"))[0] == "yt-dlp"

    def test_requests_wav_mono_at_the_project_sample_rate(self):
        argv = download_argv("https://x", Path("/tmp/a.wav"))
        assert "wav" in argv
        joined = " ".join(argv)
        assert "-ac 1" in joined
        assert f"-ar {SAMPLE_RATE}" in joined

    def test_refuses_to_expand_playlists(self):
        assert "--no-playlist" in download_argv("https://x", Path("/tmp/a.wav"))

    def test_carries_the_url_last(self):
        assert download_argv("https://example/v", Path("/tmp/a.wav"))[-1] == "https://example/v"


class TestTrimArgv:
    def test_includes_start_and_end(self):
        argv = trim_argv(Path("a.wav"), 3.0, 12.0, Path("b.wav"))
        assert "-ss" in argv and "3.0" in argv
        assert "-to" in argv and "12.0" in argv

    def test_end_before_start_raises(self):
        with pytest.raises(AcquisitionError, match="must be after"):
            trim_argv(Path("a.wav"), 10, 5, Path("b.wav"))

    def test_equal_start_and_end_raises(self):
        with pytest.raises(AcquisitionError):
            trim_argv(Path("a.wav"), 5, 5, Path("b.wav"))


class TestFailuresAreLoud:
    def test_download_failure_raises(self, tmp_path):
        with pytest.raises(AcquisitionError, match="yt-dlp failed"):
            fetch_audio("https://x", tmp_path / "a.wav", runner=fail)

    def test_trim_failure_raises(self, tmp_path):
        with pytest.raises(AcquisitionError, match="ffmpeg failed"):
            trim(tmp_path / "a.wav", 0, 1, tmp_path / "b.wav", runner=fail)

    def test_success_returns_destination(self, tmp_path):
        dest = tmp_path / "out" / "a.wav"
        assert fetch_audio("https://x", dest, runner=ok) == dest
        assert dest.parent.is_dir()


class TestOpenEndedTrim:
    def test_no_end_omits_the_to_flag(self):
        argv = trim_argv(Path("a.wav"), 3.0, None, Path("b.wav"))
        assert "-ss" in argv
        assert "-to" not in argv

    def test_no_start_defaults_to_zero(self):
        argv = trim_argv(Path("a.wav"), None, 10.0, Path("b.wav"))
        assert argv[argv.index("-ss") + 1] == "0.0"

    def test_still_rejects_an_inverted_range(self):
        with pytest.raises(AcquisitionError, match="must be after"):
            trim_argv(Path("a.wav"), 10.0, 5.0, Path("b.wav"))
