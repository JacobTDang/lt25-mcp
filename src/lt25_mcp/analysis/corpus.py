"""A labelled set of clips, so calibration is measured rather than argued.

The gain thresholds in `mapping.py` were set from synthesized signals and one
real clean clip. Arguing about whether 9.5 dB is the right boundary is not
worth doing in prose: label some clips, run them through, and count how many
land in the right bucket.

`evaluate` reports accuracy and a confusion matrix. `sweep` searches the
threshold pair for the values that classify the corpus best, which turns
"these numbers are guesses" into "these numbers are the best available fit to
the evidence we have" - and reports how much evidence that is, because a
corpus of four clips does not justify three decimal places.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lt25_mcp.analysis.features import ToneFeatures, extract

LABELS = ("clean", "crunch", "high_gain")

DEFAULT_PATH = Path.home() / ".config" / "lt25-mcp" / "corpus.json"

# Below this many samples per label, a sweep is fitting noise. Reported, not
# enforced: a small corpus is still better than none.
MIN_PER_LABEL = 3


class CorpusError(Exception):
    """Raised when a corpus is unusable."""


@dataclass
class Sample:
    path: str
    label: str
    source: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.label not in LABELS:
            raise CorpusError(
                f"{self.label!r} is not a known label; choose one of: {', '.join(LABELS)}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Corpus:
    samples: list[Sample] = field(default_factory=list)

    def add(self, path: Path | str, label: str, source: str = "", notes: str = "") -> Sample:
        sample = Sample(str(path), label, source, notes)
        self.samples = [s for s in self.samples if s.path != sample.path]
        self.samples.append(sample)
        return sample

    def counts(self) -> dict[str, int]:
        return {label: sum(1 for s in self.samples if s.label == label) for label in LABELS}

    @property
    def thin(self) -> list[str]:
        """Labels with too few samples to fit anything to."""
        return [label for label, n in self.counts().items() if n < MIN_PER_LABEL]

    def to_dict(self) -> dict:
        return {"samples": [s.to_dict() for s in self.samples]}

    @classmethod
    def from_dict(cls, data: dict) -> Corpus:
        return cls(samples=[Sample(**s) for s in data.get("samples", [])])

    def save(self, path: Path | None = None) -> Path:
        path = Path(path or DEFAULT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> Corpus:
        path = Path(path or DEFAULT_PATH)
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))


@dataclass
class Prediction:
    sample: Sample
    features: ToneFeatures
    predicted: str

    @property
    def correct(self) -> bool:
        return self.predicted == self.sample.label


@dataclass
class Report:
    predictions: list[Prediction]
    clean_crest: float
    high_gain_crest: float

    @property
    def accuracy(self) -> float:
        if not self.predictions:
            return 0.0
        return sum(p.correct for p in self.predictions) / len(self.predictions)

    @property
    def confusion(self) -> dict[str, dict[str, int]]:
        """confusion[expected][predicted] = count."""
        matrix = {a: {b: 0 for b in LABELS} for a in LABELS}
        for p in self.predictions:
            matrix[p.sample.label][p.predicted] += 1
        return matrix

    def describe(self) -> str:
        lines = [
            f"thresholds: clean >= {self.clean_crest:.1f} dB, "
            f"high gain < {self.high_gain_crest:.1f} dB",
            f"accuracy: {self.accuracy:.0%} "
            f"({sum(p.correct for p in self.predictions)}/{len(self.predictions)})",
            "",
            f"{'expected':10} {'predicted':10} {'crest':>7} {'harm':>6}  clip",
        ]
        for p in sorted(self.predictions, key=lambda p: (p.sample.label, p.predicted)):
            mark = " " if p.correct else "!"
            lines.append(
                f"{mark}{p.sample.label:9} {p.predicted:10} "
                f"{p.features.crest_factor_db:7.1f} {p.features.harmonic_ratio:6.2f}  "
                f"{Path(p.sample.path).name}"
            )
        lines.append("")
        lines.append("confusion (rows expected, columns predicted):")
        header = " " * 11 + "".join(f"{label:>11}" for label in LABELS)
        lines.append(header)
        for expected, row in self.confusion.items():
            lines.append(f"{expected:10} " + "".join(f"{row[p]:>11}" for p in LABELS))
        return "\n".join(lines)


def _classify(features: ToneFeatures, clean_crest: float, high_gain_crest: float) -> str:
    """The rule from mapping.gain_character, with the thresholds injected."""
    from lt25_mcp.analysis.mapping import CLEAN_HARMONIC_RATIO

    if features.crest_factor_db >= clean_crest and features.harmonic_ratio >= CLEAN_HARMONIC_RATIO:
        return "clean"
    if features.crest_factor_db < high_gain_crest:
        return "high_gain"
    return "crunch"


def measure(corpus: Corpus) -> list[tuple[Sample, ToneFeatures]]:
    """Extract features once, so a sweep does not re-read every file per step."""
    measured = []
    for sample in corpus.samples:
        path = Path(sample.path)
        if not path.exists():
            raise CorpusError(f"corpus references a missing file: {path}")
        measured.append((sample, extract(path)))
    return measured


def evaluate(
    corpus: Corpus,
    clean_crest: float | None = None,
    high_gain_crest: float | None = None,
    measured: list[tuple[Sample, ToneFeatures]] | None = None,
) -> Report:
    """Classify every sample and count how many land in the right bucket."""
    from lt25_mcp.analysis.mapping import CLEAN_CREST_DB, HIGH_GAIN_CREST_DB

    clean_crest = CLEAN_CREST_DB if clean_crest is None else clean_crest
    high_gain_crest = HIGH_GAIN_CREST_DB if high_gain_crest is None else high_gain_crest
    measured = measure(corpus) if measured is None else measured

    return Report(
        predictions=[
            Prediction(sample, features, _classify(features, clean_crest, high_gain_crest))
            for sample, features in measured
        ],
        clean_crest=clean_crest,
        high_gain_crest=high_gain_crest,
    )


def sweep(
    corpus: Corpus,
    clean_range: tuple[float, float, float] = (6.0, 16.0, 0.5),
    high_range: tuple[float, float, float] = (1.0, 9.0, 0.5),
) -> tuple[Report, list[Report]]:
    """Search the threshold pair for the best fit to the corpus.

    Returns the best report and every report tried. Ties are broken towards
    the widest separation between the two thresholds, which keeps a boundary
    away from where the samples actually sit rather than resting on one.
    """
    if not corpus.samples:
        raise CorpusError("cannot sweep an empty corpus")
    measured = measure(corpus)

    def steps(spec):
        low, high, step = spec
        n = int(round((high - low) / step)) + 1
        return [round(low + i * step, 3) for i in range(n)]

    reports = []
    for clean, high in itertools.product(steps(clean_range), steps(high_range)):
        if high >= clean:
            continue
        reports.append(evaluate(corpus, clean, high, measured=measured))
    if not reports:
        raise CorpusError("no valid threshold pairs in the given ranges")

    best = max(reports, key=lambda r: (r.accuracy, r.clean_crest - r.high_gain_crest))
    return best, reports
