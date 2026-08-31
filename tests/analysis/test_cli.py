"""End-to-end wiring tests for the analysis pipeline.

The heavy stages are stubbed, so this proves the stages are connected
correctly and that failures surface, without needing demucs or a network.
"""

import json

import pytest

from lt25_mcp.analysis import cli
from lt25_mcp.analysis.features import ToneFeatures
from lt25_mcp.analysis.stems import StemError


def fake_features(**overrides) -> ToneFeatures:
    base = dict(
        spectral_centroid_hz=1800.0,
        spectral_rolloff_hz=4200.0,
        low_energy_ratio=0.2,
        mid_energy_ratio=0.28,
        high_energy_ratio=0.15,
        crest_factor_db=11.0,
        harmonic_ratio=0.7,
        onset_strength=1.5,
        decay_time_s=0.8,
        estimated_tempo_bpm=120.0,
        estimated_key="E",
        tuning_offset_semitones=0.0,
        duration_s=10.0,
    )
    base.update(overrides)
    return ToneFeatures(**base)


@pytest.fixture
def stub_stages(monkeypatch, tmp_path):
    """Stub every stage that needs a download or the analysis stack."""
    calls = []

    def fetch(url, dest, **_):
        calls.append(("fetch", url))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"RIFF")
        return dest

    def trim(src, start, end, dest, **_):
        calls.append(("trim", start, end))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"RIFF")
        return dest

    def isolate(src, out_dir, **_):
        calls.append(("isolate", str(src)))
        stem = out_dir / "guitar.wav"
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.write_bytes(b"RIFF")
        return stem

    def extract(path):
        calls.append(("extract", str(path)))
        return fake_features()

    def spectrogram(path, dest, **_):
        calls.append(("spectrogram", str(path)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"PNG")
        return dest

    monkeypatch.setattr(cli.acquire, "fetch_audio", fetch)
    monkeypatch.setattr(cli.acquire, "trim", trim)
    monkeypatch.setattr(cli.stems, "isolate_guitar", isolate)
    monkeypatch.setattr(cli.features_mod, "extract", extract)
    monkeypatch.setattr(cli.plots, "spectrogram", spectrogram)
    return calls


class TestAnalyse:
    def test_runs_every_stage_in_order(self, stub_stages, sample_preset, tmp_path):
        cli.analyse(
            url="https://x", base=sample_preset, work_dir=tmp_path, start=1, end=5
        )
        assert [c[0] for c in stub_stages] == [
            "fetch",
            "trim",
            "isolate",
            "extract",
            "spectrogram",
        ]

    def test_measures_the_stem_not_the_mix(self, stub_stages, sample_preset, tmp_path):
        cli.analyse(url="https://x", base=sample_preset, work_dir=tmp_path)
        extracted = [c for c in stub_stages if c[0] == "extract"][0]
        assert "guitar.wav" in extracted[1]

    def test_no_separate_measures_the_source(self, stub_stages, sample_preset, tmp_path):
        cli.analyse(
            url="https://x", base=sample_preset, work_dir=tmp_path, separate=False
        )
        assert not any(c[0] == "isolate" for c in stub_stages)

    def test_local_audio_skips_the_download(self, stub_stages, sample_preset, tmp_path):
        src = tmp_path / "local.wav"
        src.write_bytes(b"RIFF")
        cli.analyse(audio=src, base=sample_preset, work_dir=tmp_path)
        assert not any(c[0] == "fetch" for c in stub_stages)

    def test_result_carries_a_preset_built_from_the_base(
        self, stub_stages, sample_preset, tmp_path
    ):
        result = cli.analyse(
            url="https://x", base=sample_preset, work_dir=tmp_path, name="TEST TONE"
        )
        assert result.preset.display_name == "TEST TONE"
        assert result.preset.to_dict() != sample_preset.to_dict()

    def test_pipeline_never_touches_the_amp(self, stub_stages, sample_preset, tmp_path):
        """No session, transport or write may appear in the analysis path."""
        import inspect

        source = inspect.getsource(cli)
        for forbidden in ("open_transport", "Session", "write_preset", "savePresetAs"):
            assert forbidden not in source


class TestFailureModes:
    def test_both_url_and_audio_raises(self, sample_preset, tmp_path):
        with pytest.raises(cli.PipelineError, match="exactly one"):
            cli.analyse(
                url="https://x", audio=tmp_path / "a.wav", base=sample_preset,
                work_dir=tmp_path,
            )

    def test_neither_url_nor_audio_raises(self, sample_preset, tmp_path):
        with pytest.raises(cli.PipelineError, match="exactly one"):
            cli.analyse(base=sample_preset, work_dir=tmp_path)

    def test_missing_local_audio_raises(self, sample_preset, tmp_path):
        with pytest.raises(cli.PipelineError, match="no such audio"):
            cli.analyse(audio=tmp_path / "nope.wav", base=sample_preset, work_dir=tmp_path)

    def test_stem_failure_propagates(self, stub_stages, monkeypatch, sample_preset, tmp_path):
        def boom(*a, **k):
            raise StemError("no guitar stem")

        monkeypatch.setattr(cli.stems, "isolate_guitar", boom)
        with pytest.raises(StemError):
            cli.analyse(url="https://x", base=sample_preset, work_dir=tmp_path)


class TestMain:
    def test_writes_the_preset_file(self, stub_stages, tmp_path):
        out = tmp_path / "tone.json"
        code = cli.main(
            [
                "--url", "https://x",
                "--base", "tests/fixtures/clean.json",
                "--out", str(out),
                "--work-dir", str(tmp_path / "work"),
                "--name", "CLI TEST",
            ]
        )
        assert code == 0
        assert json.loads(out.read_text())["info"]["displayName"] == "CLI TEST" + " " * 8

    def test_reports_errors_without_traceback(self, stub_stages, monkeypatch, tmp_path):
        def boom(*a, **k):
            raise StemError("model missing")

        monkeypatch.setattr(cli.stems, "isolate_guitar", boom)
        code = cli.main(
            [
                "--url", "https://x",
                "--base", "tests/fixtures/clean.json",
                "--out", str(tmp_path / "tone.json"),
                "--work-dir", str(tmp_path / "work"),
            ]
        )
        assert code == 1
