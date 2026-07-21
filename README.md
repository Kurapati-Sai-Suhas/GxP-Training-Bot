# GxP Training Bot

GxP Training Bot is a full-stack capstone project (NVIDIA GenAI Bootcamp, PS053 — Pharma & Life Sciences) for SOP-based training. A Training/QA Admin uploads an SOP document, the app extracts and chunks its text along section-heading boundaries, NVIDIA NIM (with a deterministic offline fallback) drafts role-specific multiple-choice questions with compliance explanations and a self-reported confidence score, an SME/QA Reviewer approves or rejects each draft under an electronic signature (password re-entry), and learners take the approved quiz and get immediate score + explanation feedback on any wrong answer, plus a recommended-refresher suggestion targeted at their own weak topics. Every write action is attributed to a role-checked user and recorded in an append-only audit trail, exportable as CSV.

See [`docs/SRS_GxP_Training_Bot.docx`](docs/SRS_GxP_Training_Bot.docx) for the full requirements spec, [`ROADMAP.md`](ROADMAP.md) for the day-by-day build log, and [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) for a rehearsable walkthrough.

## Tech Stack

- Backend: Django, Django REST Framework, token authentication, role-based permissions (Django Groups)
- Frontend: React, Vite
- Database: SQLite for local dev; PostgreSQL verified via Docker (`docker-compose.yml`)
- Async processing: Celery + Redis, verified via Docker; defaults to synchronous/eager execution when no broker is configured, so local dev needs nothing extra
- Document processing: PyMuPDF, python-docx; heading-aware chunker (splits on detected section headings, falls back to length-based splitting)
- AI layer: NVIDIA NIM (OpenAI-compatible API, `meta/llama-3.1-8b-instruct`), with retry-with-backoff on transient failures, a deterministic offline fallback generator, duplicate-question detection, and a per-question confidence score
- Compliance: append-only `AuditLog` (21 CFR Part 11 style, CSV-exportable) for uploads, processing, generation, approve/reject, and quiz submission; approve/reject additionally requires an electronic signature (password re-entry)
- CI: GitHub Actions (`.github/workflows/ci.yml`) — backend tests against real Postgres, frontend build
- Containerization: Docker Compose (backend, celery-worker, frontend/nginx, postgres, redis) — see `docker-compose.yml`

## Project Structure

```text
gxp-training-bot/
  backend/
    accounts/      # Job roles, learner profiles, login/logout/me, RBAC permission classes
    sops/          # SOP upload, text extraction, heading-aware chunking, Celery task
    quiz/          # Questions, options, approval workflow
    attempts/      # Quiz attempts, answers, scoring
    ai_engine/     # AI prompt + NVIDIA NIM / offline mock generation, Celery task
    analytics/     # Dashboard summary API incl. weak-topics aggregate
    audit/         # Append-only compliance audit log (model, admin, read API)
    config/        # Django settings, URLs, Celery app config
    Dockerfile
  frontend/
    src/
      services/    # API helper functions (incl. auth token handling)
      styles/      # App styling
    Dockerfile
    nginx.conf
  docker-compose.yml
  docs/            # SRS
  ROADMAP.md       # Day-by-day build plan and status
  DEMO_SCRIPT.md   # Live-demo walkthrough + fallback plan
  .github/workflows/ci.yml
```

## Backend Setup (local dev, SQLite, synchronous tasks — no Docker needed)

From the `backend` folder:

```bash
uv sync
copy .env.example .env
# then set NVIDIA_API_KEY in .env — get a free key at https://build.nvidia.com/
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver
```

Backend URL:

```text
http://localhost:8000
```

Main API groups:

