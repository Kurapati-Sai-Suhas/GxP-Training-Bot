# System Architecture
## GxP Training Bot — as implemented at commit `d783815`

This describes the system **as built**, not as intended. Where the implementation diverges from
the project's own documentation, the divergence is stated rather than reconciled.

---

## 1. Logical architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│  Browser — React 18 SPA (single component tree, no router)       │
│  App.jsx (2,305 lines) · services/api.js (fetch wrapper)         │
│  Session: auth token in localStorage                             │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTPS/HTTP · Authorization: Token <key>
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  Django 5 + Django REST Framework                                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Middleware: CORS → Security → Session → Common → CSRF →    │  │
│  │             Auth → Messages → XFrameOptions                │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ 7 apps:                                                     │  │
│  │  accounts   auth, JobRole, LearnerProfile, RBAC classes     │  │
│  │  sops       upload, extraction, 3-tier chunking cascade     │  │
│  │  quiz       Question/Option, e-signature approval workflow  │  │
│  │  attempts   scoring, Elo, FSRS, Topic/Chunk mastery         │  │
│  │  ai_engine  NVIDIA NIM generation + RAG chatbot (no models) │  │
│  │  analytics  aggregate dashboards (no models)                │  │
│  │  audit      append-only AuditLog + CSV export               │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────┬───────────────────────┬──────────────────────┬────────────┘
       │                       │                      │
       ▼                       ▼                      ▼
┌─────────────┐      ┌──────────────────┐   ┌──────────────────────┐
│ PostgreSQL  │      │ Redis            │   │ NVIDIA NIM           │
│ or SQLite   │      │ Celery broker +  │   │ integrate.api.       │
│ (DATABASE_  │      │ result backend   │   │ nvidia.com/v1        │
│  URL-driven)│      └────────┬─────────┘   │ (OpenAI-compatible)  │
└─────────────┘               │             └──────────────────────┘
                              ▼                        ▲
                    ┌──────────────────┐               │
                    │ Celery worker    │───────────────┘
                    │ 3 shared_tasks   │
                    └──────────────────┘
```

**Filesystem** is a fourth stateful dependency: uploaded SOPs live on local disk under
`MEDIA_ROOT` (`backend/media/`), mounted as a Docker volume. There is no object storage.

---

## 2. Application layering

The codebase follows a consistent, if shallow, layering:

| Layer | Location | Responsibility |
|---|---|---|
| Transport | `*/urls.py`, `*/views.py` | routing, permissions, request validation, HTTP shape |
| Serialization | `*/serializers.py` | field projection, upload validation |
| Async orchestration | `*/tasks.py` | Celery task bodies, persistence, audit writes |
| Domain logic | `*/services.py`, `attempts/fsrs.py` | LLM calls, chunking, Elo, FSRS — all pure-ish, no HTTP |
| Persistence | `*/models.py` | schema plus a small amount of behaviour (`apply_answer`, Elo seeding) |

**Genuine strength:** the two algorithm modules (`attempts/fsrs.py`, `attempts/services.py`)
and `ai_engine/services.py`'s pure functions are free of Django/HTTP coupling, which is why
they can be unit-tested without a database (`SimpleTestCase` in `ai_engine/tests.py:118`,
`FSRSAlgorithmTests`).

**Layering violation:** `attempts/views.py::submit` is a 94-line transaction script holding
grading, audit, Elo, whole-SOP mastery, and per-section mastery orchestration inline. It is the
single most complex unit in the codebase and has no corresponding `attempts/services` façade.
See GAP-D2.

---

## 3. AI / LLM architecture

### 3.1 Actual pipeline

```text
  SOPDocument.file (PDF / DOCX / TXT / MD)
        │
        ▼
  extract_text_from_file()                     sops/services.py:21
    ├── .pdf  → PyMuPDF (fitz), emits "[Page N]" markers
    ├── .docx → python-docx, paragraph text only
    └── .txt/.md → direct read (errors="ignore")
        │
        ▼
  chunk_text() — 3-TIER CASCADE                sops/services.py:121
    ├── TIER 1  heading-aware  ── regex on "Section 2:" / "3.1" style headings
    │             (oversized sections are then length-split, tag stays "heading")
    ├── TIER 2  semantic       ── only when NO heading found anywhere:
    │             Max-Min cosine chunking via NVIDIA nv-embedqa-e5-v5
    │             returns None on any failure → falls through
    └── TIER 3  fixed_length   ── last resort (no API key, or embedding call failed)
        │
        ▼
  SOPChunk rows (chunking_strategy recorded per chunk)   sops/tasks.py:21
        │
        ├──────────────────────────────┐
        ▼                              ▼
  QUIZ GENERATION                RAG CHATBOT
  ai_engine/services.py:170      ai_engine/services.py:283
        │                              │
        │                              ▼
        │                        select_relevant_chunks()  — LEXICAL, not vector:
        │                        set-overlap of 4+ letter words, ties keep doc order
        │                        top 6 chunks
        ▼                              ▼
  build_quiz_prompt()            build_sop_chat_prompt()
  "Use only the SOP text below"  "Answer using ONLY the SOP text below…
  JSON-array output contract      say so plainly if not covered"
  per-question confidence 0-1
        │                              │
        └──────────────┬───────────────┘
                       ▼
        OpenAI SDK client → NVIDIA NIM
        base_url = https://integrate.api.nvidia.com/v1
        model    = meta/llama-3.1-8b-instruct
        temperature = 0.2
                       │
        RETRY: up to 3 attempts, linear backoff 0.5s × attempt
                       │
              ┌────────┴────────┐
         success             all 3 fail
              │                   │
              ▼                   ▼
     _strip_markdown_fences   OFFLINE FALLBACK (deterministic contract)
     json.loads               ├── quiz: generate_mock_questions()
     _normalize_drafts()      │     correct answer = verbatim SOP sentence
       ├─ require 4 keys      │     distractors = fixed 5-template pool
       ├─ ≥2 options          │     confidence hardcoded 1.0
       ├─ clamp confidence    └── chat: answer_sop_question_offline()
       └─ raise if 0 usable         quotes best-matching chunk, 400-char excerpt
              │                   │
              └────────┬──────────┘
                       ▼
        DEDUPLICATION  ai_engine/tasks.py:33
        signature = (normalised question_text, normalised correct answer)
        compared against existing Questions for this (sop, job_role)
                       │
                       ▼
        PERSIST  Question(status="draft", generation_source=..., confidence_score=...)
                 + Option rows (exactly one is_correct)
                 + AuditLog "questions_generated"
                       │
                       ▼
        HUMAN GATE — SME/Admin approve with password e-signature
                       │
                       ▼
        Only status="approved" questions reach learners
