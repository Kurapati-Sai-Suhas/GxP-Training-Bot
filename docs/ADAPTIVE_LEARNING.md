# Adaptive Learning — Algorithm Specification

Documents the algorithm **as implemented**. Every formula, threshold and behaviour below is
read from source and covered by tests. File references are to `backend/attempts/`.

> [`ADAPTIVE_ALGORITHM.md`](ADAPTIVE_ALGORITHM.md) is the canonical short-form specification of
> the same algorithm. This document is the longer treatment with rationale; where the two are
> ever in tension, the source in `attempts/adaptive.py` decides.

---

## 1. Problem

Static training is `SOP → fixed quiz → score`. Three things it cannot do:

- **Distinguish *which part* of a procedure a learner failed.** A score of 70% on a 10-section
  SOP identifies a person, not a knowledge gap.
- **Stop re-testing material already known.** Every retake replays the whole document.
- **Decide *when* to re-test.** Fixed intervals treat a barely-passed section and a
  thoroughly-known one identically.

This system addresses each with a different mechanism: chunk-level attribution, accuracy-driven
selection, and FSRS scheduling.

---

## 2. Learning unit

The system learns at **two** grains, and only two. There is no course/module hierarchy — do not
claim one.

```text
SOPDocument        ← TopicMastery   (learner × SOP)
    │
    ├── SOPChunk   ← ChunkMastery   (learner × section)   ◀ the adaptive unit
    │       │
    │       └── Question.source_chunk (FK, SET_NULL)
    │               │
    │               └── AttemptAnswer.is_correct   ◀ the raw signal
```

- **`SOPChunk`** is the adaptive unit. Chunks come from the three-tier chunking cascade
  (heading-aware → semantic embeddings → fixed-length), so a chunk is normally one titled
  section of the procedure.
- **`Question`** is *not* a mastery unit. Questions are evidence about their `source_chunk`.
  Individual question difficulty is tracked separately as `Question.elo_rating`.
- **Unlinked questions** (`source_chunk IS NULL` — manually authored, or whose chunk was later
  deleted) are grouped into one explicit pseudo-section, "Questions not linked to a section".
  They are never silently dropped.

`TopicMastery` and `ChunkMastery` both inherit the abstract `MasteryState`
(`models.py:39`), so both grains share identical update logic. **`TopicMastery` is not derived
from `ChunkMastery`** — they are independent signals computed from the same attempt, because a
SOP can contain unlinked questions that would otherwise make whole-SOP mastery unreachable.

---

## 3. Learner signal

Exactly one raw signal is recorded: **`AttemptAnswer.is_correct`**, one row per question per
attempt, graded server-side from `Option.is_correct`.

Everything else is derived from it:

| Derived signal | Where | Used for |
|---|---|---|
| **Recency-weighted accuracy per chunk** | `adaptive.weighted_accuracy` | **selection priority** |
| Lifetime accuracy per chunk | `adaptive._plain_accuracy` | display / honest long-run figure |
| Recent accuracy (last `RECENT_WINDOW`) | `adaptive._plain_accuracy` over the newest slice | display / trend |
| Answer count per chunk | `adaptive._answer_history_by_chunk` | evidence sufficiency (`MIN_EVIDENCE`) |
| Learning gain (oldest vs newest half) | `adaptive._learning_gain` | display / proof the loop closed |
| Pass/fail per attempt | `views._pass_signal_from_pairs` | mastery + FSRS grade |
| Streak of passes | `MasteryState.streak_correct` | mastery status |
| Elo ability | `MasteryState.elo_rating` | difficulty suggestion |
| Elo difficulty | `Question.elo_rating` | pass-signal weighting |
| FSRS stability/difficulty | `MasteryState.fsrs_*` | next review date |

**Not used:** time-on-page, engagement, self-report, click behaviour, question ordering.

### 3.1 The decision metric — recency-weighted accuracy

For a section's answers ordered newest-first, with `i = 0` the most recent:

```text
w_i       = 0.5 ** (i / RECENCY_HALF_LIFE)      RECENCY_HALF_LIFE = 5
weighted  = Σ(w_i · correct_i) / Σ(w_i) × 100
```

