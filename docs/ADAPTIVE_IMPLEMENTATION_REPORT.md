# Adaptive Learning — Implementation Report

What the review-readiness sprint changed, why, and what was deliberately left alone.

**Baseline:** 176 tests passing · **Result:** 219 tests passing, 0 failed, 0 skipped.
No test was deleted. Four were updated — each explained in §4.

---

## 1. What changed

| # | Change | Files |
|---|---|---|
| 1 | Recency-weighted accuracy replaces lifetime accuracy as the *decision* metric | `attempts/adaptive.py` |
| 2 | Evidence-sufficiency gate (`MIN_EVIDENCE = 3`) | `attempts/adaptive.py` |
| 3 | Adaptive ↔ FSRS reconciliation (`is_due`, `available_now`) | `attempts/adaptive.py`, `attempts/views.py` |
| 4 | Section-level FSRS scheduling now actually consumed | `attempts/views.py` |
| 5 | Per-section learning gain | `attempts/adaptive.py` |
| 6 | Submitted question ids validated | `attempts/views.py` |
| 7 | Near-duplicate (reworded) question detection | `ai_engine/tasks.py` |
| 8 | Grounding regression tests (the missing producer test) | `ai_engine/tests.py` |
| 9 | UI: availability labelling, adaptive score, learning gain | `frontend/src/App.jsx`, `app.css` |
| 10 | Demo: 3 questions/section, both schedule levels, gain output | `demo_adaptive.py` |

**No migrations.** Every change is behavioural or additive at the API layer, which is why this
was safe to do two days before a review.

## 2. Why — the five reviewer questions the audit identified

### Q1 "Recent accuracy is 100%, why is the learner still HIGH?"

**Was:** priority used lifetime accuracy. A learner going 0/5 → 5/5 sat at 50% lifetime and
stayed HIGH, while the UI rendered "Recent: 100%" directly beside that verdict.

**Now:** priority uses exponentially recency-weighted accuracy (half-life 5 answers). The same
learner scores 66.7% and moves HIGH → MEDIUM; five more correct answers reach 85.7% → LOW. A
declining learner is caught *sooner* than lifetime would catch them (33.3% vs 50%).

**Verified:** `RecencyWeightedAccuracyTests` (9), `AdaptiveRecencyIntegrationTests` (5).

### Q2 "You recommended CAPA, but the quiz had nothing to do."

**Was:** `learning_path` ignored due-ness; `auto_assigned` required it. A HIGH-priority section
in a not-yet-due SOP was recommended and then not offered.

**Now:** every section carries `is_due` and `available_now`. The learning path splits
"Recommended Next — available now" from "Scheduled for Later — due 14 Aug"; the assignment engine
offers only what is available. Weak material is never hidden, only honestly scheduled.

**Verified in the browser both ways.** Not due → "Nothing is due right now" + "Scheduled for
Later, Due Aug 14" + 0 assignments. Forced due → "Available now" + "2 available now" + Learner
Quiz shows "Continue Assigned Retraining".

**Verified:** `AdaptiveScheduleReconciliationTests` (4), including
`test_learning_path_and_auto_assigned_agree`.

### Q3 "Why do 1/1 and 50/50 give the same confidence?"

**Was:** identical — both LOW.

**Now:** below 3 answers a section cannot be excluded on accuracy. 1/1 → MEDIUM with
*"insufficient evidence to rule this section out (needs 3)"*. Asymmetric on purpose: weak
performance on a small sample stays HIGH, because under-training is the costlier error.

**Verified:** `EvidenceSufficiencyTests` (8) — the full 0/1/2/3+ × high/low-accuracy matrix.

### Q4 "If the browser ignores your question ids, what enforces the decision?"

**Partially addressed, honestly.** `submit()` now rejects any question that does not belong to
the attempt's SOP or is not approved, with `400` and the offending ids. A rejected submission
does not consume the attempt and does not touch mastery.

**Still advisory:** there is no server-side record of which questions were *offered*, so the
targeted subset is still honoured by the client. The full fix (a `QuizAttemptQuestion` session)
requires a migration and was judged too risky before the review — see `FUTURE_SCOPE.md` §1.

**Verified:** `SubmissionValidationTests` (6).

### Q5 "These two questions are the same, reworded."

**Was:** exact-signature matching only.

