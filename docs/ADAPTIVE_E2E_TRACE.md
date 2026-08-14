# End-to-End Trace — One Learner Through the Full Pipeline

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


Traces every transition, naming the model, API, function, DB record and test at each stage.
The purpose is to expose **disconnected parts**, not to restate the happy path.

---

## Stage table

| # | Stage | Implementation | Model | API / Function | Tested? | Gap |
|---|---|---|---|---|---|---|
| 1 | SOP uploaded | `SOPDocumentSerializer.validate_file` | `SOPDocument` | `POST /api/sops/documents/` | ✅ 3 | — |
| 2 | Text extracted | `extract_text_from_file` | — | PyMuPDF / python-docx | ⚠️ TXT only | **PDF/DOCX extraction has no test** |
| 3 | Chunked | `chunk_text` 3-tier cascade | `SOPChunk` | `process_sop_document_task` | ✅ 5 | — |
| 4 | Questions generated | `generate_questions` | `Question`, `Option` | `POST /api/ai_engine/generate/` | ✅ 13 | — |
| 5 | **Grounding recorded** | `source_chunk` FK set at creation | `Question.source_chunk` | `ai_engine/tasks.py:60` | ⚠️ indirect | **No test asserts `source_chunk` is set** |
| 6 | Deduplicated | normalised signature | — | `_normalize()` | ✅ 1 | exact-match only |
| 7 | SME reviews | reviewer serializer w/ `source_text` | `Question` | `GET /api/quiz/questions/?status=draft` | ✅ 3 | — |
| 8 | Approved + signed | `check_password` + SHA-256 | `content_hash`, `approved_by` | `PATCH .../approve/` | ✅ 14 | — |
| 9 | Published | queryset forces `status="approved"` | — | `QuestionViewSet.get_queryset` | ✅ 3 | — |
| 10 | Quiz assembled | **client-side snapshot** | *(none)* | `App.jsx::handleStart` | ❌ | **No server-side quiz entity** |
| 11 | Delivered | `LearnerQuestionSerializer` | — | `GET /api/quiz/questions/` | ✅ 11 | — |
| 12 | Answers submitted | — | `QuizAttempt` | `POST .../submit/` | ✅ 10 | **submitted ids not validated** |
| 13 | Graded server-side | `Option.is_correct` lookup | `AttemptAnswer` | `_grade_and_record` | ✅ 4 | — |
| 14 | Score persisted | — | `QuizAttempt.score` | same | ✅ 3 | — |
| 15 | Pass signal | `_pass_signal_from_pairs` | — | confidence + Elo weighting | ✅ 2 | — |
| 16 | **TopicMastery updated** | `apply_answer` | `TopicMastery` | `submit()` | ✅ 6 | — |
| 17 | **ChunkMastery updated** | grouped by `source_chunk_id` | `ChunkMastery` | `submit()` | ✅ 7 | — |
| 18 | Elo updated | `apply_elo_update(_ability_only)` | `elo_rating` ×2 | `attempts/services.py` | ✅ 5 | — |
| 19 | FSRS scheduled | `review()` + `next_review_interval_days` | `fsrs_*`, `next_eligible_at` | `attempts/fsrs.py` | ✅ 10 | — |
| 20 | **Adaptive analysis** | accuracy thresholds | — | `adaptive.analyse_sections` | ✅ 16 | ignores `next_eligible_at` |
| 21 | Retraining selected | `select_retraining_questions` | `QuizAttempt` (pre-created) | `GET /api/attempts/auto-assigned/` | ✅ 6 | **gated by TopicMastery due-ness** |
| 22 | Explanation shown | same analysis | — | `GET /api/attempts/learning-path/` | ✅ 9 | **can contradict stage 21** |
| 23 | Learner retrained | client filters by `question_ids` | — | `App.jsx::handleContinueAssigned` | ❌ | **frontend-enforced targeting** |
| 24 | Reassessed | back to stage 12 | — | — | ✅ 2 | — |

---

## The disconnections this trace reveals

### D1 — There is no server-side quiz (stages 10, 23)

`QuizAttempt` records *that* an attempt happened, not *what it consisted of*. The question set
is assembled in the browser:

```js
// App.jsx::handleStart — manual path: EVERY approved question for the SOP
const snapshot = approvedQuestions.filter(q => String(q.sop) === String(sopId));

// App.jsx::handleContinueAssigned — adaptive path: filtered by the server's ids
const snapshot = item.targeted && item.question_ids?.length
    ? bySop.filter(q => item.question_ids.includes(q.id)) : bySop;
```

**Consequences a reviewer will find:**

- **Targeted retraining is enforced only in the client.** The server computes `question_ids`
  and then trusts the browser to honour them. A learner taking the same SOP via "Start Quiz"
  gets the full question set — the adaptive selection is bypassed entirely, without any error.
- **Results are not reproducible.** Nothing records which questions were *offered*. If a
  question is later rejected or edited, there is no way to reconstruct the quiz as sat.
- **Stage 12 accepts arbitrary question ids.** No check that a submitted question belongs to
  the attempt's SOP or is approved. A crafted payload can feed answers for unrelated questions
  straight into `ChunkMastery`.

This is the largest structural gap in the core feature. The adaptive engine is sound; its
**output is advisory** rather than enforced.

### D2 — Adaptive priority and FSRS scheduling are not reconciled (stages 20–22)

Verified by execution:

```text
CAPA: 0.0% accuracy      → priority HIGH, selected_for_retraining True
TopicMastery due in +10d → is_due False

My Learning Path : "Recommended Next: CAPA"  /  "1 weak section(s): CAPA"
auto-assigned    : 0 assignments
```

Two different questions ("which?" and "when?") are answered by two modules that never
reconcile. The learner is shown a recommendation they cannot act on. **A reviewer clicking
from the learning path to the quiz screen in a live demo hits a dead end.**

Note the asymmetry is one-directional: `learning-path` shows all sections regardless of
due-ness; `auto-assigned` requires whole-SOP due-ness *and then* filters per section.

### D3 — Grounding is recorded but never asserted (stage 5)

`source_chunk` is the FK the entire adaptive claim rests on, yet **no test asserts it is
populated by generation**. `SectionMasteryTests` sets `source_chunk` by hand in fixtures. If
`ai_engine/tasks.py:60` stopped passing `source_chunk=chunk`, every adaptive test would still
pass while chunk-level mastery silently stopped working — all questions would fall into the
"unlinked" bucket.

This is the highest-value missing test in the repository.

### D4 — PDF/DOCX extraction is untested (stage 2)

Every ingestion test uses `.txt`. `extract_pdf_text` and `extract_docx_text` are exercised only
by a corrupted-file test that asserts failure. The formats a pharma customer would actually
upload have no positive-path coverage.

---

## What is genuinely well connected

Stages 12→19 are tight and well tested: grading, pass signal, both mastery grains, Elo (with a
regression guard proving a question's rating moves exactly once per answer), and FSRS. The
`AttemptAnswer → Question.source_chunk → ChunkMastery` chain works and is verified end to end
by `SectionMasteryTests` and `AdaptiveLearningScenarioTests`.

The weakness is not the algorithm. It is that **the algorithm's output is not binding**
(D1) and **not reconciled with its own scheduler** (D2).
