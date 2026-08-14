# Changelog

All notable changes to this project. Format loosely follows Keep a Changelog.

---

## [Unreleased] — Security & record-integrity hardening sprint

Baseline for this entry: commit `d783815`, **89 tests passing**.
Result: **176 tests passing**, no regressions, no tests disabled or deleted.

Driven by the findings in [`GAP_ANALYSIS.md`](GAP_ANALYSIS.md). Every P0 finding is addressed;
several P1/P2 items are addressed opportunistically. Deferred work is listed at the end and in
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — it is recorded rather than half-built.

### Fixed — assessment integrity (P0)

- **The answer key is no longer sent to learners.** `GET /api/quiz/questions/` returned
  `is_correct` on every option, plus the `explanation`, to any authenticated user — including
  the learner about to sit the quiz. Split into role-selected serializers: reviewers keep the
  full record, learners receive question text and option text only. `/api/quiz/options/`,
  which exposed the same field, is now reviewer-only. (GAP-A1)
- **Learners can no longer request unapproved content.** "Only approved questions reach
  learners" previously depended on the client passing `?status=approved`; omitting it returned
  drafts. Now enforced server-side in `get_queryset()`. (GAP-A1 adjacent)
- **Completed attempts can no longer be resubmitted.** `submit()` had no `completed_at` guard,
  so a learner could submit, read the results screen (which discloses the correct answers by
  design), and resubmit a perfect score — overwriting the score, the answers, the Elo ratings
  and the mastery schedule. Now an atomic conditional `UPDATE` claims the attempt; a second
  submission gets `409 Conflict` and is itself audited. (GAP-A2)

### Fixed — record integrity (P0)

- **Approved questions are immutable through the API.** `PATCH`, `PUT` and `DELETE` now return
  `403` for approved content. The SPA already hid the Edit button, but the API accepted the
  write. The correction path is reject → edit → re-approve. (GAP-E4)
- **Electronic signatures are bound to the content signed.** New `Question.content_hash`
  (SHA-256 over question text, explanation, difficulty and the full ordered option set
  including the answer key), plus `approved_by` and `approved_at`. `signature_is_intact()`
  detects divergence caused by any route that bypasses the API — ORM, admin site, migration.
  Rejection clears the binding. (GXP-2)
- **Destructive SOP reprocessing is blocked.** Reprocessing deletes and rebuilds every
  `SOPChunk`, which cascades away each learner's `ChunkMastery` and orphans approved questions
  from their source text. `POST /process/` now returns `409` when approved questions exist.
  This is a *mitigation*, not the full fix — see Deferred. (GAP-A3)

### Fixed — access control (P0)

- **Uploaded SOPs are no longer publicly downloadable.** Django's `static()` media route has no
  authentication and activates whenever `DEBUG` is true — which the Docker stack set — so every
  uploaded procedure was fetchable by URL. The route is removed; files are served only through
  `GET /api/sops/documents/{id}/download/` behind normal authentication. (GAP-E1)
- **The dashboard no longer leaks cross-learner data.** `dashboard-summary` declared no
  permission class and returned every learner's name, role, SOP, score and pass/fail status to
  any authenticated caller. `learner_progress` is now scoped to the requesting user unless they
  hold a reviewer role; non-identifying aggregates remain shared. (GAP-E3)
- **Rate limiting added** on login (10/min per IP), e-signature verification (20/min per user),
  quiz generation (30/hour) and SOP chat (60/hour) — all env-tunable. The login throttle counts
  all attempts, so a correct guess after several failures is still blocked. (GAP-E2)

### Fixed — auditability (P0)

Six previously-unaudited mutation types now write `AuditLog` entries: **SOP updated**, **SOP
deleted** (with cascade impact counts captured before deletion), **question edited** (with
changed fields and previous values), **question deleted**, **job role changed**, **learner
profile changed** (with the previous job role). Blocked resubmissions are also recorded.
Approval entries now carry the content hash, signer and timestamp. (GAP-E4)

