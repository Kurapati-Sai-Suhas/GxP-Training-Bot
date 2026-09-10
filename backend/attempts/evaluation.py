"""Offline evaluation harness for adaptive / predictive approaches.

Why this exists
---------------
The project can currently state that its adaptive engine *behaves* as designed -- there are
scenario tests for that -- but it cannot state that any alternative would be *better*, because
there is no way to compare approaches on identical data. Every future proposal (difficulty-aware
priority, BKT, IRT, a neural knowledge-tracing model) is unfalsifiable until such a comparison
exists. This module is that comparison, built before the alternatives rather than after, so the
decision to adopt or reject one can be made on evidence.

What it is NOT
--------------
It is not a knowledge-tracing model, and running it does not make the project "use ML". It
evaluates predictors; it does not add one to the product. Nothing here is imported by the
production request path, and `adaptive.py`, `fsrs.py` and `services.py` are read from but never
modified.

Leakage discipline
------------------
Two rules govern every feature:

1. **Temporal order only.** Interactions are ordered per learner by `answered_at`. A prediction
   for interaction i may use interactions 0..i-1 and nothing later. There is no random split;
   a random split over a learner's sequence would let the future explain the past.

2. **Snapshots, never live ratings.** `Question.elo_rating` and `TopicMastery.elo_rating` move
   after every answer -- partly *because of* the answer being predicted. Joining today's rating
   onto a historical interaction would feed the outcome back into its own feature. The harness
   therefore reads `AttemptAnswer.question_difficulty_at_answer` and `.learner_ability_at_answer`,
   captured before the Elo update ran, and never touches the live fields.

Rows written before those snapshot fields existed carry NULL and are **excluded**, not imputed.
Excluding them shrinks the dataset; imputing them would corrupt it.

Data sufficiency
----------------
With too few observations, every metric here is computable but meaningless -- a 40-sample AUC has
a confidence interval wide enough to swallow any difference worth detecting. The harness therefore
gates itself (see `SufficiencyThresholds`) and reports INSUFFICIENT_DATA rather than a number that
would read as a result. Reaching that state is a correct outcome, not a failure.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

from . import adaptive
from .services import _expected_score

# --------------------------------------------------------------------------------------
# Sufficiency thresholds
# --------------------------------------------------------------------------------------
#
# Chosen from the standard error of the statistics involved, not from what the current
# dataset happens to contain.
#
# ROC-AUC's standard error is approximately sqrt(A(1-A)/min(n_pos, n_neg)) for small samples.
# With 20 observations in the minority class and AUC near 0.7, SE is roughly 0.10, so a 95%
# interval spans about +/- 0.20 -- wider than the entire plausible gap between the baselines
# being compared. Below that the ranking of two models is decided by noise. 20 is therefore an
# absolute floor for reporting AUC at all, not a level at which results become trustworthy.
#
# MIN_EVAL_SAMPLES = 100 follows the same reasoning applied to the evaluation split as a whole:
# below ~100 held-out interactions, log loss and Brier differences between reasonable models sit
# inside their own sampling error.
#
# MIN_LEARNERS = 5 exists because per-learner effects dominate at small n: a harness fed three
# learners is measuring those three people, not the approach.
MIN_EVAL_SAMPLES = 100
MIN_PER_CLASS = 20
MIN_LEARNERS = 5
MIN_SEQUENCE_LENGTH = 5  # a learner shorter than this contributes no usable history/eval split


@dataclass
class SufficiencyThresholds:
    min_eval_samples: int = MIN_EVAL_SAMPLES
    min_per_class: int = MIN_PER_CLASS
    min_learners: int = MIN_LEARNERS
    min_sequence_length: int = MIN_SEQUENCE_LENGTH


# --------------------------------------------------------------------------------------
# Event extraction
# --------------------------------------------------------------------------------------
@dataclass
class Interaction:
    """One learner response, with only pre-answer information attached.

    `difficulty` and `ability` are the stored snapshots -- the ratings as they were when the
    answer was recorded. They are never re-derived from the live Question/TopicMastery rows.
    """

    learner_id: int
    answered_at: datetime
    question_id: int
    chunk_id: int | None          # SOPChunk -- the project's adaptive unit. Not a "concept".
    sop_id: int
    is_correct: bool              # the target. Never used as an input feature.
    difficulty: float | None      # question_difficulty_at_answer
    ability: float | None         # learner_ability_at_answer


@dataclass
class ExtractionReport:
    total_rows: int = 0
    usable: int = 0
    excluded_synthetic: int = 0
    excluded_no_timestamp: int = 0
    excluded_no_snapshot: int = 0
    excluded_short_sequence: int = 0
    learners: int = 0
    sequence_lengths: dict[int, int] = field(default_factory=dict)


def load_interactions(queryset=None, thresholds: SufficiencyThresholds | None = None,
                      include_synthetic: bool = False):
    """Extract temporally-ordered interactions, excluding anything not temporally valid.

    Returns (sequences_by_learner, report). Exclusions are counted rather than silently
    dropped, because the size of what cannot be used is itself a finding.
    """
    from .models import AttemptAnswer

    thresholds = thresholds or SufficiencyThresholds()
    if queryset is None:
        queryset = AttemptAnswer.objects.all()

    rows = (
        queryset.select_related("attempt", "question")
        .values(
            "attempt__learner_id", "attempt__sop_id", "attempt__is_synthetic",
            "answered_at", "question_id",
            "question__source_chunk_id", "is_correct",
            "question_difficulty_at_answer", "learner_ability_at_answer",
        )
    )

    report = ExtractionReport()
    by_learner: dict[int, list[Interaction]] = {}

    for row in rows:
        report.total_rows += 1
        if row["attempt__is_synthetic"] and not include_synthetic:
            # Scripted demo outcomes. They demonstrate the loop correctly but were decided by a
            # command rather than observed from a person, so they are evidence about the
            # demo script, not about learning. Excluded by default and counted, never silently
            # blended with real interactions.
            report.excluded_synthetic += 1
            continue
        if row["answered_at"] is None:
            # Pre-instrumentation rows. Deliberately excluded rather than ordered by id:
            # an auto-increment is a proxy for time, not a guarantee of it.
            report.excluded_no_timestamp += 1
            continue
        if row["question_difficulty_at_answer"] is None or row["learner_ability_at_answer"] is None:
            # Written before the Elo snapshot existed. Imputing today's live rating is exactly
            # the look-ahead bias the snapshot was added to prevent.
            report.excluded_no_snapshot += 1
            continue
        by_learner.setdefault(row["attempt__learner_id"], []).append(
            Interaction(
                learner_id=row["attempt__learner_id"],
                answered_at=row["answered_at"],
                question_id=row["question_id"],
                chunk_id=row["question__source_chunk_id"],
                sop_id=row["attempt__sop_id"],
                is_correct=bool(row["is_correct"]),
                difficulty=row["question_difficulty_at_answer"],
                ability=row["learner_ability_at_answer"],
            )
        )

    # Stable temporal ordering. question_id breaks ties only so the order is deterministic --
    # answers submitted in one request share a recording time by design.
    usable: dict[int, list[Interaction]] = {}
    for learner_id, events in by_learner.items():
        events.sort(key=lambda e: (e.answered_at, e.question_id))
        if len(events) < thresholds.min_sequence_length:
            report.excluded_short_sequence += len(events)
            continue
        usable[learner_id] = events
        report.sequence_lengths[learner_id] = len(events)

    report.usable = sum(len(v) for v in usable.values())
    report.learners = len(usable)
    return usable, report


# --------------------------------------------------------------------------------------
# Leakage-safe features
# --------------------------------------------------------------------------------------
@dataclass
class FeatureRow:
    """Everything known *before* the target interaction, plus the target itself.

    `target` is held here for scoring only. No baseline receives it as an input; the tests
    assert that separately.
    """

    learner_id: int
    prior_total: int
    prior_correct: int
    prior_incorrect: int
    chunk_prior_total: int
    chunk_prior_correct: int
    chunk_prior_incorrect: int
    difficulty: float
    ability: float
    chunk_history: list[bool]     # newest-first, for the rule-engine adapter
    target: bool


def build_features(sequence: list[Interaction]) -> list[FeatureRow]:
    """Expanding-window features: row i sees interactions 0..i-1 only.

    The window expands rather than slides, so nothing after position i can influence the
    prediction for i. `difficulty` and `ability` come from position i's own stored snapshot,
    which was captured before that answer was graded and is therefore also pre-target.
    """
    rows: list[FeatureRow] = []
    prior_correct = prior_incorrect = 0
    chunk_correct: dict[int | None, int] = {}
    chunk_incorrect: dict[int | None, int] = {}
    chunk_history: dict[int | None, list[bool]] = {}

    for event in sequence:
        c_correct = chunk_correct.get(event.chunk_id, 0)
        c_incorrect = chunk_incorrect.get(event.chunk_id, 0)
        rows.append(
            FeatureRow(
                learner_id=event.learner_id,
                prior_total=prior_correct + prior_incorrect,
                prior_correct=prior_correct,
                prior_incorrect=prior_incorrect,
                chunk_prior_total=c_correct + c_incorrect,
                chunk_prior_correct=c_correct,
                chunk_prior_incorrect=c_incorrect,
                difficulty=event.difficulty,
                ability=event.ability,
                # Copy: the rule adapter must not see later mutations of this list.
                chunk_history=list(chunk_history.get(event.chunk_id, [])),
                target=event.is_correct,
            )
        )
        # Update history only AFTER the row is emitted -- this is the leakage boundary.
        if event.is_correct:
            prior_correct += 1
            chunk_correct[event.chunk_id] = c_correct + 1
        else:
            prior_incorrect += 1
            chunk_incorrect[event.chunk_id] = c_incorrect + 1
        chunk_history.setdefault(event.chunk_id, []).insert(0, event.is_correct)  # newest-first

    return rows


def temporal_split(rows: list[FeatureRow], train_fraction: float = 0.7):
    """Per-learner split: earliest `train_fraction` is history, the remainder is evaluated.

    Applied within a learner's own sequence, never across the pooled dataset -- pooling would
    place one learner's future before another learner's past and call it a split.
    """
    cut = int(len(rows) * train_fraction)
    cut = max(1, min(cut, len(rows) - 1)) if len(rows) >= 2 else len(rows)
    return rows[:cut], rows[cut:]


# --------------------------------------------------------------------------------------
# Metrics (pure Python -- the project has no numeric stack, and these are auditable)
# --------------------------------------------------------------------------------------
INSUFFICIENT = "insufficient_data"


def roc_auc(y_true: list[bool], y_score: list[float]):
    """Rank-based AUC (equivalent to the Mann-Whitney U statistic), with tie correction.

    Undefined when either class is absent -- there is nothing to rank against -- and reported
    as insufficient rather than defaulted to 0.5.
    """
    pos = [s for s, y in zip(y_score, y_true) if y]
    neg = [s for s, y in zip(y_score, y_true) if not y]
    if not pos or not neg:
        return INSUFFICIENT
    order = sorted(range(len(y_score)), key=lambda i: y_score[i])
    ranks = [0.0] * len(y_score)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and y_score[order[j + 1]] == y_score[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1
    rank_sum = sum(r for r, y in zip(ranks, y_true) if y)
    n_pos, n_neg = len(pos), len(neg)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def pr_auc(y_true: list[bool], y_score: list[float]):
    """Average precision. Undefined without at least one positive."""
    if not any(y_true):
        return INSUFFICIENT
    pairs = sorted(zip(y_score, y_true), key=lambda p: -p[0])
    tp = fp = 0
    total_pos = sum(1 for y in y_true if y)
    score = 0.0
    for _s, y in pairs:
        if y:
            tp += 1
            score += tp / (tp + fp + 1)  # precision at this recall step
        else:
            fp += 1
    return score / total_pos


def log_loss(y_true: list[bool], y_prob: list[float], eps: float = 1e-15):
    if not y_true:
        return INSUFFICIENT
    total = 0.0
    for y, p in zip(y_true, y_prob):
        p = min(max(p, eps), 1 - eps)
        total += -(math.log(p) if y else math.log(1 - p))
    return total / len(y_true)


def brier_score(y_true: list[bool], y_prob: list[float]):
    if not y_true:
        return INSUFFICIENT
    return sum((p - (1.0 if y else 0.0)) ** 2 for y, p in zip(y_true, y_prob)) / len(y_true)


def calibration_table(y_true: list[bool], y_prob: list[float], bins: int = 5,
                      min_per_bin: int = 5):
    """Reliability table: mean predicted probability vs observed rate, per bin.

    Bins with fewer than `min_per_bin` observations report their count and an explicit
    insufficient marker rather than a rate computed from two or three answers.
    """
    if not y_true:
        return INSUFFICIENT
    buckets: list[list[tuple[bool, float]]] = [[] for _ in range(bins)]
    for y, p in zip(y_true, y_prob):
        idx = min(int(p * bins), bins - 1)
        buckets[idx].append((y, p))
    table = []
    for i, bucket in enumerate(buckets):
        entry = {
            "bin": f"{i / bins:.1f}-{(i + 1) / bins:.1f}",
            "count": len(bucket),
        }
        if len(bucket) < min_per_bin:
            entry["predicted_mean"] = INSUFFICIENT
            entry["observed_rate"] = INSUFFICIENT
        else:
            entry["predicted_mean"] = round(sum(p for _y, p in bucket) / len(bucket), 4)
            entry["observed_rate"] = round(sum(1 for y, _p in bucket if y) / len(bucket), 4)
        table.append(entry)
    return table


# --------------------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------------------
class Baseline:
    name = "base"
    description = ""
    predicts = ""

    def fit(self, train: list[FeatureRow]) -> None:
        """Most baselines here are parameter-free; only the logistic model fits anything."""

    def predict(self, row: FeatureRow) -> float:
        raise NotImplementedError


class CurrentAdaptiveEngineBaseline(Baseline):
    """Adapter over the production rule engine -- NOT a reimplementation of it.

    `adaptive.weighted_accuracy` is imported and called directly, so this evaluates the same
    function the product uses. Nothing in adaptive.py is modified or duplicated.

    Important framing: the production engine emits a *priority band*, not a probability. It was
    never designed as a predictor. To place it on the same axis as the other baselines, its own
    decision statistic -- the recency-weighted accuracy over that chunk's prior answers -- is read
    as P(correct). That is a faithful reading of what the engine believes, but it means a poor
    score here is not evidence that the engine is bad at its actual job.

    With no prior answers for a chunk the engine has no evidence, and this adapter abstains to the
    base rate rather than inventing a confident value.
    """

    name = "current_rule_engine"
    description = (
        "Rule-based decision layer over recency-weighted accuracy "
        f"(half-life {adaptive.RECENCY_HALF_LIFE}, MIN_EVIDENCE {adaptive.MIN_EVIDENCE}). "
        "Not a trained model."
    )
    predicts = "P(correct) read from the engine's own recency-weighted accuracy for that chunk"

    def __init__(self):
        self.base_rate = 0.5

    def fit(self, train: list[FeatureRow]) -> None:
        # The engine itself learns nothing. The base rate is used only for the cold-start
        # abstention case and is taken from the training split, never from evaluation data.
        if train:
            self.base_rate = sum(1 for r in train if r.target) / len(train)

    def predict(self, row: FeatureRow) -> float:
        weighted = adaptive.weighted_accuracy(row.chunk_history)
        if weighted is None:          # never assessed on this chunk -- no evidence
            return self.base_rate
        return weighted / 100.0

    def priority(self, row: FeatureRow) -> str:
        """The engine's actual output, reported alongside the probability for transparency."""
        answered = len(row.chunk_history)
        correct = sum(1 for c in row.chunk_history if c)
        lifetime = adaptive._plain_accuracy(row.chunk_history)
        weighted = adaptive.weighted_accuracy(row.chunk_history)
        priority, _reason = adaptive._classify(answered, correct, lifetime, weighted, None)
        return priority