A flat lifetime average cannot represent *change*: a learner who answered 0/5 then 5/5 sits at
50% lifetime, so they would stay flagged weak while the interface displayed "Recent: 100%"
beside that verdict. Half-life 5 matches the window already used for the displayed recent
accuracy, so "recent" means exactly one half-life throughout the system.

Verified values:

| Sequence (chronological) | Lifetime | Weighted |
|---|---|---|
| 0/5 then 5/5 (improving) | 50.0% | **66.7%** |
| …then 5 more correct | 66.7% | **85.7%** |
| 5/5 then 0/5 (declining) | 50.0% | **33.3%** |
| no trend (alternating) | 50.0% | ~53.5% |

Improvement and deterioration are both recognised, and neither instantly — one good answer is
not a reset button. Lifetime accuracy is still stored and displayed; the `reason` string always
names whichever figure the decision used.

---

## 4. Mastery calculation

### 4.1 Pass signal (per attempt, per grain)

Not the raw percentage. `views._pass_signal_from_pairs`:

```text
trustworthy = [q for q in answers if q.confidence_score is None
                                  or q.confidence_score >= 0.5]
if len(trustworthy) < max(1, len(answers) // 2):
    trustworthy = answers          # too few left to judge fairly — use everything

weight(q)   = 1.0 + clamp((q.elo_rating - 1300) / (1700 - 1300), 0, 1)   # → 1.0 .. 2.0

passed = (Σ weight(q) for correct q) / (Σ weight(q) for all q) * 100 >= 80
```

Two deliberate adjustments:

- **Confidence filter** — a wrong answer on a *low-confidence* AI-drafted question (likely
  ambiguous) does not reset a learner's schedule. LLM self-reported confidence is known to be
  miscalibrated (Geng et al., NAACL 2024), so it is used only as a exclusion hint, never as a
  score.
- **Elo weighting** — a hard question answered correctly counts up to twice an easy one. The
  weight comes from the question's *live* rating, not its one-time difficulty label, so it
  tracks real observed difficulty. Floor/ceiling reuse `Question.DIFFICULTY_SEED_ELO`, so a
  never-answered question reproduces the pre-Elo behaviour exactly.

### 4.2 Mastery state update

`MasteryState.apply_answer(is_correct)` (`models.py:94`), once per completed attempt:

```text
elapsed_days = now - updated_at            # updated_at still holds the PREVIOUS save
stability, difficulty = fsrs.review(stability, difficulty, elapsed_days, passed)

if passed:
    streak += 1
    box_index = min(box_index + 1, 5)
    if streak >= 3: status = "mastered"
else:
    streak = 0
    box_index = 0
    status = "in_progress"

next_eligible_at = now + fsrs.next_review_interval_days(stability)
```

`box_index` is retained as a display/audit signal only — it no longer drives scheduling.

### 4.3 Elo update

`services.py`, per answered question:

```text
expected = 1 / (1 + 10^((question_rating - learner_rating) / 400))
learner_rating += 32 * (actual - expected)      # K = 32, adapts fast
question_rating += 16 * (expected - actual)     # K = 16, shared across learners
```

Section-level updates use `apply_elo_update_ability_only`, which moves **only** the learner's
rating. Otherwise a single answer would move the question's difficulty twice — once for
`TopicMastery` and once for `ChunkMastery`. There is a regression test for exactly this.

---

## 5. Adaptive selection

`adaptive.py`. This decides **which content the learner sees next**.

### 5.1 Priority classification

Per section with approved questions, checked in this order:

The decision metric is **recency-weighted accuracy** (§3.1), not the flat lifetime average.

| # | Condition | Priority | Selected | Rationale |
|---|---|---|---|---|
| 1 | `answered == 0` | **HIGH** | ✅ | Never assessed. Checked *first* so absence of data is never read as competence |
| 2 | `mastery_status == "mastered"` | NONE | ❌ | 3 consecutive passes — retired |
| 3 | `answered < 3` **and** weighted ≥ 60% | **MEDIUM** | ✅ | Insufficient evidence to exclude |
| 4 | weighted `< 60%` | **HIGH** | ✅ | Genuine measured weakness |
| 5 | weighted `< 80%` | **MEDIUM** | ✅ | Below the pass mark used everywhere else |
| 6 | otherwise | LOW | ❌ | Performing well, not yet mastered |