### Added — production configuration (P1)

- `docker-compose.prod.yml`: `DEBUG=False`, gunicorn, required secrets that fail fast if
  missing, health checks and restart policies on every service.
- `docker-compose.yml` remains the development stack, now with Redis and worker health checks.
- Gunicorn `--timeout 180`, deliberately above the 120s synchronous LLM wait — the default 30s
  would kill generation requests mid-flight.
- WhiteNoise for static assets (gunicorn, unlike `runserver`, does not serve them).
- Startup **refuses to boot** with `DEBUG=False` and the development `SECRET_KEY`, or with a
  wildcard/empty `ALLOWED_HOSTS`.
- Secure cookies, HSTS (opt-in), nosniff, referrer policy, and a proxy TLS header that is only
  trusted when explicitly enabled — all applied only when `DEBUG=False`, so local HTTP
  development is unaffected.
- `manage.py check --deploy --fail-level WARNING` passes with **zero** issues.

### Added — observability (P2)

- Logging configuration (there was none). Console handler, env-tunable level, dedicated
  `ai_engine` and `sops` loggers.
- `classify_llm_error()` buckets provider failures into `rate_limit`,
  `authentication_failure`, `timeout`, `connection_error`, `invalid_model_output`,
  `validation_failure`, `provider_error`, `model_not_found`, `unknown`. Every retry and every
  fallback is now logged with its category. Previously all five `except Exception` blocks
  discarded the exception entirely, making an expired API key indistinguishable from normal
  operation. (GAP-E9)

### Added — tests (89 → 176)

Answer-key confidentiality (11) · completed-attempt immutability (10) · approved-content
immutability (6) · e-signature binding (8) · live LLM path via a mocked provider (8) · LLM
error classification (6) · dashboard access control (6) · SOP file access control (5) · SOP
mutation audit (4) · throttling (4) · role and profile audit (3).

Both P0 fixes were verified by temporarily disabling the guard and confirming the new tests
fail — see [`TESTING.md`](TESTING.md).

### Added — CI

`makemigrations --check` (model/migration drift), `check --deploy --fail-level WARNING`,
`pip-audit`, frontend `eslint`, and `npm audit`.

### Fixed — tooling

ESLint had **never run** in this project: the `lint` script and plugins were present but no
config file existed, so every invocation errored out. Added an ESLint 9 flat config; the
frontend now lints clean (0 errors; 2 pre-existing hook-dependency warnings remain).

### Changed — API contracts

| Change | Consumer impact |
|---|---|
| Learner question payload drops `is_correct`, `explanation` and reviewer metadata | SPA result screen now reads correctness from the submit response |
| `AttemptAnswerSerializer` gains `selected_option_text`, `correct_option_text` | Additive |
| `SOPDocumentSerializer`: `file` is write-only, new `download_url` | SPA fetches files as authenticated blobs |
| `POST .../submit/` may return `409` | New failure mode |
| `POST .../process/` may return `409` | New failure mode |
| `PATCH`/`PUT`/`DELETE` on an approved question returns `403` | New failure mode |
| `GET /api/quiz/options/` requires a reviewer role | Not used by the SPA |
| `/media/...` no longer served | Replaced by the download endpoint |

### Deferred — recorded, not started

Each is a genuine architectural change that a partial implementation would leave worse than the
current coherent state:

- **SOP version lifecycle** — needs a new entity and a data migration over live rows.
  Reprocessing is blocked rather than versioned, so revising a procedure has no workflow yet.
- **Training assignment and completion model** — the system still cannot answer "is this person
  qualified?" This remains the largest missing *product* capability.
- **Celery job-state refactor** — endpoints still block on `.get(timeout=…)`. Slow, not
  incorrect.
- **Question revision history** — corrections replace rather than supersede.
- **Tamper-evident audit storage** — still admin-UI enforcement only; no hash chain or WORM.
- **Token expiry/rotation**, semantic deduplication, request-ID correlation, frontend tests.
