# Adaptive Learning — Algorithm Specification

The algorithm **as implemented**, after the review-readiness sprint. Every formula, threshold
and behaviour below is read from `backend/attempts/adaptive.py` and covered by tests.

Supersedes the algorithm sections of `ADAPTIVE_LEARNING.md`, which described the pre-sprint
lifetime-accuracy version.

---

## 1. The two questions, kept separate

| | **Adaptive selection** | **Spaced repetition** |
|---|---|---|
| Answers | *Which* content? | *When*? |
| Module | `attempts/adaptive.py` | `attempts/fsrs.py` (FSRS-4.5) |
| Signal | Recency-weighted accuracy per section | Pass/fail grade + elapsed days |
| Output | `high` / `medium` / `low` / `none` | `next_eligible_at` |

They cannot be merged. FSRS retrievability is `R(0, S) = 1.0` for **every** stability value, so
immediately after an attempt a just-failed and a just-passed section score identically.
Retrievability answers "is it time?"; accuracy answers "is this learner weak?".

**They are now reconciled** — see §5.

---

## 2. Learning unit

```text
SOPDocument        ← TopicMastery   (learner × SOP)
    │
    ├── SOPChunk   ← ChunkMastery   (learner × section)   ◀ the adaptive unit
    │       │
    │       └── Question.source_chunk (FK)
    │               │
    │               └── AttemptAnswer.is_correct   ◀ the only raw signal
```

Questions with `source_chunk = NULL` are collected into one explicit "Questions not linked to a
section" bucket, never dropped.

---

## 3. The decision metric — recency-weighted accuracy

### Formula

For a section's answers ordered newest-first, with `i = 0` the most recent:

```text
w_i       = 0.5 ** (i / HALF_LIFE)          HALF_LIFE = 5
weighted  = Σ(w_i · correct_i) / Σ(w_i) × 100
```

### Why weighted rather than lifetime

A flat lifetime average cannot represent *change*. A learner who answered 0/5 and then 5/5 has
plainly improved, but their lifetime accuracy is 50% — so under a flat average they stayed
flagged HIGH while the UI displayed "Recent: 100%" beside that verdict. The screen contradicted
itself, and the learner could not shed a weak label without an implausible run of successes.

### Why half-life = 5

Five is the window already used for the displayed "recent accuracy", so *recent* means exactly
one half-life throughout the system. An answer five answers ago counts half as much as the
newest one.

### Verified behaviour

| Scenario (chronological) | Lifetime | Weighted | Effect |
|---|---|---|---|
| 0/5 then 5/5 (improving) | 50.0% | **66.7%** | HIGH → MEDIUM |
| …then 5 more correct | 66.7% | **85.7%** | MEDIUM → LOW |
| 5/5 then 0/5 (declining) | 50.0% | **33.3%** | flagged sooner than lifetime would |
| alternating (no trend) | 50.0% | ~53.5% | barely moved — as intended |
| 1 correct after 9 wrong | 10.0% | <30% | one good answer is not a reset button |

Improvement and deterioration are both recognised, and neither instantly — the correct
behaviour for a compliance-training record.

**Lifetime accuracy is still computed, stored and displayed.** It is the honest long-run figure;
the weighted score is the decision figure. Both appear in the API and the UI, and the `reason`
string always names the one the decision was made on.

---

## 4. Evidence sufficiency

```text
MIN_EVIDENCE = 3
```

Below three answers a section **cannot** be classified LOW or NONE on accuracy alone. It is
capped at MEDIUM with an explicit reason:

> "100.0% accuracy (1/1 correct), but only 1 assessment(s) so far — insufficient evidence to
> rule this section out (needs 3)."

**Deliberately asymmetric.** Weak performance on a small sample stays HIGH: over-training costs a
few extra questions, under-training produces an unqualified operator.

Three is the smallest sample that can show a trend at all. It is a chosen default, not a fitted
value — with real deployment data it is the first constant that should be tuned.

`answered == 0` remains a **distinct** state ("never assessed"), checked first, so absence of
data is never mistaken either for competence or for measured weakness.

---

## 5. Priority classification

Checked in this order:

| # | Condition | Priority | Selected |
|---|---|---|---|
| 1 | `answered == 0` | **HIGH** | ✅ never assessed |
| 2 | `mastery_status == "mastered"` | NONE | ❌ retired |
| 3 | `answered < 3` **and** weighted ≥ 60% | **MEDIUM** | ✅ insufficient evidence |
| 4 | weighted < 60% | **HIGH** | ✅ weak |
| 5 | weighted < 80% | **MEDIUM** | ✅ below pass mark |
| 6 | otherwise | LOW | ❌ |