```text
/api/accounts/login/                          POST  (username, password) -> token + user (incl. roles)
/api/accounts/logout/                         POST  (auth required)
/api/accounts/me/                             GET   (auth required)
/api/accounts/job-roles/                      writes: Admin only
/api/accounts/learner-profiles/                writes: Admin only
/api/sops/documents/                          writes: Admin only; file validated server-side (type allowlist + 20MB cap)
/api/sops/documents/{id}/process/             POST  Admin only
/api/ai_engine/generate/                      POST  Admin only  {sop, job_role, count, difficulty}
/api/ai_engine/sop-chat/                      POST  (auth required) {sop, question} -> RAG answer grounded in that SOP's chunks
/api/quiz/questions/                          supports ?sop=&job_role=&status= filters; includes confidence_score
/api/quiz/questions/{id}/approve/             PATCH Admin or SME Reviewer, requires {password} (electronic signature)
/api/quiz/questions/{id}/reject/              PATCH Admin or SME Reviewer, requires {password} (electronic signature)
/api/attempts/quiz-attempts/                  POST  (auth required) {sop, job_role}; list scoped to own attempts unless Admin
/api/attempts/quiz-attempts/{id}/submit/      POST  (auth required, owner only)
/api/attempts/auto-assigned/                  GET   (auth required) per-learner adaptive-retraining schedule (due topics)
/api/analytics/dashboard-summary/             GET   (auth required) incl. weak_topics
/api/analytics/recommended-refresher/         GET   (auth required) per-learner "most personally missed SOP" suggestion
/api/audit/logs/                              GET   Admin only — the compliance audit trail (also viewable at /admin/)
/api/audit/logs/export/                       GET   Admin only — CSV export of the audit trail
```

Reads generally require only authentication; the actions above marked "Admin only" / "Admin or SME Reviewer" additionally require the matching Django Group (or `is_staff`) — see **Roles** below. Attempt/answer *reads* are also row-scoped: a plain learner only ever sees their own attempts, Admins see everyone's.

Approving or rejecting a question is a 21 CFR Part 11-style electronic signature: the reviewer must re-submit their own password in the request body (`{"password": "..."}`), verified server-side via `check_password()`, not just rely on an already-authenticated session. A missing or wrong password returns `400` and the question's status is left unchanged; a successful signature is recorded in the audit log entry (`details.e_signature = true`). The frontend prompts for this via a confirmation modal on Question Review.

**Adaptive retraining** — `attempts/models.py TopicMastery` tracks one row per (learner, SOP): a Leitner-style expanding-interval scheduler (correct answer → longer interval before re-test; wrong answer → resets to a 1-day interval) plus a streak-based mastery threshold (3 correct in a row with no intervening miss → `mastered`, at which point it stops being surfaced). It's kept up to date by a `post_save` signal on `AttemptAnswer` (`attempts/signals.py`) — no existing view or serializer was modified to add this. `GET /api/attempts/auto-assigned/` reads that state live and returns every SOP currently due for the requesting learner, each with a difficulty-matched suggestion drawn from the existing `Question.difficulty` field. This is a **soft assignment**: the endpoint never creates a `QuizAttempt` itself — the Learner Quiz screen surfaces due topics prominently, and the learner still starts the quiz through the exact same `POST /api/attempts/quiz-attempts/` flow used for any other quiz. The scheduling design was a deliberate choice among several knowledge-tracing/spaced-repetition approaches from the literature (see `ROADMAP.md` Day 6) — Bayesian/deep knowledge tracing and trained spaced-repetition models (BKT, DKT, Half-Life Regression) were explicitly rejected as needing far more response data per skill than this deployment will ever have.

### Roles

Three tiers, checked via `accounts/permissions.py`:

- **Admin** (`is_staff=True` or in the `Admin` Django Group) — uploads/processes SOPs, triggers AI generation, manages job roles/learner profiles, can also review, sees all quiz attempts, and can read the audit log.
- **SME Reviewer** (in the `SME` Django Group) — approves/rejects generated questions only.
- **Learner** (everyone else, normally with a `LearnerProfile`) — takes quizzes, sees only their own attempts.

Demo accounts created by `seed_demo` (password `demo12345` for all): `rohit`, `priya`, `arun`, `sneha`, `karan` (learners, one per job role), `anjali` (Admin, `is_staff=True`), `vikram` (SME Reviewer, no upload/generate rights).

## Frontend Setup

From the `frontend` folder:

```bash
npm install
npm run dev
```

On Windows, you can also run `.\run_frontend.ps1` (frontend) and `.\run_backend.ps1` (backend).

Frontend URL: `http://localhost:5173`. The sidebar hides Generate Quiz / Question Review for accounts that don't hold the matching role, and the SOP upload form / approve-reject buttons are hidden or disabled the same way.

## Async processing (Celery + Redis) and PostgreSQL — optional, Docker-verified

Local dev defaults to `CELERY_TASK_ALWAYS_EAGER=True` (synchronous, in-process) and SQLite, so nothing extra is required to run the app. To actually run SOP processing and AI generation on a background worker against Postgres, either:

- **Full stack via Docker Compose** (recommended): `docker compose up --build` from the repo root brings up Postgres, Redis, the Django backend (migrates on start), a Celery worker, and the frontend behind nginx on `http://localhost:8080`. The backend is also published on `http://localhost:8000`.
- **Manually**: run a Redis container, set `CELERY_TASK_ALWAYS_EAGER=False`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` in the backend's environment, then run `celery -A config worker --loglevel=info` alongside `manage.py runserver`.

## Testing

```bash
cd backend
uv run python manage.py test
```

59 tests across `accounts`, `sops`, `ai_engine`, `quiz`, `attempts`, `analytics`, and `audit` — including RBAC boundary tests per role, a forced-offline-fallback path for AI generation and the SOP chatbot (no live API key needed to run tests), electronic-signature boundary tests (missing/wrong password on approve/reject), adaptive-retraining scheduling tests, and regression tests for real bugs found during development (a stale Django prefetch-cache issue that showed up twice: once in SOP chunk counting, once in quiz-attempt submission). CI (`.github/workflows/ci.yml`) runs this suite against a real Postgres service container.

## Current Scope

- SOP upload (real form, multipart) → text extraction → heading-aware chunking, fully wired end to end
- Server-side SOP upload validation: file-extension allowlist (`.pdf/.docx/.txt/.md`) and a 20MB size cap, enforced in the serializer, not just the frontend's `<input accept=...>`
- AI quiz generation via NVIDIA NIM per SOP chunk, with a retry-with-backoff on transient failures, an offline fallback generator, and duplicate-question detection, wired into the Generate Quiz screen with a live/offline badge
- Each AI-drafted question carries a self-reported `confidence_score` (0.0–1.0), surfaced as a badge in Question Review so reviewer attention concentrates on the drafts the model was least sure about
- Question approval/rejection workflow, backend + UI, gated to Admin/SME Reviewer, and now requires an electronic signature (password re-entry) at the point of approval/rejection — the audit log records `e_signature: true` on each such entry
- **RAG-based SOP chatbot** — any authenticated user can ask a free-text question about a processed SOP on the SOP Library screen; the answer is grounded exclusively in that SOP's own chunks (word-overlap relevance selection, not embeddings), with the same NVIDIA NIM / offline-fallback pattern as quiz generation, and every query is written to the audit trail
- Token-based auth with three role tiers (Admin / SME Reviewer / Learner), enforced on both the API and the UI
- Append-only audit trail for every write action, an Admin-only CSV export of the trail (`/api/audit/logs/export/`, also a button on the Dashboard), and read access via Django admin
- Learner Quiz: real approved-question fetch (scoped to the learner's role), real `QuizAttempt` creation, real scoring + per-question explanations on submit, a "Recommended Refresher" card (the SOP the learner has personally missed the most, ever), and an **adaptive-retraining "Adaptive Retraining Due" card** driven by a literature-grounded Leitner-style spaced-repetition scheduler (`TopicMastery` model) — both are soft assignments the learner still starts themselves through the normal quiz flow
- Analytics (incl. weak topics by correct-rate) and Users & Roles pages bound to real backend data
- Celery + Redis for async SOP processing / AI generation, and PostgreSQL support, both verified via Docker
- Dockerfiles + `docker-compose.yml` for the full stack; GitHub Actions CI running tests + build

## Known Gaps / Deliberately Out of Scope

See `ROADMAP.md` and Section 9 of the SRS for the full list. What's genuinely still open:

- Embeddings-based/vector-search chunking (the current chunker is heading-aware but not semantic) — two independent 2026 chunking-strategy studies on structured technical documents found semantic chunking didn't reliably outperform structure-aware chunking, so this is treated as a worthwhile experiment rather than an urgent fix (see `ROADMAP.md` Day 5 literature notes)
- Hard auto-assignment (the system pre-creating a live `QuizAttempt` before the learner acts) — deliberately not built; soft assignment (surface it, learner still starts it) was the chosen scope, see `ROADMAP.md` Day 6
- Multi-turn SOP chat (today's chatbot is single-turn: one question, one grounded answer, no conversation memory)
- Frontend test suite (backend has 59 tests; frontend has none yet)
- `frontend/package-lock.json` is gitignored, so CI uses `npm install` instead of `npm ci` — committing the lockfile would make builds reproducible