class EloBaseline(Baseline):
    """Elo expected score from the stored pre-answer snapshots.

    Uses `services._expected_score`, the same function the production Elo update uses, so this
    measures the project's own rating system rather than a re-derivation of it. Both inputs are
    snapshots captured before the target answer was graded.
    """

    name = "elo"
    description = "Elo expected score from pre-answer ability and difficulty snapshots."
    predicts = "P(correct) = 1 / (1 + 10^((difficulty - ability)/400))"

    def predict(self, row: FeatureRow) -> float:
        return _expected_score(row.ability, row.difficulty)


class LogisticBaseline(Baseline):
    """Interpretable logistic regression, fitted by gradient descent in pure Python.

    Deliberately small: four features, L2 regularisation, standardisation from the *training*
    split only. This is a conventional PFA-style baseline, not knowledge tracing -- there is no
    latent knowledge state and no concept layer. It is here to give the rule engine something
    fair to be compared against.
    """

    name = "logistic_pfa"
    description = (
        "Logistic regression on prior counts and pre-answer snapshots. "
        "PFA-style, not BKT and not knowledge tracing."
    )
    predicts = "P(correct | prior correct, prior incorrect, difficulty, ability)"

    FEATURES = ("chunk_prior_correct", "chunk_prior_incorrect", "difficulty", "ability")

    def __init__(self, learning_rate: float = 0.05, epochs: int = 400, l2: float = 0.01):
        self.learning_rate, self.epochs, self.l2 = learning_rate, epochs, l2
        self.weights = [0.0] * len(self.FEATURES)
        self.bias = 0.0
        self.means = [0.0] * len(self.FEATURES)
        self.stds = [1.0] * len(self.FEATURES)
        self.fitted = False

    def _raw(self, row: FeatureRow):
        return [float(getattr(row, name)) for name in self.FEATURES]

    def _standardise(self, values):
        return [(v - m) / s for v, m, s in zip(values, self.means, self.stds)]

    def fit(self, train: list[FeatureRow]) -> None:
        if not train:
            return
        raw = [self._raw(r) for r in train]
        n = len(raw)
        for j in range(len(self.FEATURES)):
            column = [r[j] for r in raw]
            mean = sum(column) / n
            variance = sum((v - mean) ** 2 for v in column) / n
            self.means[j] = mean
            # Guard a constant column -- common at small n, and division by zero would
            # otherwise produce NaN weights that quietly poison every prediction.
            self.stds[j] = math.sqrt(variance) if variance > 1e-12 else 1.0

        X = [self._standardise(r) for r in raw]
        y = [1.0 if r.target else 0.0 for r in train]
        for _epoch in range(self.epochs):
            grad_w = [0.0] * len(self.FEATURES)
            grad_b = 0.0
            for xi, yi in zip(X, y):
                z = self.bias + sum(w * x for w, x in zip(self.weights, xi))
                p = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))
                error = p - yi
                for j in range(len(self.FEATURES)):
                    grad_w[j] += error * xi[j]
                grad_b += error
            for j in range(len(self.FEATURES)):
                self.weights[j] -= self.learning_rate * (grad_w[j] / n + self.l2 * self.weights[j])
            self.bias -= self.learning_rate * (grad_b / n)
        self.fitted = True

    def predict(self, row: FeatureRow) -> float:
        x = self._standardise(self._raw(row))
        z = self.bias + sum(w * v for w, v in zip(self.weights, x))
        return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


