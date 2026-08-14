# Adaptive Learning — Architecture

Corrected to match the repository. Boxes name the module that actually does the work.

---

## 1. End-to-end pipeline

```text
                    ┌──────────────────────────┐
                    │      SOP Document        │  SOPDocument
                    │  PDF / DOCX / TXT / MD    │  sops/models.py
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │     Document Parser      │  sops/services.py
                    │ PyMuPDF · python-docx    │  extract_text_from_file()
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │    Chunking Cascade      │  sops/services.py::chunk_text
                    │ 1 heading-aware (regex)  │  strategy recorded per chunk
                    │ 2 semantic (embeddings)  │  nv-embedqa-e5-v5, Max-Min
                    │ 3 fixed-length (fallback)│
                    └────────────┬─────────────┘
                                 ↓
                          ┌─────────────┐
                          │  SOPChunk   │  ◀── the adaptive unit
                          └──────┬──────┘
                                 ↓
                    ┌──────────────────────────┐
                    │   Question Generation    │  ai_engine/services.py
                    │ NVIDIA NIM llama-3.1-8b  │  3 retries, linear backoff
                    │ ↓ on failure             │
                    │ deterministic offline    │  generate_mock_questions()
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │  Validation & Dedup      │  _normalize_drafts()
                    │ JSON schema · fences     │  content-signature dedup
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │  Question (status=draft) │  source_chunk FK ◀ GROUNDING
                    │  + confidence + source   │
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │       SME Review         │  quiz/views.py
                    │ sees source_text, conf., │  IsReviewerUser
                    │ generation_source, Elo   │
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │ Approval + E-Signature   │  password re-verified
                    │ SHA-256 content_hash     │  approved_by / approved_at
                    │ → content becomes        │  403 on edit/delete
                    │   IMMUTABLE              │
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │     Published Quiz       │  status="approved" only,
                    │ LearnerQuestionSerializer│  enforced in get_queryset
                    │ NO is_correct on the wire│
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │     Learner Answers      │  QuizAttempt + AttemptAnswer
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │  Server-side Evaluation  │  attempts/views.py::submit
                    │ Option.is_correct in DB  │  atomic single-submission claim
                    └────────────┬─────────────┘
                                 ↓
              ┌──────────────────┴──────────────────┐
              ↓                                     ↓
   ┌────────────────────┐               ┌────────────────────────┐
   │   TopicMastery     │               │     ChunkMastery       │
   │  (learner × SOP)   │               │ (learner × section)    │
   └─────────┬──────────┘               └───────────┬────────────┘
             │      both inherit MasteryState       │
             └──────────────────┬───────────────────┘
                                ↓
        ┌───────────────────────────────────────────────┐
        │  Two independent algorithms, different jobs   │
        ├───────────────────────┬───────────────────────┤
        │   ADAPTIVE ENGINE     │   SPACED REPETITION   │
        │   adaptive.py         │   fsrs.py (FSRS-4.5)  │
        │   "WHICH content?"    │   "WHEN to review?"   │
        │   recency-weighted    │   stability/difficulty│
        │   accuracy, half-life │                       │
        │   = 5 answers;        │                       │
        │   MIN_EVIDENCE = 3    │                       │
        │   → HIGH/MED/LOW/NONE │   → next_eligible_at  │
        │      (selected_for_   │      (is_due)         │
        │       retraining)     │                       │
        └───────────┬───────────┴───────────┬───────────┘
                    ↓                       ↓
        ┌──────────────────────┐  ┌──────────────────────┐
        │ Weak-topic detection │  │  Review scheduling   │
        │ + question selection │  │  (failed → sooner)   │
        └───────────┬──────────┘  └───────────┬──────────┘
                    └───────────┬─────────────┘
                                ↓
                 available_now = selected AND is_due
                 (learning path labels both states;
                  assignment offers only available_now)
                                ↓
                    ┌──────────────────────────┐
                    │  Targeted Retraining     │  auto_assigned_retraining
                    │  pre-created QuizAttempt │  idempotent
                    │  scoped question_ids     │
                    └────────────┬─────────────┘
                                 ↓
                          Reassessment ──┐
                                 ↑       │
                                 └───────┘  loop closes at "Learner Answers"
```

## 2. The grounding link

The single foreign key that makes targeted retraining possible:

```text
SOPChunk ◀────── Question.source_chunk ◀────── AttemptAnswer.question
   ▲                                                    │
   └──────────── ChunkMastery.sop_chunk ◀───────────────┘
                     (learner × section)
```

An answer is evidence about **a specific passage of the procedure**, not just about the
document. Remove this FK and the system degrades to a conventional quiz with a score.

`Question.source_chunk` is `SET_NULL`: questions survive chunk deletion but lose provenance,
which is why reprocessing is blocked once approved content exists.

## 3. Request / process boundaries

```text
Browser (React SPA)
    │  Authorization: Token <key>
    ↓
Django REST Framework  ── permissions: IsAuthenticated / IsReviewerUser / IsAdminUser
    │                     throttles: login, esignature, ai_generate, sop_chat
    ├──────────────► PostgreSQL / SQLite   (all mastery + audit state)
    ├──────────────► local filesystem      (SOP files, authenticated download only)
    └──► Redis ──► Celery worker ──► NVIDIA NIM
                   3 tasks, each awaited synchronously with .get(timeout=…)
```

**Known architectural caveat:** every Celery task is dispatched *and immediately awaited*
(`.delay(...).get(timeout=...)`). This gives process isolation but **not** request-thread
liberation. Gunicorn's timeout is set to 180s to accommodate it. Job-state tracking is a
deferred item.

## 4. Where each concern lives

| Concern | Module |
|---|---|
| Extraction + chunking cascade | `sops/services.py` |
| SOP processing task | `sops/tasks.py` |
| LLM generation, retry, fallback, error classification | `ai_engine/services.py` |
| Generation task, dedup, persistence, audit | `ai_engine/tasks.py` |
| Review, e-signature, content hash, immutability | `quiz/views.py`, `quiz/models.py` |
| Learner vs reviewer projection | `quiz/serializers.py` |
| Grading, mastery orchestration, retraining | `attempts/views.py` |
| **Adaptive selection policy** | `attempts/adaptive.py` |
| Spaced repetition | `attempts/fsrs.py` |
| Elo | `attempts/services.py` |
| Mastery state | `attempts/models.py` |
| Audit trail | `audit/models.py` |
| RBAC | `accounts/permissions.py` |

## 5. Adaptive state model

```text
                 ┌──────────────┐
                 │ MasteryState │  abstract — no table
                 │  box_index   │
                 │  streak      │
                 │  status      │
                 │  next_eligible_at
                 │  elo_rating  │
                 │  fsrs_stability / fsrs_difficulty
                 │  updated_at  │
                 │  apply_answer(is_correct)
                 └──────┬───────┘
              ┌─────────┴─────────┐
              ↓                   ↓
    ┌──────────────────┐  ┌──────────────────┐
    │  TopicMastery    │  │  ChunkMastery    │
    │ unique(learner,  │  │ unique(learner,  │
    │        sop)      │  │        sop_chunk)│
    └──────────────────┘  └──────────────────┘
```

Both are computed from the same attempt but **neither is derived from the other** — a SOP may
contain unlinked questions, which would otherwise make whole-SOP mastery unreachable.