Thresholds: `WEAK_ACCURACY_THRESHOLD = 60.0`, `PROFICIENT_ACCURACY_THRESHOLD = 80.0`
(matches `MasteryState.PASS_THRESHOLD`), `MIN_EVIDENCE = 3`, `RECENCY_HALF_LIFE = 5.0`.
`RECENT_WINDOW = 5` is display-only — it produces the "recent accuracy" figure shown in the UI
and does not enter the decision.

Rule 3 is deliberately asymmetric: high accuracy on fewer than three answers is capped at MEDIUM
("insufficient evidence"), while *weak* performance on a small sample falls through to rule 4 and
stays HIGH. Under-training produces an unqualified operator; over-training costs a few questions.

Boundaries fall **upward**: exactly 60.0 is MEDIUM, exactly 80.0 is LOW.

Sort order: priority band, then weakest accuracy first; never-assessed sorts ahead of measured.

### 5.2 The mastered-topic asymmetry

`training_sections(sections, topic_mastered)`:

> When whole-SOP `TopicMastery` is `mastered`, that judgement is trusted **unless there is
> measured evidence against it**. A section with recorded wrong answers still reopens the topic;
> a never-assessed section alone does not.

Without this, any mastered SOP containing a section the learner happened never to be asked about
would be re-offered forever, and mastery would never retire anything.

### 5.3 Unlinked questions

Collected into one bucket keyed `chunk_id = None`, classified by the same rules. Excluding them
would make an entire SOP invisible to retraining whenever none of its questions carry chunk
linkage.

### 5.4 Where selection is consumed

`views.auto_assigned_retraining` — for each SOP where **either** its own `TopicMastery` schedule
**or** any of its sections' `ChunkMastery` schedules is due:

```text
sections      = adaptive.analyse_sections(learner, sop, job_role)
if not adaptive.needs_training(sections, topic_mastered, only_available=True): skip this SOP
question_ids  = adaptive.select_retraining_questions(sections, topic_mastered, only_available=True)
```

Two things to note:

- The whole-SOP `mastered` exclusion was **removed** from the candidate query. Whether there is
  anything to train is decided per section.
- `only_available=True` means the assignment engine hands over only sections whose own FSRS
  schedule is due. Sections that are weak but not yet due are still *reported* by
  `learning_path`, labelled with their scheduled date — see §6.

---

## 6. Spaced repetition — a separate concern

**Adaptive selection and spaced repetition are different algorithms answering different
questions. They are not the same mechanism.**

| | Adaptive selection | Spaced repetition |
|---|---|---|
| Question | *Which* content? | *When*? |
| Algorithm | Recency-weighted accuracy thresholds (`adaptive.py`) | FSRS-4.5 (`fsrs.py`) |
| Input | Recency-weighted accuracy per section (half-life 5 answers) | Pass/fail grade + elapsed days |
| Output | Priority + question ids | `next_eligible_at` |
| Grain | Section | Section and SOP |

The two verdicts are combined, not merged: each section reports `selected_for_retraining` (WHAT)
and `is_due` (WHEN), and `available_now` is their conjunction. The learning path shows weak but
not-yet-due sections under "Scheduled for Later" with the date; the assignment engine offers only
`available_now` sections. Weak material is never hidden — only honestly scheduled.

### Why accuracy drives selection and FSRS does not

FSRS retrievability *looks* like the natural "how well is this known" number. It cannot drive
selection:

```text
R(elapsed, S) = (1 + (19/81) · elapsed / S) ^ -0.5
R(0, S) = 1.0     for every S
```

Immediately after an attempt, `elapsed = 0`, so a section just **failed** and a section just
**passed** both score 1.0. Retrievability answers *"is it time to review?"*; accuracy answers
*"is this learner weak here?"*.

### FSRS specifics