DEFAULT_BASELINES = (CurrentAdaptiveEngineBaseline, EloBaseline, LogisticBaseline)


# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------
def evaluate(queryset=None, train_fraction: float = 0.7,
             thresholds: SufficiencyThresholds | None = None, baselines=DEFAULT_BASELINES):
    """Run the full harness and return a machine-readable result dictionary."""
    thresholds = thresholds or SufficiencyThresholds()
    sequences, extraction = load_interactions(queryset, thresholds)

    train_rows: list[FeatureRow] = []
    eval_rows: list[FeatureRow] = []
    for events in sequences.values():
        rows = build_features(events)
        train, test = temporal_split(rows, train_fraction)
        train_rows.extend(train)
        eval_rows.extend(test)

    y_true = [r.target for r in eval_rows]
    positives = sum(1 for y in y_true if y)
    negatives = len(y_true) - positives

    blockers = []
    if len(eval_rows) < thresholds.min_eval_samples:
        blockers.append(
            f"evaluation split has {len(eval_rows)} interactions, "
            f"below the {thresholds.min_eval_samples} required"
        )
    if min(positives, negatives) < thresholds.min_per_class:
        blockers.append(
            f"minority class has {min(positives, negatives)} observations, "
            f"below the {thresholds.min_per_class} required"
        )
    if extraction.learners < thresholds.min_learners:
        blockers.append(
            f"{extraction.learners} learners with usable sequences, "
            f"below the {thresholds.min_learners} required"
        )
    status = "INSUFFICIENT_DATA" if blockers else "OK"

    result = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": status,
        "blockers": blockers,
        "dataset": {
            "total_rows": extraction.total_rows,
            "usable": extraction.usable,
            "excluded_synthetic": extraction.excluded_synthetic,
            "excluded_no_timestamp": extraction.excluded_no_timestamp,
            "excluded_no_snapshot": extraction.excluded_no_snapshot,
            "excluded_short_sequence": extraction.excluded_short_sequence,
            "learners": extraction.learners,
            "sequence_lengths": extraction.sequence_lengths,
            "train_interactions": len(train_rows),
            "eval_interactions": len(eval_rows),
            "eval_positives": positives,
            "eval_negatives": negatives,
        },
        "split": {
            "type": "per-learner temporal",
            "train_fraction": train_fraction,
            "ordering_field": "answered_at",
            "note": "No random splitting. A learner's later answers are never used to predict "
                    "their earlier ones.",
        },
        "thresholds": asdict(thresholds),
        "leakage_controls": [
            "Features for interaction i are built from interactions 0..i-1 only.",
            "Difficulty and ability come from AttemptAnswer snapshots captured before the "
            "Elo update, never from the live Question/TopicMastery ratings.",
            "Rows without answered_at or without snapshots are excluded, never imputed.",
            "Synthetic demo interactions (QuizAttempt.is_synthetic) are excluded by default.",
            "The target (is_correct) is never exposed to any baseline as an input.",
        ],
        "baselines": {},
    }

    for cls in baselines:
        model = cls()
        model.fit(train_rows)
        entry = {
            "description": model.description,
            "predicts": model.predicts,
        }
        if eval_rows:
            y_prob = [model.predict(r) for r in eval_rows]
            entry["metrics"] = {
                "roc_auc": _round(roc_auc(y_true, y_prob)),
                "pr_auc": _round(pr_auc(y_true, y_prob)),
                "log_loss": _round(log_loss(y_true, y_prob)),
                "brier": _round(brier_score(y_true, y_prob)),
            }
            entry["calibration"] = calibration_table(y_true, y_prob)
        else:
            entry["metrics"] = {k: INSUFFICIENT for k in ("roc_auc", "pr_auc", "log_loss", "brier")}
            entry["calibration"] = INSUFFICIENT
        if status == "INSUFFICIENT_DATA":
            entry["interpretation"] = (
                "Computed for mechanism verification only. NOT a performance claim -- the "
                "dataset is below the documented sufficiency thresholds."
            )
        result["baselines"][model.name] = entry

    return result


