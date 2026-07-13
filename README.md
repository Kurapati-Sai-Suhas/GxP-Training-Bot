# GxP Training Bot

GxP Training Bot is a full-stack capstone project (NVIDIA GenAI Bootcamp, PS053 — Pharma & Life Sciences) for SOP-based training. A Training/QA Admin uploads an SOP document, the app extracts and chunks its text along section-heading boundaries, NVIDIA NIM (with a deterministic offline fallback) drafts role-specific multiple-choice questions with compliance explanations, an SME/QA Reviewer approves or rejects each draft, and learners take the approved quiz and get immediate score + explanation feedback on any wrong answer. Every write action is attributed to a role-checked user and recorded in an append-only audit trail.

See [`docs/SRS_GxP_Training_Bot.docx`](docs/SRS_GxP_Training_Bot.docx) for the full requirements spec, [`ROADMAP.md`](ROADMAP.md) for the day-by-day build log, and [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) for a rehearsable walkthrough.

## Tech Stack

- Backend: Django, Django REST Framework, token authentication, role-based permissions (Django Groups)
- Frontend: React, Vite
- Database: SQLite for local dev; PostgreSQL verified via Docker (`docker-compose.yml`)
- Async processing: Celery + Redis, verified via Docker; defaults to synchronous/eager execution when no broker is configured, so local dev needs nothing extra
- Document processing: PyMuPDF, python-docx; heading-aware chunker (splits on detected section headings, falls back to length-based splitting)
- AI layer: NVIDIA NIM (OpenAI-compatible API, `meta/llama-3.1-8b-instruct`), with a deterministic offline fallback generator and duplicate-question detection
- Compliance: append-only `AuditLog` (21 CFR Part 11 style) for uploads, processing, generation, approve/reject, and quiz submission
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
/api/sops/documents/                          writes: Admin only
/api/sops/documents/{id}/process/             POST  Admin only
/api/ai_engine/generate/                      POST  Admin only  {sop, job_role, count, difficulty}
/api/quiz/questions/                          supports ?sop=&job_role=&status= filters
/api/quiz/questions/{id}/approve/             PATCH Admin or SME Reviewer
/api/quiz/questions/{id}/reject/              PATCH Admin or SME Reviewer
/api/attempts/quiz-attempts/                  POST  (auth required) {sop, job_role}; list scoped to own attempts unless Admin
/api/attempts/quiz-attempts/{id}/submit/      POST  (auth required, owner only)
/api/analytics/dashboard-summary/             GET   (auth required) incl. weak_topics
/api/audit/logs/                              GET   Admin only — the compliance audit trail (also viewable at /admin/)
```

Reads generally require only authentication; the actions above marked "Admin only" / "Admin or SME Reviewer" additionally require the matching Django Group (or `is_staff`) — see **Roles** below. Attempt/answer *reads* are also row-scoped: a plain learner only ever sees their own attempts, Admins see everyone's.

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

35 tests across `accounts`, `sops`, `ai_engine`, `quiz`, `attempts`, `analytics`, and `audit` — including RBAC boundary tests per role, a forced-offline-fallback path for AI generation (no live API key needed to run tests), and two regression tests for real bugs found during development (a stale Django prefetch-cache issue that showed up twice: once in SOP chunk counting, once in quiz-attempt submission). CI (`.github/workflows/ci.yml`) runs this suite against a real Postgres service container.

## Current Scope

- SOP upload (real form, multipart) → text extraction → heading-aware chunking, fully wired end to end
- AI quiz generation via NVIDIA NIM per SOP chunk, with an offline fallback generator and duplicate-question detection, wired into the Generate Quiz screen with a live/offline badge
- Question approval/rejection workflow, backend + UI, gated to Admin/SME Reviewer
- Token-based auth with three role tiers (Admin / SME Reviewer / Learner), enforced on both the API and the UI
- Append-only audit trail for every write action (also visible via Django admin, read-only)
- Learner Quiz: real approved-question fetch (scoped to the learner's role), real `QuizAttempt` creation, real scoring + per-question explanations on submit
- Analytics (incl. weak topics by correct-rate) and Users & Roles pages bound to real backend data
- Celery + Redis for async SOP processing / AI generation, and PostgreSQL support, both verified via Docker
- Dockerfiles + `docker-compose.yml` for the full stack; GitHub Actions CI running tests + build

## Known Gaps / Deliberately Out of Scope

See `ROADMAP.md` and Section 9 of the SRS for the full list. What's genuinely still open:

- Electronic signatures (re-authentication on approval) for full 21 CFR Part 11 parity — the audit trail itself is implemented, e-signature capture is not
- Embeddings-based/vector-search chunking (the current chunker is heading-aware but not semantic)
- Adaptive retraining (auto-assigning a targeted quiz based on weak-topic data)
- Frontend test suite (backend has 35 tests; frontend has none yet)
- `frontend/package-lock.json` is gitignored, so CI uses `npm install` instead of `npm ci` — committing the lockfile would make builds reproducible