FSRS-4.5 with published default weights. Only two of four grades are used — `AGAIN` (failed)
and `GOOD` (passed) — because this app has no Hard/Easy signal. Per-user parameter optimisation
is deliberately not attempted: it needs far more logged reviews per learner than a deployment
this size generates.

Interval: the point where modelled recall decays to 90%, floored at 1 day.

**Observed in the demo run:** from a single attempt, failed sections were scheduled for
**2026-08-13** and the passed section for **2026-08-15** — FSRS bringing weak material back
sooner, with no special-casing.

---

## 7. Explainability

Every selection carries the numbers that produced it. `GET /api/attempts/learning-path/`
returns per section: `answered`, `correct`, `accuracy`, `recent_accuracy`, `mastery_status`,
`streak_correct`, `elo_rating`, `memory_stability_days`, `next_eligible_at`, `priority`,
`reason`, `selected_for_retraining`.

The `reason` string is generated from the same values that drove the decision, e.g.:

```text
"0.0% accuracy (0/2 correct) - below the 60% weakness threshold."
"Mastered - 3 passing assessments in a row (75.0% lifetime accuracy across 8 answers)."
"Never assessed - no completed attempt has covered this section yet."
```

The chain is fully traceable:

```text
AttemptAnswer.is_correct
   → Question.source_chunk
      → per-chunk accuracy
         → priority + reason
            → selected question ids
               → the learner's next quiz
```

Surfaced in the UI at **My Learning Path**, scoped server-side to `request.user`.

---

## 8. Tests

| Area | Class | Count |
|---|---|---|
| Controlled GMP/CAPA/Documentation scenario | `AdaptiveLearningScenarioTests` | 7 |
| Recency-weighted metric (pure) | `RecencyWeightedAccuracyTests` | 9 |
| Recency end-to-end | `AdaptiveRecencyIntegrationTests` | 5 |
| Evidence sufficiency (0/1/2/3+ answers) | `EvidenceSufficiencyTests` | 8 |
| Adaptive ↔ FSRS reconciliation | `AdaptiveScheduleReconciliationTests` | 4 |
| Two-learner divergence | `TwoLearnerPersonalisationTests` | 2 |
| Explainability output | `LearningPathExplainabilityTests` | 10 |
| Section mastery | `SectionMasteryTests` | 7 |
| Elo | `AdaptiveEloRatingTests` | 5 |
| FSRS pure functions | `FSRSAlgorithmTests` | 7 |
| FSRS integration | `AdaptiveFSRSSchedulingTests` | 3 |
| Retraining / escalation | `AdaptiveRetrainingTests` | 12 |

---

## 9. Known limitations

1. **Decay is by answer position, not elapsed time.** Ten answers ago weighs the same whether it
   was yesterday or last year; calendar time enters only through FSRS scheduling.
2. **Thresholds and the half-life are chosen constants**, not fitted, and are global rather than
   per-SOP or per-learner.
3. **No cross-SOP prioritisation** — sections are ranked within a SOP, and SOPs are processed in
   due-date order.
4. **Classification runs on the rounded metric.** `weighted_accuracy()` rounds to one decimal
   before comparison, so a raw 59.97% is treated as 60.0% (MEDIUM rather than HIGH). A ~0.05pp
   band is one level too lenient.
5. **Difficulty does not affect priority.** Elo weights the pass signal but not the accuracy used
   for selection, so an easy miss and a hard miss count the same.
6. **Reprocessing an SOP still destroys `ChunkMastery`** (cascade). Currently *blocked* when
   approved questions exist; the real fix is SOP versioning.
7. **Evidence is a count, not a confidence interval.** `MIN_EVIDENCE = 3` prevents a tiny sample
   from excluding a section, but 3 answers and 50 answers are treated identically above that
   floor. A Wilson bound would be the fuller answer.
8. **Selection is not yet binding at submission time.** Submitted questions are validated to
   belong to the attempt's SOP and to be approved, but no server-side record pins the exact
   offered set — see [`FUTURE_SCOPE.md`](FUTURE_SCOPE.md) §1.

*(Resolved since this document was first written: the per-section `RECENT_WINDOW` query — the
ordered answer history is now fetched once in `_answer_history_by_chunk`.)*
