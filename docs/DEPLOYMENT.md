# Deployment Guide
## GxP Training Bot

Two configurations are provided. They are genuinely different execution paths, not the same
stack with a flag flipped.

| | Development | Production-shaped |
|---|---|---|
| File | `docker-compose.yml` | `docker-compose.prod.yml` |
| `DEBUG` | `True` | `False` |
| Server | `manage.py runserver` | gunicorn, 3 workers, `--timeout 180` |
| Secrets | `backend/.env` | required host env vars; **fails to start if missing** |
| Static files | Django | WhiteNoise |
| Cookies | plain | secure, HttpOnly |
| HSTS / nosniff / referrer policy | off | on (HSTS opt-in) |
| Restart policy | none | `unless-stopped` |

---

## Local development (no containers)

```bash
cd backend
uv sync
cp .env.example .env          # then set NVIDIA_API_KEY (optional — offline fallback works without it)
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver
```

```bash
cd frontend
npm install
npm run dev
```

SQLite and eager Celery mean nothing else needs to run. Demo accounts (`seed_demo`, password
`demo12345`): `anjali` (Admin), `vikram` (SME Reviewer), `rohit`/`priya`/`arun`/`sneha`/`karan`
(Learners).

## Development stack in Docker

```bash
docker compose up --build
```

Backend on `:8000`, frontend on `:8080`, with Postgres, Redis and a real Celery worker
(`CELERY_TASK_ALWAYS_EAGER=False`).

---

## Production-shaped stack

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export ALLOWED_HOSTS="training.example.com"
export CORS_ALLOWED_ORIGINS="https://training.example.com"
export CSRF_TRUSTED_ORIGINS="https://training.example.com"
export VITE_API_BASE_URL="https://training.example.com/api"
export NVIDIA_API_KEY="nvapi-..."

docker compose -f docker-compose.prod.yml up --build
```

Compose fails immediately with a named error if any required variable is missing. That is
deliberate: a misconfigured deployment must not start in a weak state.

### Behind a TLS-terminating proxy

```bash
export SECURE_SSL_REDIRECT=True
export USE_X_FORWARDED_PROTO=True     # only enable when a trusted proxy sets X-Forwarded-Proto
export SECURE_HSTS_SECONDS=31536000
export SECURE_HSTS_INCLUDE_SUBDOMAINS=True
export SECURE_HSTS_PRELOAD=True
```

`USE_X_FORWARDED_PROTO` is opt-in on purpose. Trusting that header unconditionally would let a
client claim its request arrived over HTTPS when it did not.

HSTS is opt-in for the same class of reason: enabling it on a hostname still served over plain
HTTP locks browsers out of it for the full `max-age`.

---

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | `dev-only-secret-key` | **Startup fails** if this default is used with `DEBUG=False` |
| `DEBUG` | `True` | |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | **Startup fails** on `*` or empty when `DEBUG=False` |
| `DATABASE_URL` | `sqlite:///db.sqlite3` | `postgres://` and `postgresql://` supported |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated |
| `CSRF_TRUSTED_ORIGINS` | empty | Needed when the frontend is on another origin |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | |
| `CELERY_TASK_ALWAYS_EAGER` | `True` | `False` to use a real worker |
| `NVIDIA_API_KEY` | empty | Absent ⇒ deterministic offline fallback throughout |
| `LOG_LEVEL` | `INFO` | Applies to the `ai_engine` and `sops` loggers |
| `THROTTLE_LOGIN` | `10/min` | Per IP |
| `THROTTLE_ESIGNATURE` | `20/min` | Per user |
| `THROTTLE_AI_GENERATE` | `30/hour` | Per user |
| `THROTTLE_SOP_CHAT` | `60/hour` | Per user |
| `SECURE_SSL_REDIRECT` | `True` when `DEBUG=False` | Set `False` if a proxy already redirects |
| `USE_X_FORWARDED_PROTO` | `False` | Enable only behind a trusted proxy |
| `SECURE_HSTS_SECONDS` | `0` | Opt-in |
| `GUNICORN_WORKERS` | `3` | |

---

## Operational notes

**Uploaded documents** live on a shared `media_data` volume, mounted into both the backend and
the worker (the worker reads `sop.file.path` from disk). They are **not** served over HTTP —
`/media/` is deliberately unrouted, and files are available only through the authenticated
`GET /api/sops/documents/{id}/download/` endpoint. Any move to multiple backend hosts requires
either shared storage or a migration to object storage.

**Gunicorn's timeout is 180s by design.** Quiz generation waits synchronously on a Celery
result for up to 120s; the default 30s worker timeout would kill those requests. Reducing this
value will break generation on large SOPs.

**Health checks** are defined for Postgres (`pg_isready`), Redis (`redis-cli ping`), the
backend (HTTP reachability — a `401` counts as healthy) and the Celery worker
(`celery inspect ping`). Startup order is gated on Postgres and Redis being healthy.

---

## Verification status

Honest reporting of what has and has not been executed:

| Item | Status |
|---|---|
| Backend test suite (176 tests) | ✅ run, all passing |
| `makemigrations --check` | ✅ run, no drift |
| `check --deploy --fail-level WARNING` | ✅ run, zero issues |
| `SECRET_KEY` fail-fast guard | ✅ verified — refuses to boot |
| Local dev settings unaffected | ✅ verified |
| Frontend lint + production build | ✅ run, 0 errors |
| `docker compose up` (dev) | ⚠️ **not executed in this environment** |
| `docker compose -f docker-compose.prod.yml up` | ⚠️ **not executed in this environment** |
| gunicorn under load | ⚠️ not executed |
| CI pipeline end-to-end | ⚠️ not executed (steps verified individually, locally) |

The Docker and CI configurations are written against verified local behaviour but have not been
run as complete stacks here. Treat them as reviewed-but-unexecuted until a container build is
performed.

---

## Not included

TLS certificates, a managed secret store, database backup/restore, log shipping, monitoring and
alerting, and horizontal scaling of the media volume. See [`SECURITY.md`](SECURITY.md) §9.