def _round(value, places: int = 4):
    return value if isinstance(value, str) else round(value, places)


def to_markdown(result: dict) -> str:
    """Human-readable report. Deliberately leads with the status, so a reader cannot mistake
    mechanism-verification numbers for results."""
    d = result["dataset"]
    lines = [
        "# Adaptive Evaluation Report",
        "",
        f"**Generated:** {result['generated_at']}",
        f"**Status:** `{result['status']}`",
        "",
    ]
    if result["status"] == "INSUFFICIENT_DATA":
        lines += [
            "> ## ⚠️ NO VALID MODEL PERFORMANCE CLAIM CAN CURRENTLY BE MADE",
            ">",
            "> The harness ran correctly end to end. The dataset is below the documented",
            "> sufficiency thresholds, so any metric below reflects the mechanism working, not",
            "> the quality of a model. Blockers:",
            ">",
        ] + [f"> - {b}" for b in result["blockers"]] + [""]

    lines += [
        "## Dataset",
        "",
        "| Quantity | Value |",
        "|---|---|",
        f"| Total interaction rows | {d['total_rows']} |",
        f"| Usable (temporally valid) | {d['usable']} |",
        f"| Excluded — synthetic demo data | {d['excluded_synthetic']} |",
        f"| Excluded — no `answered_at` | {d['excluded_no_timestamp']} |",
        f"| Excluded — no Elo snapshot | {d['excluded_no_snapshot']} |",
        f"| Excluded — sequence too short | {d['excluded_short_sequence']} |",
        f"| Learners with usable sequences | {d['learners']} |",
        f"| Train / evaluation interactions | {d['train_interactions']} / {d['eval_interactions']} |",
        f"| Evaluation positives / negatives | {d['eval_positives']} / {d['eval_negatives']} |",
        "",
        "## Split",
        "",
        f"- **Type:** {result['split']['type']}",
        f"- **Ordering field:** `{result['split']['ordering_field']}`",
        f"- **Train fraction:** {result['split']['train_fraction']}",
        f"- {result['split']['note']}",
        "",
        "## Leakage controls",
        "",
    ] + [f"- {c}" for c in result["leakage_controls"]] + ["", "## Baselines", ""]

    for name, entry in result["baselines"].items():
        lines += [
            f"### `{name}`",
            "",
            f"{entry['description']}",
            "",
            f"**Predicts:** {entry['predicts']}",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for metric, value in entry["metrics"].items():
            lines.append(f"| {metric} | {value} |")
        if "interpretation" in entry:
            lines += ["", f"> {entry['interpretation']}"]
        lines.append("")

    lines += [
        "## What this report is and is not",
        "",
        "- **Implemented:** temporal extraction, leakage-safe features, per-learner temporal "
        "split, three baselines, four metrics, calibration, sufficiency gating.",
        "- **Measured:** only what the Dataset table states.",
        "- **Not yet possible:** any claim that one approach outperforms another, until the "
        "sufficiency thresholds are met with real learner data.",
        "",
        "No knowledge-tracing model is implemented. Elo and the logistic baseline are "
        "conventional predictors used for comparison, not knowledge tracing.",
    ]
    return "\n".join(lines)


def to_json(result: dict) -> str:
    return json.dumps(result, indent=2, default=str)
