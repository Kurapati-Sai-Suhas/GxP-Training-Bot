# Batch A + B — Implementation Report

**Batch A:** server-side quiz integrity (offered-question binding)
**Batch B:** KT-ready learner interaction instrumentation
**Completed:** 19 August 2026 · **Baseline:** commit `06b6e95`, 221 tests
**Status:** implemented, tested, verified — **not committed, not pushed**

---

## 1. Before / after

### The data contract

| | BEFORE | AFTER |
|---|---|---|
| Who decides the assessment | **The browser.** It filtered approved questions client-side and sent whatever it chose | **The server.** `QuizAttemptQuestion` records the exact set, in order, when the attempt is created |
| Submission validation | Membership only — question must belong to this SOP, role, and be approved | **Exact set equality:** `SET(submitted) == SET(offered)`, equal length, no duplicates |
| Partial submission | **Accepted.** Answer 2 of 6 → score 100% | **Rejected, HTTP 400,** zero writes |
| Substituted question | **Accepted** if approved for the same SOP | **Rejected, HTTP 400** |
| Omitted question | **Accepted** — and inflated the score | **Rejected, HTTP 400** |
| Duplicate ids | Rejected (fixed in a prior batch) | Rejected — retained, now with `duplicate_question_ids` |
| Score denominator | `len(submitted_answers)` | **The offered set** |
| Unanswered question | Could simply be omitted | Must be present with `selected_option: null`, counts as incorrect |
| Attempt reproducibility | **Impossible** — no record of what was asked | Offered set + order + per-answer timestamp |

### The interaction record

| | BEFORE | AFTER |
|---|---|---|
| Fields | `id, attempt, question, selected_option, is_correct` | + `answered_at`, `response_latency_ms` |
| Temporal ordering | `-id` auto-increment **proxy** | Server-set, timezone-aware `answered_at` |
| Response latency | Not captured | Optional, validated (0 ≤ x ≤ 3,600,000 ms), explicitly untrusted |
| Client influence over time | n/a | **None** — a client-supplied `answered_at` is ignored |
| Historical rows | n/a | Left `NULL`. **Not backfilled, not fabricated** |

---

## 2. What changed, and why

### A — Offered-question binding

`QuizAttemptQuestion(attempt, question, position, created_at)` is written at attempt creation on
**both** paths:

- **Self-started quiz** (`perform_create`) — every approved question for the attempt's SOP and job
  role. This is exactly the set the client previously assembled for itself, so behaviour is
  preserved while authority moves to the server.
- **Adaptive assignment** (`auto_assigned_retraining`) — the ids the adaptive engine selected.

`_persist_offered_questions()` is **non-destructive**. The assignment endpoint reuses an existing
incomplete attempt rather than creating one per page load; re-scoping an attempt a learner is
part-way through would silently change the assessment under them. The set recorded at creation
wins, and the ids reported to the client are always the ids the server will accept.

Submission now rejects any mismatch with `400`, reporting `invalid_question_ids` (retained under
its original name), `not_offered_question_ids`, `missing_question_ids`, `offered_count` and
`submitted_count`. All validation runs **before** the atomic claim, so a rejected submission leaves
the attempt open and retakeable.

### B — Interaction instrumentation

`answered_at` is **nullable and set explicitly in code**, not `auto_now_add`. That is the whole
point: an auto-populated non-null column would have stamped all 51 pre-existing rows with the
migration's run time, inventing a response history that never happened. One `timezone.now()`
reading is taken per submission, so answers graded in one request share a coherent recording time
rather than implying a response sequence the server never observed.

`response_latency_ms` is nullable and validated. It is documented in the model as **observational
telemetry, not audit evidence** — it never affects grading, mastery, scheduling or the audit trail.

**Decision — extend `AttemptAnswer` rather than add a separate event table.** `AttemptAnswer`
already carries the learner (via attempt), question, correctness and selected option; concept
arrives via `Question.source_chunk`, SOP via the attempt, difficulty via `Question.elo_rating`. A
parallel immutable event table would duplicate all of that with no current consumer and a two-way
consistency burden. If append-only event sourcing becomes a requirement (tamper-evidence, L7), that
is the point to add it.

---

## 3. Files changed