Thresholds: 80% is `MasteryState.PASS_THRESHOLD`, the pass mark used system-wide. 60% is a
chosen default marking "clearly struggling" — the first constant to tune with real data.

Ordering: priority band, then weakest weighted accuracy first; never-assessed sorts ahead of
measured.

---

## 6. Reconciliation with FSRS

Each section carries both verdicts:

```text
selected_for_retraining   WHAT the adaptive engine wants trained
is_due                    WHEN this section's own FSRS schedule permits it
available_now             both  →  the learner can start it right now
```

- **`auto_assigned_retraining`** offers only `available_now` sections. It can only hand over
  training that is due.
- **`learning_path`** reports *all* selected sections, each labelled "Available now" or
  "Scheduled for <date>". Weak material is never hidden — it is honestly scheduled.

This closed a verified defect: a HIGH-priority section in a not-yet-due SOP was recommended by
the learning path while the assignment engine offered nothing, so a learner clicking through hit
an empty quiz screen.

**Section-level scheduling is now consumed.** `ChunkMastery.next_eligible_at` was previously
computed and never read; assignment waited for the whole SOP to come due, delaying exactly the
section FSRS had scheduled soonest (FSRS schedules a failed section sooner than a passed one —
verified: 14 Aug vs 16 Aug from a single attempt). A SOP is now a candidate if **either** its own
schedule **or** any of its sections' schedules is due.

---

## 7. Mastery state (unchanged this sprint)

Per completed attempt, at both grains:

```text
pass signal = confidence-filtered, Elo-weighted accuracy ≥ 80%
  ├─ questions with LLM confidence < 0.5 excluded (unless >half would drop)
  └─ each weighted 1.0–2.0 by live Elo difficulty

pass → streak += 1  (3 ⇒ mastered);  fail → streak = 0
FSRS review(grade) → (stability, difficulty) → next_eligible_at
Elo: learner K=32, question K=16; section-level updates are ability-only
```

Note two distinct notions of "mastery" coexist by design: `mastery_status` is streak-based
(*"has this learner passed repeatedly?"*) and priority is accuracy-based (*"is this learner weak
right now?"*). A section can be `in_progress` with 100% accuracy and LOW priority.

---

## 8. Learning gain

For sections with ≥ 4 answers, the newest half is compared with the oldest half:

```text
initial_accuracy   oldest half
current_accuracy   newest half
improvement        current − initial   (percentage points)
```

Computed from stored `AttemptAnswer` rows, never fabricated. This is what turns "CAPA is now at
75%" into "CAPA went from 50% to 100%, +50 points" — the visible proof the adaptive loop closed.

---

## 9. Explainability contract

Every section returns: `answered`, `correct`, `accuracy` (lifetime), `weighted_accuracy`,
`recent_accuracy`, `evidence_sufficient`, `initial_accuracy`, `current_accuracy`,
`improvement`, `mastery_status`, `streak_correct`, `elo_rating`, `memory_stability_days`,
`next_eligible_at`, `is_due`, `priority`, `reason`, `question_ids`,
`selected_for_retraining`, `available_now`.

The `reason` is generated from the same numbers that drove the decision, and names the deciding
metric explicitly when it differs from lifetime:

```text
"33.3% recency-weighted accuracy (5/10 correct overall = 50.0% lifetime) - below the
 60% weakness threshold."
```

**The UI may not display a metric that contradicts the priority beside it.** That rule is the
reason the weighted figure is labelled "Adaptive score" in the interface.

---

## 10. Known limitations

1. **Difficulty does not affect priority.** Elo weights the *pass signal* but not the accuracy
   used for selection, so an easy miss and a hard miss are treated the same.
2. **No mastery decay by elapsed time.** Time enters only through FSRS scheduling; a section
   assessed a year ago at 90% still reads LOW.
3. **Thresholds are chosen defaults**, not fitted to data.
4. **Half-life is global**, not per-learner or per-SOP.
5. **No cross-SOP prioritisation** — sections rank within a SOP.
6. **Adaptation is between assessments, not within one.** Item-level adaptive testing (CAT)
   would need calibrated item parameters this data scale cannot support.
7. **The selected question set is still advisory at submission time.** Submitted questions are
   now validated to belong to the attempt's SOP and to be approved, but there is no server-side
   record of which questions were *offered* — see `FUTURE_SCOPE.md`.
