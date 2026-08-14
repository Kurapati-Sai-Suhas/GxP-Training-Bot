# Adaptive Learning — Core Feature Boundary

> ## ⚠ STATUS UPDATE — this document is the AUDIT RECORD (pre-sprint)
>
> The findings below were true at audit time and are preserved so the ❌ rows stay meaningful.
> A review-readiness sprint has since closed several of them. Current state:
>
> | Audit finding | Status now |
> |---|---|
> | Priority ignores recent performance | **FIXED** — recency-weighted accuracy, half-life 5 |
> | 1/1 treated like 50/50 | **FIXED** — `MIN_EVIDENCE = 3` evidence gate |
> | Learning path vs auto-assigned disagree | **FIXED** — `is_due` / `available_now` reconciliation |
> | `ChunkMastery.next_eligible_at` never consumed | **FIXED** — section-level due-ness drives assignment |
> | No test that generation sets `source_chunk` | **FIXED** — `GenerationGroundingTests` |
> | Reworded duplicates pass | **FIXED** — lexical near-duplicate detection |
> | Submitted question ids unvalidated | **FIXED** — SOP + approved-status validation |
> | Adaptive selection advisory (no server-side quiz session) | **STILL OPEN** — `FUTURE_SCOPE.md` §1 |
> | No SOP version lifecycle | **STILL OPEN** — `FUTURE_SCOPE.md` §2 |
> | Difficulty does not affect priority | **STILL OPEN** — `FUTURE_SCOPE.md` §5 |
>
> Tests: 176 at audit time → **219 now**.
> See [`ADAPTIVE_IMPLEMENTATION_REPORT.md`](ADAPTIVE_IMPLEMENTATION_REPORT.md) and
> [`ADAPTIVE_ALGORITHM.md`](ADAPTIVE_ALGORITHM.md).

---


Defines which parts of the repository constitute the primary project feature, so a reviewer
can see what is in scope for the central claim and what is supporting infrastructure.

**Audit basis:** source read end-to-end; behaviours verified by execution where stated.
No code was modified in producing this document.

---

## A. Quiz generation

| Concern | Implementation | In scope |
|---|---|---|
| Document ingestion | `SOPDocument` + `SOPDocumentSerializer.validate_file` (extension allow-list, 20 MB) | ✅ |
| Parsing | `sops/services.py::extract_text_from_file` — PyMuPDF (PDF), python-docx (DOCX), direct read (TXT/MD) | ✅ |
| Chunking | `chunk_text()` — 3-tier cascade: heading regex → Max-Min semantic (embeddings) → fixed-length | ✅ |
| Prompt construction | `build_quiz_prompt()` | ✅ |
| LLM generation | `generate_questions_with_nvidia_nim()` — 3 retries, linear backoff | ✅ |
| Structured output | JSON array contract; `_strip_markdown_fences()` | ✅ |
| Validation | `_normalize_drafts()` — 4 required keys, ≥2 options, confidence clamp | ✅ |
| Deduplication | `ai_engine/tasks.py` — normalised (question_text, correct_answer) signature | ✅ **exact-match only** |
| Source provenance | `Question.source_chunk` FK (`SET_NULL`) | ✅ |
| Question storage | `Question` + `Option`, `status="draft"` | ✅ |
| Offline fallback | `generate_mock_questions()` | ✅ |

**Out of scope:** the RAG SOP chatbot (`sop_chat`). It shares the LLM layer but is a separate
learner-support feature and feeds nothing into mastery.

## B. SME review

| Concern | Implementation | In scope |
|---|---|---|
| Review queue | `GET /api/quiz/questions/?status=draft` → Question Review screen | ✅ |
| Source visibility | `source_section`, `source_text`, `chunking_strategy` on the reviewer serializer | ✅ |
| Generation metadata | `confidence_score`, `generation_source`, `elo_rating` badges | ✅ |
| Edit | `PATCH /api/quiz/questions/{id}/` (Admin only) | ✅ |
| Approve / reject | `PATCH .../approve/`, `.../reject/` (`IsReviewerUser`) | ✅ |
| Electronic signature | Password re-verified via `check_password()`; `content_hash`, `approved_by`, `approved_at` | ✅ |
| Publication | `status="approved"` — the only state learners can receive | ✅ |
| Regenerate | ❌ **not implemented** — no per-question regenerate action | ❌ |

## C. Quiz / assessment

