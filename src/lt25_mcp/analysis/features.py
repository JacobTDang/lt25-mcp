"""What we measure about a guitar tone.

`ToneFeatures` is deliberately a plain dataclass with no analysis library in
sight, so the mapping rules that consume it can be written and tested without
pulling in librosa. `extract()` is the only part that needs the heavy stack,
and it is imported lazily.

Units are stated on every field because the mapping rules depend on them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


class FeatureError(Exception):
    """Raised when audio cannot be measured."""


@dataclass(frozen=True)
class ToneFeatures:
    """Objective measurements of a guitar signal."""

    spectral_centroid_hz: float
    """Centre of spectral mass. Brightness. Clean necks sit low, fizzy
    high-gain sits high."""

    spectral_rolloff_hz: float
    """Frequency below which 85% of the energy lives. Tracks presence."""

    low_energy_ratio: float
    """Share of energy below 150 Hz. Thump versus tightness. 0..1."""

    mid_energy_ratio: float
    """Share of energy in 400-800 Hz. Scooped versus honky. 0..1."""

    high_energy_ratio: float
    """Share of energy above 3 kHz. Fizz and pick attack. 0..1."""

    crest_factor_db: float
    """Peak over RMS. High means dynamic and clean; low means compressed or
    saturated."""

    harmonic_ratio: float
    """Share of energy that is harmonic rather than percussive. 0..1. Falls as
    distortion and noise rise."""

    onset_strength: float
    """Mean onset envelope. Pick attack sharpness."""

    decay_time_s: float
    """Time for the signal to fall 60 dB from a peak. Long values imply
    reverb or delay in the source."""

    estimated_tempo_bpm: float
    """Detected tempo, for setting delay time in sync."""

    estimated_key: str
    """Detected musical key, e.g. 'E' or 'C#'."""

    tuning_offset_semitones: float
    """Deviation from A440 in semitones. Large negative values mean the
    source is tuned down and the part will not play in standard tuning."""

    duration_s: float

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        """Human-readable summary of what was measured."""
        return "\n".join(
            [
                f"  duration        {self.duration_s:.1f} s",
                f"  centroid        {self.spectral_centroid_hz:.0f} Hz",
                f"  rolloff (85%)   {self.spectral_rolloff_hz:.0f} Hz",
                f"  low  <150Hz     {self.low_energy_ratio:.2%}",
                f"  mid  400-800Hz  {self.mid_energy_ratio:.2%}",
                f"  high >3kHz      {self.high_energy_ratio:.2%}",
                f"  crest factor    {self.crest_factor_db:.1f} dB",
                f"  harmonic ratio  {self.harmonic_ratio:.2f}",
                f"  onset strength  {self.onset_strength:.2f}",
                f"  decay           {self.decay_time_s:.2f} s",
                f"  tempo           {self.estimated_tempo_bpm:.0f} BPM",
                f"  key             {self.estimated_key}",
                f"  tuning offset   {self.tuning_offset_semitones:+.2f} semitones",
            ]
        )


def extract(path: Path) -> ToneFeatures:
    """Measure a mono audio file.

    librosa is imported here rather than at module scope so the mapping rules
    stay usable without the analysis stack installed.
    """
    try:
        import librosa
        import numpy as np
    except ModuleNotFoundError as exc:  # pragma: no cover - setup failure path
        raise FeatureError(
            "librosa and numpy are required for feature extraction. "
            "Install them with `uv add librosa`."
        ) from exc

    path = Path(path)
    if not path.exists():
        raise FeatureError(f"no such audio file: {path}")

    y, sr = librosa.load(str(path), sr=None, mono=True)
    if y.size == 0:
        raise FeatureError(f"{path} contains no audio")

    peak = float(np.max(np.abs(y)))
    if peak == 0.0:
        raise FeatureError(f"{path} is silent; nothing to measure")

    spectrum = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    band_energy = spectrum.sum(axis=1)
    total = float(band_energy.sum()) or 1.0

    def band(low: float, high: float) -> float:
        mask = (freqs >= low) & (freqs < high)
        return float(band_energy[mask].sum()) / total

    rms = float(np.sqrt(np.mean(y**2)))
    crest_db = 20.0 * float(np.log10(peak / rms)) if rms > 0 else 0.0

    harmonic, percussive = librosa.effects.hpss(y)
    h_energy = float(np.sum(harmonic**2))
    p_energy = float(np.sum(percussive**2))
    harmonic_ratio = h_energy / (h_energy + p_energy) if (h_energy + p_energy) else 0.0

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo = librosa.feature.tempo(onset_envelope=onset_env, sr=sr)
    tuning = float(librosa.estimate_tuning(y=y, sr=sr)) * 12.0

    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr).mean(axis=1)
    key = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][
        int(np.argmax(chroma))
    ]

    return ToneFeatures(
        spectral_centroid_hz=float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))),
        spectral_rolloff_hz=float(
            np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85))
        ),
        low_energy_ratio=band(0, 150),
        mid_energy_ratio=band(400, 800),
        high_energy_ratio=band(3000, sr / 2),
        crest_factor_db=crest_db,
        harmonic_ratio=harmonic_ratio,
        onset_strength=float(np.mean(onset_env)),
        decay_time_s=_decay_time(y, sr),
        estimated_tempo_bpm=float(np.atleast_1d(tempo)[0]),
        estimated_key=key,
        tuning_offset_semitones=tuning,
        duration_s=float(len(y) / sr),
    )


def _decay_time(y, sr: int) -> float:
    """Seconds from the loudest moment until the signal falls 60 dB."""
    import numpy as np

    envelope = np.abs(y)
    peak_index = int(np.argmax(envelope))
    peak = float(envelope[peak_index])
    if peak == 0.0:
        return 0.0
    floor = peak / 1000.0  # -60 dB
    tail = envelope[peak_index:]
    below = np.nonzero(tail < floor)[0]
    if below.size == 0:
        return float(len(tail) / sr)
    return float(below[0] / sr)
