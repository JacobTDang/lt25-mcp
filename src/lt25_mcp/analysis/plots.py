"""Spectrograms, so a tone can be looked at rather than only measured.

Numbers describe a tone; a spectrogram shows it. Comparing the target against
what the amp actually produces is how the loop converges, and that comparison
is far easier by eye than by statistic.
"""

from __future__ import annotations

from pathlib import Path


class PlotError(Exception):
    """Raised when a plot cannot be produced."""


def _load(path: Path):
    try:
        import librosa
    except ModuleNotFoundError as exc:  # pragma: no cover - setup failure path
        raise PlotError(
            "librosa and matplotlib are required for plots. "
            "Install them with `uv add librosa matplotlib`."
        ) from exc
    path = Path(path)
    if not path.exists():
        raise PlotError(f"no such audio file: {path}")
    return librosa.load(str(path), sr=None, mono=True)


def spectrogram(path: Path, dest: Path, *, title: str | None = None) -> Path:
    """Render a log-frequency mel spectrogram to a PNG."""
    import librosa
    import librosa.display
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    y, sr = _load(path)
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128)
    db = librosa.power_to_db(mel, ref=np.max)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    image = librosa.display.specshow(db, sr=sr, x_axis="time", y_axis="mel", ax=ax)
    ax.set_title(title or Path(path).stem)
    fig.colorbar(image, ax=ax, format="%+2.0f dB")
    fig.tight_layout()
    fig.savefig(dest, dpi=120)
    plt.close(fig)
    return dest


def compare(target: Path, candidate: Path, dest: Path) -> Path:
    """Two average spectra overlaid, so the gap between them is visible."""
    import librosa
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    for path, label in ((target, "target"), (candidate, "candidate")):
        y, sr = _load(path)
        spectrum = np.abs(librosa.stft(y)).mean(axis=1)
        freqs = librosa.fft_frequencies(sr=sr)
        with np.errstate(divide="ignore"):
            db = 20 * np.log10(np.maximum(spectrum, 1e-10))
        ax.semilogx(freqs[1:], db[1:], label=label, linewidth=1.2)
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("magnitude (dB)")
    ax.set_xlim(40, 16000)
    ax.grid(alpha=0.3, which="both")
    ax.legend()
    fig.tight_layout()
    fig.savefig(dest, dpi=120)
    plt.close(fig)
    return dest