| File | +/− | Change |
|---|---|---|
| `backend/attempts/models.py` | +53 | `QuizAttemptQuestion`; two fields + `MAX_RESPONSE_LATENCY_MS` on `AttemptAnswer` |
| `backend/attempts/views.py` | +143/−25 | Offered-set persistence (both paths); exact-set contract; latency validation; server timestamps; offered-set scoring |
| `backend/attempts/serializers.py` | +12 | `offered_question_ids` on `QuizAttemptSerializer` |
| `backend/attempts/tests.py` | +381 | 23 new tests; `_offer_exactly` helper; 11 existing call sites narrowed |
| `frontend/src/App.jsx` | +20/−9 | Both quiz-start paths render the server's offered set |

**Migrations:** `0006_attemptanswer_answered_at_and_more` (schema),
`0007_backfill_offered_questions` (data).

The data migration backfills **only incomplete attempts** — 31 rows across 5 of 6 — reconstructing
the set the self-start path would have served, so in-flight quizzes stay submittable. Completed
attempts are untouched: they are finished records, cannot be resubmitted, and inventing an offered
set for them would fabricate evidence about an assessment that already happened. It is reversible.

---

## 4. Tests

**221 → 244 (+23). No test deleted. No assertion weakened.**

### Added

| Group | Tests |
|---|---|
| `OfferedQuestionSetTests` (13) | A1 persistence · A18 adaptive set persisted as served · A18b reuse keeps original set · A2 exact set accepted · A9 null-answer accepted · A10 denominator is offered set · A3 duplicates · A4/A5 foreign · A6/A8 missing · A7 extra · A8b empty · hostile mixed payload · A16 resubmission · A17 learner isolation |
| `InteractionTelemetryTests` (10) | B1/B2 server-set + timezone-aware · B3 client cannot override · B4/B7 latency optional · B5 negative · B6 implausible · B6b non-numeric · B8 no fabricated history · B9/B10 orderable and distinguishable · KT sequence reconstruction |

A11–A15 (zero writes on invalid submission) are asserted by `_assert_nothing_written()` inside
every rejection test: no `AttemptAnswer`, no `TopicMastery`, no `ChunkMastery`, `completed_at`
still null, and question Elo unchanged at 1500.

### Existing tests modified — and why

The exact-set contract legitimately invalidated assumptions in **11 submission call sites** that
submitted a subset of the approved questions. **Their intent was preserved, not weakened.**

`_take_quiz({"CAPA": True})` means *"the learner sits a targeted assessment covering CAPA"* — a
real scenario the adaptive engine produces. Under the new contract such an attempt must have an
offered set equal to that subset. The test helper `_offer_exactly()` narrows the recorded set
accordingly, standing in for the assignment endpoint. **The submission itself still goes through
the API and is still validated** against whatever set is recorded.

`SubmissionValidationTests` was deliberately left **un-narrowed** — it exists to prove that
foreign, unapproved, duplicated and not-offered questions are rejected, so its attempt keeps the
real offered set the server recorded.

One test, `test_distinct_question_ids_are_still_accepted`, now creates its second question *before*
the attempt. A question added after an attempt starts is deliberately not offered by it: the
assessment is fixed when it begins.

---

## 5. Verification

| Check | Result |
|---|---|
| Backend tests | **244 passed, 0 failures, 0 skipped** |
| Migration drift | No changes detected |
| Deploy check | 0 issues |
| Frontend lint | 0 errors, 0 warnings |
| Frontend build | Success |
| **Demo regression** | **Byte-identical** — 6 of 9 selected, GMP excluded, 50% → 100% (+50.0 pp), 87.9% vs 75.0% |

### Adversarial verification (isolated throwaway database)

Zero-write claims measured as a **delta** against a pre-probe snapshot of mastery and Elo state:

```
offered set persisted at creation: 6 rows for 6 approved questions
[ok]  exact offered set (6 of 6)                     HTTP 200
[ok]  PARTIAL: only 2 of 6 -- the inflation vector   HTTP 400  zero-writes=True
[ok]  MISSING one question (5 of 6)                  HTTP 400  zero-writes=True
[ok]  EXTRA foreign question appended                HTTP 400  zero-writes=True
[ok]  SUBSTITUTED: foreign replaces an offered one   HTTP 400  zero-writes=True
[ok]  DUPLICATES: same id repeated                   HTTP 400  zero-writes=True
[ok]  EMPTY submission                               HTTP 400  zero-writes=True
[ok]  HOSTILE MIX: offered + not-offered + repeats   HTTP 400  zero-writes=True
[ok]  SCORING: 2 correct + 4 unanswered of 6 -> 33.33%   (never 50 or 100)
[ok]  answered_at set + timezone-aware on all written rows
[ok]  client-supplied answered_at ignored (stored 2026, forged 1999)
[ok]  latency negative / >1h / non-numeric -> HTTP 400, zero-writes=True
[ok]  KT sequence reconstructable by time, ordered=True
```

