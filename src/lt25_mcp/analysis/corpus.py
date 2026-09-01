"""A labelled set of clips, so calibration is measured rather than argued.

Arguing about whether 0.00088 is the right clean boundary is not worth doing
in prose: label some clips, run them through, and count how many land in the
right bucket.

Labels come from the amp itself. Recording through a factory preset makes
Fender's choice of amp model and gain the ground truth, with the same guitar,
room and player across every take, so the only thing varying is the tone.

`evaluate` reports accuracy and a confusion matrix. `sweep` searches the
threshold pair for the values that classify the corpus best, which turns
"these numbers are guesses" into "these numbers are the best available fit to
the evidence we have" - and reports how much evidence that is, because a
corpus of four clips does not justify three decimal places.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from lt25_mcp.analysis.features import ToneFeatures, extract
from lt25_mcp.dsp_catalog import AMP_MODELS

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

    amp_model: str = ""
    """FenderId of the amp the clip was actually recorded through, when known.

    A clip captured through a factory preset has one - Fender's own choice of
    model is the ground truth. A stem separated from a recording does not, and
    an empty value means exactly that: no claim, rather than a guess.
    """

    def __post_init__(self) -> None:
        if self.label not in LABELS:
            raise CorpusError(
                f"{self.label!r} is not a known label; choose one of: {', '.join(LABELS)}"
            )
        if self.amp_model and self.amp_model not in AMP_MODELS:
            raise CorpusError(
                f"{self.amp_model!r} is not a known amp model FenderId"
            )

    def to_dict(self) -> dict:
        return asdict(self)


def amp_model_from_source(source: str) -> str:
    """The FenderId a capture's source string names, or '' if it names none.

    Clips recorded through the amp carry the preset's amp label in
    parentheses - "amp slot 1 FENDER CLEAN (TWIN CLEAN), played live" - and
    the catalog maps that label back to a FenderId. Reverse-mapping through
    the catalog rather than trusting any parenthesis keeps a source like
    "courage solo (TAB lesson)" from inventing a ground truth.
    """
    labels = {label: fender_id for fender_id, label in AMP_MODELS.items()}
    for candidate in re.findall(r"\(([^)]+)\)", source):
        if candidate in labels:
            return labels[candidate]
    return ""


@dataclass
class Corpus:
    samples: list[Sample] = field(default_factory=list)

    def add(
        self,
        path: Path | str,
        label: str,
        source: str = "",
        notes: str = "",
        amp_model: str = "",
    ) -> Sample:
        sample = Sample(str(path), label, source, notes, amp_model)
        self.samples = [s for s in self.samples if s.path != sample.path]
        self.samples.append(sample)
        return sample

    def backfill_amp_models(self) -> int:
        """Fill empty `amp_model` fields recoverable from source strings.

        Returns how many were filled. An explicit value is never overwritten:
        a stated truth beats a parsed one.
        """
        filled = 0
        for sample in self.samples:
            if sample.amp_model:
                continue
            model = amp_model_from_source(sample.source)
            if model:
                sample.amp_model = model
                filled += 1
        return filled

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
        # A sample field this build does not know means the file was written
        # by newer code. Loading it anyway would drop that field on the next
        # save, so refuse loudly instead of truncating someone else's data.
        known = {f.name for f in fields(Sample)}
        for s in data.get("samples", []):
            unknown = sorted(set(s) - known)
            if unknown:
                raise CorpusError(
                    f"corpus sample carries unknown fields: {', '.join(unknown)}; "
                    "the file was written by newer code, and loading it here "
                    "would drop them on the next save"
                )
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
    clean_flat: float
    high_gain_flat: float

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
            f"thresholds: clean < {self.clean_flat:.5f} <= crunch < "
            f"{self.high_gain_flat:.5f} <= high gain",
            f"accuracy: {self.accuracy:.0%} "
            f"({sum(p.correct for p in self.predictions)}/{len(self.predictions)})",
            "",
            f"{'expected':10} {'predicted':10} {'flatness':>10} {'harm':>6}  clip",
        ]
        for p in sorted(self.predictions, key=lambda p: (p.sample.label, p.predicted)):
            mark = " " if p.correct else "!"
            lines.append(
                f"{mark}{p.sample.label:9} {p.predicted:10} "
                f"{p.features.spectral_flatness:10.5f} {p.features.harmonic_ratio:6.2f}  "
                f"{Path(p.sample.path).name}"
            )
        lines.append("")
        lines.append("confusion (rows expected, columns predicted):")
        header = " " * 11 + "".join(f"{label:>11}" for label in LABELS)
        lines.append(header)
        for expected, row in self.confusion.items():
            lines.append(f"{expected:10} " + "".join(f"{row[p]:>11}" for p in LABELS))
        return "\n".join(lines)


@dataclass
class ModelPrediction:
    sample: Sample
    features: ToneFeatures
    predicted: str

    @property
    def exact(self) -> bool:
        return self.predicted == self.sample.amp_model

    @property
    def family(self) -> bool:
        """The predicted model sits in the clip's gain family.

        Deluxe65 for a Twin65 clip is a near miss - the player auditions a
        clean Fender either way. Jcm800 for it is not. The truth side is the
        clip's label rather than a family lookup, because the label *is* the
        gain family Fender used the true model in.
        """
        from lt25_mcp.analysis.mapping import MODEL_FAMILY

        return MODEL_FAMILY[self.predicted] == self.sample.label


@dataclass
class ModelReport:
    predictions: list[ModelPrediction]
    skipped: int
    """Corpus clips with no known amp model, which cannot be scored."""

    @property
    def exact_accuracy(self) -> float:
        if not self.predictions:
            return 0.0
        return sum(p.exact for p in self.predictions) / len(self.predictions)

    @property
    def family_accuracy(self) -> float:
        if not self.predictions:
            return 0.0
        return sum(p.family for p in self.predictions) / len(self.predictions)

    @property
    def confusion(self) -> dict[str, dict[str, int]]:
        """confusion[true model][predicted model] = count, observed pairs only.

        Sparse rather than a full grid: eighteen models square is 324 cells,
        and with nine clips all but a handful are zero.
        """
        matrix: dict[str, dict[str, int]] = {}
        for p in self.predictions:
            row = matrix.setdefault(p.sample.amp_model, {})
            row[p.predicted] = row.get(p.predicted, 0) + 1
        return matrix

    def describe(self) -> str:
        n = len(self.predictions)
        exact = sum(p.exact for p in self.predictions)
        family = sum(p.family for p in self.predictions)
        skipped = f" ({self.skipped} skipped: no known model)" if self.skipped else ""
        lines = [
            f"amp model choice over {n} clips recorded through known presets{skipped}",
            f"exact model:  {self.exact_accuracy:.0%} ({exact}/{n})",
            f"right family: {self.family_accuracy:.0%} ({family}/{n})",
            "",
            f"{'':1}{'label':10} {'truth':12} {'predicted':12}  clip",
        ]
        for p in sorted(
            self.predictions, key=lambda p: (p.sample.label, p.sample.amp_model)
        ):
            mark = " " if p.exact else ("~" if p.family else "!")
            lines.append(
                f"{mark}{p.sample.label:10} {_short(p.sample.amp_model):12} "
                f"{_short(p.predicted):12}  {Path(p.sample.path).name}"
            )
        lines.append("")
        lines.append("~ near miss: right gain family, wrong model within it")
        lines.append("")
        lines.append("confusion (truth -> predicted):")
        for truth, row in sorted(self.confusion.items()):
            for predicted, count in sorted(row.items()):
                lines.append(f"  {_short(truth):12} -> {_short(predicted):12} {count}")
        return "\n".join(lines)


def _short(fender_id: str) -> str:
    """FenderIds minus the noise: DUBS_Twin65 -> Twin65."""
    return fender_id.removeprefix("DUBS_")


def _classify(features: ToneFeatures, clean_flat: float, high_gain_flat: float) -> str:
    """The rule from mapping.gain_character, with the thresholds injected."""
    if features.spectral_flatness < clean_flat:
        return "clean"
    if features.spectral_flatness >= high_gain_flat:
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
    clean_flat: float | None = None,
    high_gain_flat: float | None = None,
    measured: list[tuple[Sample, ToneFeatures]] | None = None,
) -> Report:
    """Classify every sample and count how many land in the right bucket."""
    from lt25_mcp.analysis.mapping import CLEAN_FLATNESS, HIGH_GAIN_FLATNESS

    clean_flat = CLEAN_FLATNESS if clean_flat is None else clean_flat
    high_gain_flat = HIGH_GAIN_FLATNESS if high_gain_flat is None else high_gain_flat
    measured = measure(corpus) if measured is None else measured

    return Report(
        predictions=[
            Prediction(sample, features, _classify(features, clean_flat, high_gain_flat))
            for sample, features in measured
        ],
        clean_flat=clean_flat,
        high_gain_flat=high_gain_flat,
    )


def evaluate_models(
    corpus: Corpus,
    measured: list[tuple[Sample, ToneFeatures]] | None = None,
) -> ModelReport:
    """Score `choose_amp_model` against clips whose true amp model is known.

    `evaluate` checks the gain class; this checks the pick *within* it - the
    centroid and midrange rules that decide Twin65 over Deluxe65 over
    Princeton65. Ground truth is `Sample.amp_model`, the model of the factory
    preset a clip was recorded through. Clips without one cannot be scored
    and are counted as skipped rather than silently dropped.

    There is deliberately no sweep counterpart: with nine clips over eighteen
    models, searching the within-family rules for a better fit would be
    fitting noise, per model, with one sample each.
    """
    from lt25_mcp.analysis.mapping import choose_amp_model

    with_truth = [s for s in corpus.samples if s.amp_model]
    if not with_truth:
        raise CorpusError(
            "no clip carries a known amp model; record through factory "
            "presets, or backfill amp_model from the source strings"
        )
    if measured is None:
        measured = measure(Corpus(samples=with_truth))
    else:
        measured = [(s, f) for s, f in measured if s.amp_model]

    return ModelReport(
        predictions=[
            ModelPrediction(sample, features, choose_amp_model(features))
            for sample, features in measured
        ],
        skipped=len(corpus.samples) - len(with_truth),
    )


def sweep(
    corpus: Corpus,
    clean_range: tuple[float, float, float] = (0.00001, 0.0030, 0.00001),
    high_range: tuple[float, float, float] = (0.00002, 0.0080, 0.00001),
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
        # Rounding to 3 decimals would flatten every candidate to zero:
        # flatness thresholds live around 0.001.
        return [round(low + i * step, 8) for i in range(n)]

    reports = []
    for clean, high in itertools.product(steps(clean_range), steps(high_range)):
        if high <= clean:
            continue
        reports.append(evaluate(corpus, clean, high, measured=measured))
    if not reports:
        raise CorpusError("no valid threshold pairs in the given ranges")

    best = max(reports, key=lambda r: (r.accuracy, r.high_gain_flat - r.clean_flat))
    return best, reports