```

### 3.2 Provider abstraction — the honest description

There is **one** live AI provider, referenced at three call sites, all hardcoded:

| Constant | Value | File |
|---|---|---|
| `NVIDIA_NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | `ai_engine/services.py:9` |
| `NVIDIA_NIM_MODEL` | `meta/llama-3.1-8b-instruct` | `ai_engine/services.py:10` |
| `NVIDIA_NIM_BASE_URL` | (duplicated) | `sops/services.py:14` |
| `NVIDIA_EMBED_MODEL` | `nvidia/nv-embedqa-e5-v5` | `sops/services.py:15` |

A repo-wide search for `anthropic`, `gemini`, `claude`, or `provider` returns **zero matches**.
There is no provider registry, no strategy interface, no configuration key selecting a backend.
The `openai` package appears in `requirements.txt` **only** because NVIDIA NIM exposes an
OpenAI-compatible wire protocol.

**Accurate characterisation:** *provider-portable* (swapping providers means editing two
constants in two files) with **three independent live→offline degradation points**
(quiz generation, SOP chat, semantic chunking) — not *provider-agnostic* in the sense of a
runtime-swappable abstraction layer.

The base URL is duplicated across two modules rather than shared, so a provider change requires
edits in both. See GAP-D3.

### 3.3 Fallback contract

Every AI-dependent feature degrades rather than fails:

| Feature | Live path | Fallback | Fallback is deterministic? |
|---|---|---|---|
| Quiz generation | NVIDIA NIM, 3 retries | `generate_mock_questions` | **Partly** — correct answer is verbatim SOP text and confidence is fixed at 1.0, but `random.sample`/`random.shuffle` run **unseeded** (`ai_engine/services.py:143-145`), so distractor choice and option order vary between runs |
| SOP chat | NVIDIA NIM, 3 retries | `answer_sop_question_offline` | **Yes** — fully deterministic, quotes the top chunk |
| Semantic chunking | nv-embedqa-e5-v5 | `_split_by_length` | **Yes** — pure function of the input |

All three catch bare `except Exception` and **discard the exception object entirely** — no
logging, no metric, no audit entry. When the LLM path fails, the only surviving evidence is the
`generation_source="mock"` column on the resulting rows. See GAP-E9.

---

## 4. Asynchronous architecture

```text
   HTTP request (web worker thread)
        │
        │  .delay(...)                    ┌────────────────────────┐
        ├────────────────────────────────►│ Redis (broker)         │
        │                                 └───────────┬────────────┘
        │                                             ▼
        │  .get(timeout=N)  ◄── BLOCKS ──┐   ┌────────────────────┐
        │                                └───│ Celery worker      │
        │                                    │ runs task, writes  │
        │                                    │ to DB + audit log  │
        ▼                                    └────────────────────┘
   HTTP response
```