**Now:** lexical near-duplicate detection with asymmetric thresholds — the **correct answer**
identifies which fact is tested (bar 0.8), the **stem** confirms the subject (bar 0.4).

The asymmetry was forced by evidence: "What must be done before batch release?" and "Prior to
batch release, what is required?" share only 0.40 stem overlap despite being the same question.
A symmetric 0.75 bar missed exactly the case exact matching already missed.

No new dependency — an embedding call per candidate would add latency and make de-duplication
depend on the provider being reachable, which the pipeline is designed not to require.

**Verified:** `NearDuplicateDetectionTests` (6), covering the catches *and* the non-catches
(same topic/different objective, same stem/different answer).

### Plus: the untested link the whole claim rests on

`Question.source_chunk` had **no test proving generation populates it**. Every adaptive test set
it by hand in fixtures — testing the consumer, never the producer. It could have broken with all
176 tests still green, silently dropping every question into the "unlinked" bucket.

**Now:** `GenerationGroundingTests` (3) prove it for the live path, the offline fallback, and
that the linked chunk is the one whose text was actually sent to the model.

## 3. Tests added

| Suite | Tests | Covers |
|---|---:|---|
| `RecencyWeightedAccuracyTests` | 9 | the metric itself (pure) |
| `AdaptiveRecencyIntegrationTests` | 5 | improving / declining / reason string / gain |
| `EvidenceSufficiencyTests` | 8 | 0,1,2,3+ answers × accuracy |
| `AdaptiveScheduleReconciliationTests` | 4 | priority ↔ FSRS agreement |
| `TwoLearnerPersonalisationTests` | 2 | different learners → different content |
| `SubmissionValidationTests` | 6 | foreign / unapproved / malformed ids |
| `GenerationGroundingTests` | 3 | source_chunk actually populated |
| `NearDuplicateDetectionTests` | 6 | reworded duplicates, no false positives |
| — | **43** | 176 → 219 |

## 4. Tests changed — and why

Four existing tests were updated. None was changed to force a pass; each reflects a legitimate
behaviour change.

| Test | Change | Why |
|---|---|---|
| `AdaptiveLearningScenarioTests` fixture | 2 → 3 questions per section | `MIN_EVIDENCE = 3` means a two-question section can never be excluded however well answered. Three is also closer to real coverage. |
| `_force_due()` helpers (2 places) | force `ChunkMastery` as well as `TopicMastery` | Assignment is now gated on the *section's* own schedule. Forcing only the SOP leaves every section scheduled for tomorrow — correct behaviour, just not what those tests are about. |
| `test_strong_section_is_not_selected_for_retraining` | 1 → 3 correct answers | One answer is deliberately no longer sufficient to exclude. A new test asserts that rule directly. |
| `test_three_correct_answers_are_sufficient_to_exclude` | assert *exclusion*, not the label `low` | Three consecutive passes also satisfy the mastery streak, so the section retires as `none`. A separate test exercises the LOW branch with the streak broken. |

## 5. Deliberately not done

| Item | Why not |
|---|---|
| Server-side quiz session | New model + migration + submission-path rewrite, two days before review |
| SOP version lifecycle | Data migration over live rows; highest-risk item in the backlog |
| Question revision history | Migration; not needed to defend the core claim |
| Reviewer rationale field | Migration on the e-signature path — the strongest part of the system; not worth destabilising |
| Single-question regenerate | New endpoint + UI; convenience, not correctness |
| Entailment verification | Research-scale; the SME gate is the actual control |
| Difficulty-aware priority | Coherent change but touches the metric again after it was just changed |
| Celery job-state refactor | Contract change across 3 endpoints |

## 6. Verification performed

- **219 backend tests** — all passing (accounts 16, sops 19, quiz 36, ai_engine 36, attempts 97,
  analytics 10, audit 5)
- **Frontend** — eslint 0 errors (2 pre-existing warnings), production build clean
- **Demo** — full run against **live NVIDIA NIM**: 9 questions, 3 sections, 33% first attempt,
  2 sections targeted, +50 point measured gain, both sections mastered
- **Browser** — learning path verified in both the scheduled and available states; click-through
  to "Continue Assigned Retraining" confirmed
- **Reviewer provenance** — `source_text` matches the stored `SOPChunk` row exactly
