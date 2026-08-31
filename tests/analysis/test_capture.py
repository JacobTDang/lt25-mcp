"""Tests for USB audio capture. ffmpeg is never actually run."""

import subprocess

import pytest

from lt25_mcp.analysis.capture import (
    CaptureError,
    capture_argv,
    find_amp_device,
    parse_devices,
    record,
)

LISTING = """\
[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] FaceTime HD Camera
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] iPhone Microphone
[AVFoundation indev @ 0x1] [1] MacBook Pro Microphone
[AVFoundation indev @ 0x1] [2] Mustang LT 25
"""


def runner_for(stderr, returncode=1):
    def run(_argv):
        return subprocess.CompletedProcess([], returncode, stdout="", stderr=stderr)
    return run


class TestParseDevices:
    def test_only_audio_devices_are_returned(self):
        devices = parse_devices(LISTING)
        assert devices == {0: "iPhone Microphone", 1: "MacBook Pro Microphone",
                           2: "Mustang LT 25"}

    def test_video_devices_sharing_an_index_do_not_leak_in(self):
        assert parse_devices(LISTING)[0] == "iPhone Microphone"

    def test_empty_output_gives_nothing(self):
        assert parse_devices("") == {}


class TestFindAmp:
    def test_finds_the_amp_by_name(self):
        assert find_amp_device(runner=runner_for(LISTING)) == (2, "Mustang LT 25")

    def test_absent_amp_raises_and_lists_what_is_there(self):
        without = LISTING.replace("[2] Mustang LT 25\n", "")
        with pytest.raises(CaptureError) as exc:
            find_amp_device(runner=runner_for(without))
        assert "MacBook Pro Microphone" in str(exc.value)

    def test_no_devices_at_all_raises(self):
        with pytest.raises(CaptureError, match="none"):
            find_amp_device(runner=runner_for(""))


class TestCaptureArgv:
    def test_targets_the_device_index(self):
        argv = capture_argv(2, 20.0, "out.wav")
        assert ":2" in argv
        assert "avfoundation" in argv

    def test_records_mono_for_the_requested_duration(self):
        argv = capture_argv(2, 12.5, "out.wav")
        assert argv[argv.index("-t") + 1] == "12.5"
        assert argv[argv.index("-ac") + 1] == "1"


class TestRecord:
    def test_zero_duration_raises(self, tmp_path):
        with pytest.raises(CaptureError, match="positive"):
            record(tmp_path / "a.wav", seconds=0, device_index=2, runner=runner_for("", 0))

    def test_ffmpeg_failure_raises(self, tmp_path):
        with pytest.raises(CaptureError, match="capture failed"):
            record(tmp_path / "a.wav", device_index=2, runner=runner_for("boom", 1))

    def test_success_returns_the_destination(self, tmp_path):
        dest = tmp_path / "nested" / "a.wav"
        assert record(dest, device_index=2, runner=runner_for("", 0)) == dest
        assert dest.parent.is_dir()
