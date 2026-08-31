"""Adapting presets to whichever guitar is actually plugged in.

Pickup categories are a coarse prior: "humbucker" spans a vintage PAF and a
ceramic bridge unit that are nothing alike, and category says nothing about
string gauge, playing attack or how high the pickups are set. So rather than
categorising harder, this measures.

Each guitar is captured once through the same reference preset, playing the
same thing, and stored as a `GuitarProfile`. One profile is marked the
reference - the guitar presets were built around - and every other guitar is
described as a delta from it. Switching guitars then shifts the tone controls
by that delta.

Two consequences worth being explicit about:

The first guitar calibrated gets no adjustment, and that is correct: with one
guitar there is nothing to adapt between, and presets are built for it by
definition.

Absolute levels are only comparable between captures made the same way. They
are *not* comparable against a recording off the internet, which has been
mixed and mastered, so nothing here compares a player's output level to a
target clip's. Brightness is compared, cautiously, because spectral tilt
survives mastering better than level does.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "lt25-mcp" / "guitars.json"

# The preset a calibration capture must be played through, so two captures are
# comparable. Anything with a flat, low-gain response works; what matters is
# that it does not change between guitars.
REFERENCE_PRESET_AMP = "DUBS_LinearGain"
REFERENCE_KNOBS = {"gain": 5.0, "treb": 5.0, "mid": 5.0, "bass": 5.0}

CALIBRATION_INSTRUCTIONS = """\
Play for about 20 seconds through the reference preset:

  - open chords strummed at a normal, consistent dynamic
  - cover the low and high strings; do not palm-mute
  - do not touch the guitar's own volume or tone controls

The capture measures how hot and how bright this guitar is relative to the
others. Playing harder or softer than usual will skew it, so play the way you
normally would."""

# How far a knob moves per unit of measured difference. Deliberately gentle:
# these adapt a starting point rather than redesign a tone.
GAIN_PER_DB = 0.35
"""Gain knob steps (0-10 scale) per dB of output difference between guitars."""

TREBLE_PER_OCTAVE = 2.0
"""Treble knob steps per octave of brightness difference."""

MAX_ADJUSTMENT = 2.5
"""No automatic adjustment moves a knob further than this, on the 0-10 scale."""


class GuitarError(Exception):
    """Raised when a guitar profile is missing or unusable."""


@dataclass
class GuitarProfile:
    """What a capture of one guitar measured."""

    name: str
    output_dbfs: float
    """RMS level of the capture. Comparable only against other captures."""

    centroid_hz: float
    """Spectral centre of mass. Brightness."""

    crest_factor_db: float
    """Pick dynamics: how far peaks sit above the average."""

    sustain_s: float
    pickups: str = "unknown"
    is_reference: bool = False
    captured_at: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise GuitarError("a guitar profile needs a name")
        if not self.captured_at:
            self.captured_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> GuitarProfile:
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def describe(self) -> str:
        marker = "  (reference)" if self.is_reference else ""
        return (
            f"{self.name}{marker}\n"
            f"  output    {self.output_dbfs:+.1f} dBFS\n"
            f"  centroid  {self.centroid_hz:.0f} Hz\n"
            f"  dynamics  {self.crest_factor_db:.1f} dB crest\n"
            f"  sustain   {self.sustain_s:.2f} s"
        )


@dataclass
class GuitarLibrary:
    """Every guitar that has been calibrated."""

    guitars: dict[str, GuitarProfile] = field(default_factory=dict)

    @property
    def reference(self) -> GuitarProfile | None:
        for profile in self.guitars.values():
            if profile.is_reference:
                return profile
        return None

    def add(self, profile: GuitarProfile) -> GuitarProfile:
        """Store a profile. The first one added becomes the reference."""
        if not self.guitars:
            profile.is_reference = True
        self.guitars[profile.name] = profile
        return profile

    def set_reference(self, name: str) -> GuitarProfile:
        if name not in self.guitars:
            raise GuitarError(f"no profile named {name!r}; calibrate it first")
        for profile in self.guitars.values():
            profile.is_reference = profile.name == name
        return self.guitars[name]

    def to_dict(self) -> dict:
        return {"guitars": {n: p.to_dict() for n, p in self.guitars.items()}}

    @classmethod
    def from_dict(cls, data: dict) -> GuitarLibrary:
        return cls(
            guitars={
                name: GuitarProfile.from_dict(raw)
                for name, raw in data.get("guitars", {}).items()
            }
        )

    def save(self, path: Path | None = None) -> Path:
        path = Path(path or DEFAULT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> GuitarLibrary:
        path = Path(path or DEFAULT_PATH)
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))


def profile_from_capture(name: str, capture: Path, **extra) -> GuitarProfile:
    """Measure a calibration recording into a profile."""
    import librosa
    import numpy as np

    from lt25_mcp.analysis.features import extract

    features = extract(Path(capture))
    # Output level is derived here rather than read off ToneFeatures, which
    # deliberately carries no absolute level: it is only meaningful between
    # captures made the same way.
    y, _sr = librosa.load(str(capture), sr=None, mono=True)
    rms = float(np.sqrt(np.mean(y**2)))
    output_dbfs = 20.0 * math.log10(rms) if rms > 0 else -120.0

    return GuitarProfile(
        name=name,
        output_dbfs=output_dbfs,
        centroid_hz=features.spectral_centroid_hz,
        crest_factor_db=features.crest_factor_db,
        sustain_s=features.decay_time_s,
        **extra,
    )


def adapt(
    knobs: dict[str, float], playing: GuitarProfile, reference: GuitarProfile | None
) -> tuple[dict[str, float], list[str]]:
    """Shift knob positions from the reference guitar to the one being played.

    Returns adjusted knobs and a note per change. With no reference, or when
    the reference is the guitar being played, nothing moves.
    """
    adjusted = dict(knobs)
    if reference is None or reference.name == playing.name:
        return adjusted, []

    notes: list[str] = []

    level_delta = playing.output_dbfs - reference.output_dbfs
    gain_delta = _clamp_adjustment(-level_delta * GAIN_PER_DB)
    if "gain" in adjusted and abs(gain_delta) >= 0.1:
        before = adjusted["gain"]
        adjusted["gain"] = _clamp_knob(before + gain_delta)
        notes.append(
            f"gain {before:.1f} -> {adjusted['gain']:.1f}: {playing.name} is "
            f"{abs(level_delta):.1f} dB "
            f"{'hotter' if level_delta > 0 else 'quieter'} than {reference.name}"
        )

    if playing.centroid_hz > 0 and reference.centroid_hz > 0:
        octaves = math.log2(playing.centroid_hz / reference.centroid_hz)
        treb_delta = _clamp_adjustment(-octaves * TREBLE_PER_OCTAVE)
        if "treb" in adjusted and abs(treb_delta) >= 0.1:
            before = adjusted["treb"]
            adjusted["treb"] = _clamp_knob(before + treb_delta)
            notes.append(
                f"treb {before:.1f} -> {adjusted['treb']:.1f}: {playing.name} is "
                f"{'brighter' if octaves > 0 else 'darker'} than {reference.name} "
                f"by {abs(octaves):.2f} octaves of spectral centre"
            )
    return adjusted, notes


def _clamp_adjustment(delta: float) -> float:
    return max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, delta))


def _clamp_knob(value: float) -> float:
    return max(0.0, min(10.0, value))
