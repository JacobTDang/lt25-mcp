"""What we measure about a guitar tone.

`ToneFeatures` is deliberately a plain dataclass with no analysis library in
sight, so the mapping rules that consume it can be written and tested without
pulling in librosa. `extract()` is the only part that needs the heavy stack,
and it is imported lazily.

Units are stated on every field because the mapping rules depend on them.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path


LOW_BAND = (80.0, 250.0)
MID_BAND = (400.0, 1200.0)
HIGH_BAND = (2000.0, 8000.0)


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
    """Share of guitar-band energy in 80-250 Hz - the fundamentals of the
    lower strings. Thump versus tightness. 0..1, normalized against the mid
    and high bands so it stays meaningful on an isolated stem."""

    mid_energy_ratio: float
    """Share of guitar-band energy in 400-1200 Hz. Scooped versus honky. 0..1."""

    high_energy_ratio: float
    """Share of guitar-band energy in 2-8 kHz. Presence, fizz and pick
    attack. 0..1."""

    crest_factor_db: float
    """Peak over RMS across the whole take.

    Note this measures the *performance's* dynamic range as much as the tone:
    gaps between notes inflate it regardless of distortion. Measured on real
    amp output it does not separate clean from high gain - use
    `spectral_flatness` for that."""

    harmonic_ratio: float
    """Share of energy that is harmonic rather than percussive. 0..1. Falls as
    distortion and noise rise."""

    onset_strength: float
    """Mean onset envelope. Pick attack sharpness."""

    decay_time_s: float
    """Time for the signal to fall 30 dB from its loudest point. Long values
    imply reverb, delay or long sustain in the source. Note this conflates
    note decay with room decay; it is a proxy, not an RT60."""

    estimated_tempo_bpm: float
    """Detected tempo, for setting delay time in sync."""

    estimated_key: str
    """Detected musical key, e.g. 'E' or 'C#'."""

    tuning_offset_semitones: float
    """Deviation from A440 in semitones. Large negative values mean the
    source is tuned down and the part will not play in standard tuning."""

    duration_s: float

    spectral_flatness: float = 0.0
    """Geometric over arithmetic mean of the spectrum, 0..1.

    Depends on the sample rate, because a lower one truncates the top of the
    spectrum. Compare it only between clips recorded the same way, and check
    `high_band_truncated` first - the calibration was done at 44.1 kHz.

    Low means peaky - a few strong harmonics, which is a clean tone. High means
    noise-like, which is what distortion produces as it generates harmonics and
    intermodulation. This is the measurement that actually tracks saturation:
    across real amp presets it ran 0.0000-0.0011 clean, 0.0009-0.0023 crunch
    and 0.0029-0.0062 high gain."""

    sample_rate_hz: int = 44100
    """Sample rate of the source. Low rates truncate the high band."""

    high_band_truncated: bool = False
    """True when the source's Nyquist limit falls inside the high band, so the
    measured treble content is an underestimate and the treble knob derived
    from it should not be trusted."""

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        """Human-readable summary of what was measured."""
        return "\n".join(
            [
                f"  duration        {self.duration_s:.1f} s",
                f"  centroid        {self.spectral_centroid_hz:.0f} Hz",
                f"  rolloff (85%)   {self.spectral_rolloff_hz:.0f} Hz",
                f"  low  80-250Hz   {self.low_energy_ratio:.2%}",
                f"  mid  400-1200Hz {self.mid_energy_ratio:.2%}",
                f"  high 2-8kHz     {self.high_energy_ratio:.2%}",
                f"  crest factor    {self.crest_factor_db:.1f} dB",
                f"  flatness        {self.spectral_flatness:.5f}",
                f"  harmonic ratio  {self.harmonic_ratio:.2f}",
                f"  onset strength  {self.onset_strength:.2f}",
                f"  decay           {self.decay_time_s:.2f} s",
                f"  tempo           {self.estimated_tempo_bpm:.0f} BPM",
                f"  key             {self.estimated_key}",
                f"  tuning offset   {self.tuning_offset_semitones:+.2f} semitones",
                f"  sample rate     {self.sample_rate_hz} Hz",
            ]
            + (
                [
                    f"  NOTE: Nyquist is {self.sample_rate_hz // 2} Hz, inside the "
                    f"{int(HIGH_BAND[0])}-{int(HIGH_BAND[1])} Hz band, so the high "
                    "band is truncated and treble is understated",
                ]
                if self.high_band_truncated
                else []
            )
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

    def band(low: float, high: float) -> float:
        mask = (freqs >= low) & (freqs < high)
        return float(band_energy[mask].sum())

    # Bands chosen around a guitar's actual range, and normalized against each
    # other rather than against total energy. An isolated guitar stem has had
    # the bass guitar removed, so measuring "share of everything below 150 Hz"
    # reports near-zero for every stem and tells you nothing about whether the
    # guitar tone itself is bass-heavy.
    low_e, mid_e, high_e = band(*LOW_BAND), band(*MID_BAND), band(*HIGH_BAND)
    tonal = low_e + mid_e + high_e or 1.0

    rms = float(np.sqrt(np.mean(y**2)))
    crest_db = 20.0 * float(np.log10(peak / rms)) if rms > 0 else 0.0

    harmonic, percussive = librosa.effects.hpss(y)
    h_energy = float(np.sum(harmonic**2))
    p_energy = float(np.sum(percussive**2))
    harmonic_ratio = h_energy / (h_energy + p_energy) if (h_energy + p_energy) else 0.0

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo = librosa.feature.tempo(onset_envelope=onset_env, sr=sr)
    tuning = float(librosa.estimate_tuning(y=y, sr=sr)) * 12.0

    chroma = _chroma(harmonic, sr).mean(axis=1)
    key = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][
        int(np.argmax(chroma))
    ]

    return ToneFeatures(
        spectral_centroid_hz=float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))),
        spectral_rolloff_hz=float(
            np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85))
        ),
        low_energy_ratio=low_e / tonal,
        mid_energy_ratio=mid_e / tonal,
        high_energy_ratio=high_e / tonal,
        crest_factor_db=crest_db,
        spectral_flatness=float(np.mean(librosa.feature.spectral_flatness(y=y))),
        harmonic_ratio=harmonic_ratio,
        onset_strength=float(np.mean(onset_env)),
        decay_time_s=_decay_time(y, sr),
        estimated_tempo_bpm=float(np.atleast_1d(tempo)[0]),
        estimated_key=key,
        tuning_offset_semitones=tuning,
        duration_s=float(len(y) / sr),
        sample_rate_hz=int(sr),
        high_band_truncated=bool(sr / 2 < HIGH_BAND[1]),
    )


def _chroma(harmonic, sr: int):
    """Chroma over as many octaves as the sample rate actually supports.

    librosa's constant-Q chroma defaults to seven octaves from C1, whose top
    bin lands near 4186 Hz. On an 8 kHz source that exceeds Nyquist and raises
    rather than degrading, so the octave count is reduced to fit.
    """
    import librosa
    import numpy as np

    fmin = librosa.note_to_hz("C1")
    nyquist = sr / 2
    octaves = int(np.floor(np.log2(nyquist / fmin)))
    octaves = max(1, min(7, octaves))
    return librosa.feature.chroma_cqt(y=harmonic, sr=sr, n_octaves=octaves)


DECAY_FRAME = 2048
DECAY_HOP = 512
DECAY_DROP = 10 ** (-30 / 20)  # -30 dB


def _decay_time(y, sr: int) -> float:
    """Seconds from the loudest moment until the signal falls 30 dB.

    Measured on a short-time RMS envelope rather than raw sample magnitude. A
    steady tone crosses zero twice per cycle, so raw magnitude dips below any
    floor almost immediately and would report a sustained note as instantly
    decayed.
    """
    import librosa
    import numpy as np

    envelope = librosa.feature.rms(
        y=y, frame_length=DECAY_FRAME, hop_length=DECAY_HOP
    )[0]
    if envelope.size == 0:
        return 0.0
    peak_index = int(np.argmax(envelope))
    peak = float(envelope[peak_index])
    if peak == 0.0:
        return 0.0
    # -30 dB rather than -60. Real recordings hit their noise floor long
    # before -60 dB, so a T60 measurement saturates at "never decays" for
    # almost every clip and stops discriminating.
    floor = peak * DECAY_DROP
    below = np.nonzero(envelope[peak_index:] < floor)[0]
    if below.size == 0:
        # The signal never decays within the clip. Reporting time-from-peak
        # here would depend on where the peak happened to land, so report the
        # whole clip: the true decay is at least this long.
        return float(len(y) / sr)
    return float(librosa.frames_to_time(below[0], sr=sr, hop_length=DECAY_HOP))


SPECTRUM_BANDS = 28
SPECTRUM_MIN_HZ = 60.0
SPECTRUM_MAX_HZ = 12000.0


def log_spectrum(path: Path, bands: int = SPECTRUM_BANDS) -> tuple[list[float], list[float]]:
    """A coarse log-spaced spectrum, for drawing rather than for measuring.

    Returns band centre frequencies and their levels in dB relative to the
    loudest band, so two spectra can be overlaid regardless of how loud either
    recording happens to be.
    """
    import librosa
    import numpy as np

    path = Path(path)
    if not path.exists():
        raise FeatureError(f"no such audio file: {path}")

    y, sr = librosa.load(str(path), sr=None, mono=True)
    if y.size == 0:
        raise FeatureError(f"{path} contains no audio")

    magnitude = np.abs(librosa.stft(y)).mean(axis=1)
    freqs = librosa.fft_frequencies(sr=sr)
    top = min(SPECTRUM_MAX_HZ, sr / 2)
    edges = np.logspace(np.log10(SPECTRUM_MIN_HZ), np.log10(top), bands + 1)

    centres: list[float] = []
    levels: list[float] = []
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (freqs >= low) & (freqs < high)
        energy = float(magnitude[mask].mean()) if mask.any() else 0.0
        centres.append(float(np.sqrt(low * high)))
        levels.append(energy)

    peak = max(levels) or 1.0
    return centres, [
        20.0 * math.log10(max(level, peak * 1e-5) / peak) for level in levels
    ]