**Three Celery tasks exist:**

| Task | Module | Awaited with | Called from |
|---|---|---|---|
| `process_sop_document_task` | `sops/tasks.py:9` | `.get(timeout=60)` | `SOPDocumentViewSet.process` |
| `generate_quiz_task` | `ai_engine/tasks.py:15` | `.get(timeout=120)` | `generate_quiz` view |
| `answer_sop_question_task` | `ai_engine/tasks.py:100` | `.get(timeout=60)` | `sop_chat` view |

### The central architectural caveat

**Every task is dispatched and then immediately awaited synchronously.** The HTTP worker thread
blocks for the full duration of the LLM call — up to 120 seconds. This means the Celery
integration delivers **process isolation** (a crashing or memory-hungry parse does not take
down the web worker) but **not** request-thread liberation, which is the usual reason to adopt a
task queue. The code comments claim the latter benefit
(`ai_engine/views.py:43`: *"so a slow NVIDIA NIM response never ties up this web worker's
thread"*) — that claim is not accurate as implemented.

Consequences: concurrency is bounded by web workers, not queue depth; `.get()` inside a request
is a documented Celery anti-pattern that can deadlock when the worker pool is saturated; and
there is no task-status endpoint, no retry policy, no dead-letter handling, and no way for a
client to poll a long job. See GAP-D1/F4.

**Default is eager mode.** `CELERY_TASK_ALWAYS_EAGER` defaults to `"True"`
(`config/settings.py:112`), so a plain `git clone` + `runserver` executes all tasks inline with
no Redis required. Only `docker-compose.yml` sets it to `"False"`. This is a deliberate and
well-judged developer-experience decision, but it does mean the *default* execution path in
development and in the test suite is **not** the deployed path.

---

## 5. Adaptive learning subsystem

Two independent rating systems operate on every submission:

```text
                    QuizAttempt submitted
                            │
        ┌───────────────────┴──────────────────┐
        ▼                                      ▼
  PASS/FAIL SIGNAL                       ELO UPDATE
  _pass_signal_from_pairs()              attempts/services.py
    ├─ drop questions with                 ├─ apply_elo_update()
    │  confidence < 0.5                    │    symmetric: learner ↑ / question ↓
    │  (unless >half would drop)           │    K_learner=32, K_question=16
    ├─ weight each by Elo → 1.0–2.0        │    called ONCE per answer (whole-SOP)
    └─ weighted % ≥ 80 → pass              └─ apply_elo_update_ability_only()
        │                                       section-level; does NOT move the
        │                                       question rating (prevents double-count)
        ▼
  MasteryState.apply_answer(pass)
    ├─ FSRS review()  → (stability, difficulty)      attempts/fsrs.py
    │    grades used: GOOD (pass) / AGAIN (fail) only
    ├─ streak/box: pass → +1 (3 ⇒ mastered); fail → reset to 0
    └─ next_eligible_at = now + next_review_interval_days(stability)
        │
        ├──► TopicMastery   (learner, sop)         — always
        └──► ChunkMastery   (learner, sop_chunk)   — only for questions with source_chunk
```

FSRS-4.5 with published default weights; per-user parameter optimisation is deliberately not
attempted (documented rationale: insufficient data volume). The `box_index`/`BOX_INTERVAL_DAYS`
Leitner machinery is retained purely as a display and audit signal — it no longer drives
scheduling.

---

## 6. Deployment architecture

### 6.1 `docker-compose.yml` — five services

```text
┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌────────────────┐   ┌──────────┐
│ db       │   │ redis    │   │ backend      │   │ celery-worker  │   │ frontend │
│ postgres │   │ redis:7  │   │ Django       │   │ celery -A      │   │ nginx    │
│ :16-alp  │   │ -alpine  │   │ :8000        │   │ config worker  │   │ :80→8080 │
│ healthck │   │          │   │ runserver    │   │                │   │          │
└────┬─────┘   └────┬─────┘   └──────┬───────┘   └───────┬────────┘   └────┬─────┘
     │              │                │                   │                 │
     └──────────────┴────────────────┴───────────────────┘                 │
        depends_on: db(healthy), redis(started)                            │
     volumes: postgres_data, media_data (shared backend ↔ worker) ─────────┘
                                                        depends_on: backend
```

Startup order is enforced by a `pg_isready` healthcheck on `db`; `redis` is only
`service_started`. `backend` runs `migrate` then serves. Both `backend` and `celery-worker`
share the `media_data` volume — necessary, because the worker reads `sop.file.path` from disk.

### 6.2 Production-readiness assessment

The compose stack is **not** a production deployment, despite the README describing it as "the
actual, tested path to a Postgres+Redis+Celery deployment":

| Issue | Evidence | Impact |
|---|---|---|
| `DEBUG: "True"` in both app services | `docker-compose.yml:24,49` | full tracebacks with settings/env to any client on error |
| `manage.py runserver` as the server | `docker-compose.yml:42`, `backend/Dockerfile:16` | Django's dev server — single-threaded, explicitly unsupported for production; no gunicorn/uvicorn |
| Media served by Django, unauthenticated | `config/urls.py:18` (`if settings.DEBUG`) | every uploaded SOP is world-readable at `/media/sops/...` |
| Hardcoded DB password | `docker-compose.yml:6` (`gxp_pass`) | committed credential |
| Weak `SECRET_KEY` | `.env.example` → `change-me-for-development` | session/token forgery if reused |
| No TLS anywhere | no ingress/cert config | tokens traverse in plaintext |
| No `SECURE_*` settings | grep finds none in `settings.py` | no HSTS, secure cookies, or redirect |
| No healthcheck on `backend` | `docker-compose.yml:19-42` | orchestrator cannot detect app-level failure |
| No resource limits, no restart policy | — | |

See GAP-E1 and the Security section of the SRS.

### 6.3 CI — `.github/workflows/ci.yml`

Two jobs, both on `push` to `main` and on every PR:

- **backend** — Python 3.12, `pip install -r requirements.txt`, then `migrate` + `test`
  against a **real `postgres:16-alpine` service container** with a health check. `DEBUG: "False"`
  and `CELERY_TASK_ALWAYS_EAGER: "True"`. This genuinely avoids the common SQLite-in-CI /
  Postgres-in-prod drift.
- **frontend** — Node 20, `npm install` (not `npm ci`, because `package-lock.json` is gitignored
  — acknowledged in an inline comment), then `npm run build`.

**Not present in CI:** linting (`eslint` is configured in `package.json` but never run), any
frontend test step (no frontend tests exist), coverage measurement or thresholds, security or
dependency scanning, container image build/publish, and any deployment stage.

---

## 7. Data flow — end-to-end

```text
1. Admin uploads SOP           POST /api/sops/documents/        → SOPDocument(uploaded) + audit
2. Admin processes it          POST .../{id}/process/           → Celery → extract → chunk
                                                                 → SOPChunk[] + audit
3. Admin generates questions   POST /api/ai_engine/generate/    → Celery → NIM (or fallback)
                                                                 → dedupe → Question(draft)[] + audit
4. SME reviews + e-signs       PATCH /api/quiz/questions/{}/approve/  {password}
                                                                 → Question(approved) + audit
5. Learner sees due retraining GET /api/attempts/auto-assigned/ → (creates QuizAttempt) + audit
   or starts manually          POST /api/attempts/quiz-attempts/→ QuizAttempt
6. Learner submits             POST .../{id}/submit/            → grade server-side
                                                                 → AttemptAnswer[] + audit
                                                                 → Elo + FSRS
                                                                 → TopicMastery + ChunkMastery
7. QA monitors                 GET /api/attempts/retraining-status/
                               GET /api/attempts/section-mastery/
                               GET /api/analytics/dashboard-summary/
8. Inspector export            GET /api/audit/logs/export/      → CSV
```

Steps 1–4 are Admin/SME-gated. Steps 5–6 are learner-scoped. Step 7 is reviewer-gated except
`dashboard-summary`, which is open to any authenticated user (GAP-E3).

---

## 8. Technology inventory

| Concern | Choice | Pinned at |
|---|---|---|
| Web framework | Django | `>=5.0,<6.0` |
| API | Django REST Framework | `>=3.15,<4.0` |
| CORS | django-cors-headers | `>=4.3,<5.0` |
| DB driver | psycopg (binary) | `>=3.1,<4.0` |
| PDF extraction | PyMuPDF | `>=1.24,<2.0` |
| DOCX extraction | python-docx | `>=1.1,<2.0` |
| Task queue | Celery | `>=5.3,<6.0` |
| Broker/result | Redis | `>=5.0,<6.0` |
| LLM client | openai (pointed at NVIDIA) | `>=1.40,<2.0` |
| Config | python-dotenv | `>=1.0,<2.0` |
| Frontend | React | `^18.3.1` |
| Build | Vite | `^5.4.0` |
| Icons | lucide-react | `^0.468.0` |

All backend pins are compatible-range, not exact; `uv.lock` exists but is gitignored, and
`frontend/package-lock.json` is gitignored too — so **neither** side has a committed lockfile
and no build in this repository is byte-reproducible. See GAP-D4.

**Notably absent:** no linter or formatter in CI, no type checking, no `django-environ`,
no `drf-spectacular`/OpenAPI schema, no structured logging library, no APM/error tracking,
no test-coverage tooling.