| Concern | Implementation | In scope |
|---|---|---|
| Approved-question filtering | `QuestionViewSet.get_queryset()` forces `status="approved"` for non-reviewers | ✅ |
| Quiz construction | **Client-side** — `LearnerQuiz` snapshots approved questions for the chosen SOP | ⚠️ see note |
| Delivery | `LearnerQuestionSerializer` (no `is_correct`, no `explanation`) | ✅ |
| Answer submission | `POST /api/attempts/quiz-attempts/{id}/submit/` | ✅ |
| Server-side scoring | Re-derived from `Option.is_correct` in DB | ✅ |
| Attempt lifecycle | Atomic single-submission claim → `409` on repeat | ✅ |
| Results | `AttemptAnswerSerializer` — correctness, correct option text, explanation | ✅ |

> ⚠️ **There is no server-side "quiz" entity.** A quiz is whatever set of question ids the
> client chooses to submit. The server validates neither that submitted questions belong to the
> attempt's SOP nor that they are approved. This matters for §D — see `ADAPTIVE_E2E_TRACE.md`.

## D. Adaptive learning — **the primary claim**

| Concern | Implementation | In scope |
|---|---|---|
| Performance collection | `AttemptAnswer.is_correct`, one row per question per attempt | ✅ |
| Mastery representation | `MasteryState` (abstract) | ✅ |
| Whole-SOP state | `TopicMastery` (learner × SOP) | ✅ |
| Section state | `ChunkMastery` (learner × SOPChunk) | ✅ **the adaptive unit** |
| Mastery update | `MasteryState.apply_answer()` — streak, box, FSRS | ✅ |
| Pass signal | `_pass_signal_from_pairs()` — confidence-filtered, Elo-weighted, ≥80% | ✅ |
| Priority calculation | `adaptive._classify()` — lifetime accuracy thresholds | ✅ |
| Weak-topic identification | `analyse_sections()` | ✅ |
| Never-assessed handling | `answered == 0` → HIGH, checked first | ✅ |
| Retraining selection | `select_retraining_questions()` + `auto_assigned_retraining` | ⚠️ **gated by FSRS — see below** |
| Explainability | `GET /api/attempts/learning-path/` + My Learning Path UI | ⚠️ **can contradict selection** |
| Learning path | Same endpoint | ⚠️ |

## E. Spaced repetition

| Concern | Implementation | In scope |
|---|---|---|
| FSRS algorithm | `attempts/fsrs.py` — FSRS-4.5, published default weights | ✅ |
| Grades used | `AGAIN` / `GOOD` only (no Hard/Easy signal exists) | ✅ |
| Scheduling | `next_review_interval_days()` → `next_eligible_at` | ✅ |
| Interaction with selection | `auto_assigned_retraining` filters on `TopicMastery.next_eligible_at <= now` | ⚠️ **conflict** |

---

## The distinction that matters

| | **Adaptive learning** | **Spaced repetition** |
|---|---|---|
| Question answered | *Which* content? | *When*? |
| Module | `attempts/adaptive.py` | `attempts/fsrs.py` |
| Signal | Lifetime accuracy per section | Pass/fail grade + elapsed days |
| Output | `high` / `medium` / `low` / `none` + question ids | `next_eligible_at` |
| Grain | Section (`SOPChunk`) | Section **and** whole SOP |

They cannot be merged: FSRS retrievability is `R(0,S) = 1.0` for every stability, so immediately
after an attempt a just-failed and a just-passed section score identically. Retrievability
answers "is it time?"; accuracy answers "is this learner weak?".

### But they are not properly reconciled — verified

`analyse_sections()` ignores `next_eligible_at` entirely. `auto_assigned_retraining()` only
considers SOPs whose **TopicMastery** is due. Consequence, reproduced by execution:

```text
Section CAPA: 0.0% accuracy → priority = HIGH, selected_for_retraining = True
TopicMastery.next_eligible_at = +10 days → is_due = False

My Learning Path  : "Recommended Next: CAPA"   "1 weak section(s): CAPA"
Learner Quiz      : 0 assignments offered
```

The learner is told a section is urgent and given no way to act on it. See
`REVIEWER_GAP_ANALYSIS.md` Q34/Q36 — this is the single most likely live-demo failure.

---

## Explicitly out of scope

RAG SOP chatbot · analytics dashboard · audit trail · RBAC · throttling · deployment config ·
CI. All are supporting infrastructure; none participates in the adaptive loop.