### Browser verification

`demo_learner` → Learner Quiz → Start Quiz. The creation response returned
`offered_question_ids: [236…244]` and the UI rendered **"Question 1 of 9"** from that set. No new
console errors; the 401s on mount are the pre-existing token-restore issue.

---

## 6. Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| Offered question set persisted | ✅ | `QuizAttemptQuestion`; test A1 |
| Server validates exact offered set | ✅ | `views.py` submit; tests A2–A8 |
| Duplicate ids rejected | ✅ | A3 + adversarial |
| Foreign ids rejected | ✅ | A4/A5 |
| Missing ids rejected | ✅ | A6/A8 |
| Extra ids rejected | ✅ | A7 |
| Null unanswered questions supported | ✅ | A9 |
| Score uses full offered set | ✅ | A10 — 66.67% and 33.33% cases |
| Invalid submissions perform zero writes | ✅ | `_assert_nothing_written()`; delta-verified |
| Mastery / Elo / FSRS unchanged on invalid submission | ✅ | Same |
| Resubmission rejected | ✅ | A16 — 409 |
| Learner isolation preserved | ✅ | A17 — 404 |
| `answered_at` implemented, server-controlled | ✅ | B1–B3 |
| Latency implemented + validated | ✅ | B4–B7 |
| Historical timestamps not fabricated | ✅ | B8; 0 of 51 backfilled |
| Migration clean | ✅ | No drift |
| Backend tests pass | ✅ | 244 |
| Frontend lint / build pass | ✅ | 0/0; build OK |
| Security regression passes | ✅ | Adversarial harness |
| Adaptive regression passes | ✅ | Demo byte-identical |
| KT readiness report created | ✅ | `KT_DATA_READINESS.md` |
| Batch report created | ✅ | This document |

---

## 7. Deliberately NOT done

| Item | Reason |
|---|---|
| Frontend latency instrumentation | Backend accepts and validates it; wiring a per-question timer means UI changes with no current consumer. Field is ready; population deferred |
| Backfilling `answered_at` on 51 historical rows | Would fabricate a response history. NULL is the honest signal |
| Giving `demo_adaptive` timestamps | It writes rows directly via the ORM. A NULL marks its output as synthetic and never subject to the submission contract — see `KT_DATA_READINESS.md` §3 |
| Adaptive algorithm changes | Out of scope by instruction. Thresholds, recency weighting, `MIN_EVIDENCE`, Elo, FSRS and the mastery state machine are untouched |
| Vector DB, KT model, SOP versioning, concept layer | Later batches |

---

## 8. Remaining risks

| Risk | Assessment |
|---|---|
| **Self-start offers the whole SOP** | The adaptive decision is now *enforced* for assigned attempts, but a learner may still self-start a full-SOP quiz. That is legitimate behaviour, not a bypass — the offered set is recorded either way. Whether self-start should be adaptive is a **product** question, deliberately not changed here |
| **Questions approved mid-attempt are excluded** | By design: the assessment is fixed when it begins. Worth stating in the SRS |
| **`_offer_exactly` in tests** | Test setup, not a production path. `SubmissionValidationTests` deliberately does not use it |
| **Difficulty look-ahead bias** | `Question.elo_rating` is live; an interaction does not record difficulty at answer time. Must be addressed before any KT dataset is built (see `KT_DATA_READINESS.md` §6) |
| **6 incomplete attempts backfilled** | Reconstruction, not a record. Confined to attempts that were never completed |

---

## 9. Gap status

| Baseline gap | Status |
|---|---|
| **L1** — offered set not persisted; adaptive advisory not enforced | **CLOSED** |
| **L12** — no timestamp / latency on interactions | **CLOSED for new data**; historical rows remain NULL by design |
| L2 SOP versioning · L3 difficulty in priority · L4 entailment · L5 KT model · L13 embeddings · L14 lexical retrieval · L15 concept layer | Unchanged — later batches |

---

## 10. Next recommended batch

**Batch C — evaluation harness.** Temporal splitting, AUC / log-loss / Brier / calibration, and
baselines (current rule-based engine, Elo, logistic). Without it, no later claim that a vector
database or a KT model is "better" can be substantiated — which is the standard set in the
programme brief.

It is also the cheapest batch: it adds no production code path.
