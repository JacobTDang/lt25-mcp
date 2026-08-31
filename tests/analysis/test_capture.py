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
            record(tmp_path / "a.wav", seconds=0, device_index=2,
                   runner=runner_for("", 0), allow_short=True)

    def test_ffmpeg_failure_raises(self, tmp_path):
        with pytest.raises(CaptureError, match="capture failed"):
            record(tmp_path / "a.wav", device_index=2, runner=runner_for("boom", 1))

    def test_the_default_take_length_is_long_enough(self):
        from lt25_mcp.analysis.capture import MIN_TAKE_SECONDS
        import inspect

        default = inspect.signature(record).parameters["seconds"].default
        assert default >= MIN_TAKE_SECONDS

    def test_success_returns_the_destination(self, tmp_path):
        dest = tmp_path / "nested" / "a.wav"
        assert record(dest, device_index=2, runner=runner_for("", 0)) == dest
        assert dest.parent.is_dir()


class TestCaptureDoesNotHang:
    """macOS gates audio capture behind a Microphone permission, and a process
    without it blocks forever producing no output and no error."""

    def test_a_blocked_capture_raises_instead_of_hanging(self, tmp_path):
        import subprocess as sp

        def hangs(_argv):
            raise sp.TimeoutExpired(cmd="ffmpeg", timeout=35)

        with pytest.raises(CaptureError, match="Microphone permission"):
            record(tmp_path / "a.wav", seconds=40, device_index=2, runner=hangs)

    def test_the_error_says_what_to_do(self, tmp_path):
        import subprocess as sp

        def hangs(_argv):
            raise sp.TimeoutExpired(cmd="ffmpeg", timeout=35)

        with pytest.raises(CaptureError) as exc:
            record(tmp_path / "a.wav", seconds=5, device_index=2, runner=hangs,
               allow_short=True)
        assert "System Settings" in str(exc.value)

    def test_injected_runners_stay_argv_only(self, tmp_path):
        """A test double must not need to know about timeouts."""
        seen = []

        def plain(argv):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        record(tmp_path / "a.wav", seconds=1, device_index=2, runner=plain,
               allow_short=True)
        assert seen and seen[0][0] == "ffmpeg"


class TestTakeLength:
    """A 20s take produced five spurious knob moves on real playing; a 30s take
    produced none. See docs/measurements.md."""

    def test_a_short_take_is_refused(self, tmp_path):
        with pytest.raises(CaptureError, match="too short"):
            record(tmp_path / "a.wav", seconds=20, device_index=2,
                   runner=runner_for("", 0))

    def test_the_refusal_explains_why(self, tmp_path):
        with pytest.raises(CaptureError) as exc:
            record(tmp_path / "a.wav", seconds=5, device_index=2,
                   runner=runner_for("", 0))
        assert "noise floor" in str(exc.value)

    def test_a_device_check_may_opt_out(self, tmp_path):
        record(tmp_path / "a.wav", seconds=2, device_index=2,
               runner=runner_for("", 0), allow_short=True)

    def test_a_long_enough_take_is_allowed(self, tmp_path):
        from lt25_mcp.analysis.capture import MIN_TAKE_SECONDS

        record(tmp_path / "a.wav", seconds=MIN_TAKE_SECONDS, device_index=2,
               runner=runner_for("", 0))


class TestSilenceIsRefused:
    """Nine silent captures once entered a labelled corpus and produced a
    confident, wrong conclusion. Silence must fail, not measure."""

    def _write(self, tmp_path, amplitude, name="a.wav"):
        import math

        import numpy as np
        import soundfile as sf

        t = np.linspace(0, 2.0, 44100 * 2, endpoint=False)
        path = tmp_path / name
        sf.write(path, (amplitude * np.sin(2 * math.pi * 220 * t)).astype(np.float32),
                 44100, subtype="FLOAT")
        return path

    def test_an_idle_capture_raises(self, tmp_path):
        from lt25_mcp.analysis.capture import check_has_signal

        with pytest.raises(CaptureError, match="near-silence"):
            check_has_signal(self._write(tmp_path, 0.003))

    def test_the_error_says_what_to_check(self, tmp_path):
        from lt25_mcp.analysis.capture import check_has_signal

        with pytest.raises(CaptureError) as exc:
            check_has_signal(self._write(tmp_path, 0.001))
        assert "plugged in" in str(exc.value)

    def test_a_real_take_passes(self, tmp_path):
        from lt25_mcp.analysis.capture import check_has_signal

        peak, rms = check_has_signal(self._write(tmp_path, 0.5))
        assert peak > 0.4

    def test_the_threshold_sits_between_idle_and_played(self, tmp_path):
        """Measured: idle peaks 0.001-0.006, a real take peaks near 0.55."""
        from lt25_mcp.analysis.capture import SILENCE_PEAK

        assert 0.006 < SILENCE_PEAK < 0.5

    def test_an_empty_file_raises(self, tmp_path):
        import numpy as np
        import soundfile as sf

        from lt25_mcp.analysis.capture import check_has_signal

        path = tmp_path / "empty.wav"
        sf.write(path, np.zeros(0, dtype=np.float32), 44100)
        with pytest.raises(CaptureError):
            check_has_signal(path)


class TestWaitForPlaying:
    """Coordinating 'start now' by hand is what produced nine silent takes."""

    def test_returns_as_soon_as_signal_appears(self, tmp_path, monkeypatch):
        import math

        import numpy as np
        import soundfile as sf

        from lt25_mcp.analysis import capture as cap

        calls = {"n": 0}

        def fake_record(dest, seconds, **kw):
            calls["n"] += 1
            t = np.linspace(0, 1.0, 44100, endpoint=False)
            amp = 0.001 if calls["n"] < 3 else 0.5   # quiet, quiet, then playing
            sf.write(dest, (amp * np.sin(2 * math.pi * 220 * t)).astype(np.float32),
                     44100, subtype="FLOAT")
            return dest

        monkeypatch.setattr(cap, "record", fake_record)
        cap.wait_for_playing(timeout=10, poll_seconds=0.1, device_index=2)
        assert calls["n"] == 3

    def test_gives_up_with_an_actionable_error(self, tmp_path, monkeypatch):
        import math

        import numpy as np
        import soundfile as sf

        from lt25_mcp.analysis import capture as cap

        def always_quiet(dest, seconds, **kw):
            t = np.linspace(0, 1.0, 44100, endpoint=False)
            sf.write(dest, (0.001 * np.sin(2 * math.pi * 220 * t)).astype(np.float32),
                     44100, subtype="FLOAT")
            return dest

        monkeypatch.setattr(cap, "record", always_quiet)
        with pytest.raises(CaptureError, match="plugged into"):
            cap.wait_for_playing(timeout=0.5, poll_seconds=0.05, device_index=2)
