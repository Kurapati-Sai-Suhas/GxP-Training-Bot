# Testing Guide
## GxP Training Bot

**Current state: 219 backend tests, all passing. 0 frontend tests.**

```bash
cd backend
uv run python manage.py test
```

Run one app or one class:

```bash
uv run python manage.py test quiz
uv run python manage.py test quiz.tests.AnswerKeyConfidentialityTests
```

Frontend lint and build:

```bash
cd frontend
npx eslint src
npm run build
```

---

## Test inventory

| App | Tests | Coverage focus |
|---|---:|---|
| `attempts` | 63 | Submission and grading, **completed-attempt immutability**, Elo (5), FSRS pure (7) + integration (3), section mastery (7), auto-assignment, escalation |
| `quiz` | 36 | **Answer-key confidentiality (11)**, **approved-content immutability (6)**, **e-signature binding (8)**, e-signature workflow (6), RBAC, filtering, Elo seeding |
| `ai_engine` | 27 | **Live LLM path via mocked provider (8)**, **error classification (6)**, offline fallback, chunk ranking, chat validation, audit |
| `sops` | 19 | Upload validation, processing, chunking cascade, **file access control (5)**, **mutation audit (4)** |
| `accounts` | 16 | Login, identity, role tiers, write permissions, **throttling (4)**, **role/profile audit (3)** |
| `analytics` | 10 | **Dashboard access control (6)**, weak topics, refresher recommendation |
| `audit` | 5 | Attribution, admin-only access, CSV export, append-only |
| **Total** | **219** | |

Bold entries were added during the hardening sprint.

---

## Principles this suite follows

**Regression tests must fail without the fix.** Both P0 fixes were verified by temporarily
disabling the guard and confirming the tests go red:

- Disabling the resubmission claim → **7 of 10** `CompletedAttemptImmutabilityTests` fail.
- Bypassing the learner serializer → **4 of 11** `AnswerKeyConfidentialityTests` fail.

In both cases the tests that *kept* passing were the "don't break the normal flow" ones,
which is the correct signature: the guard is what broke, not the feature.

**CI never needs a live LLM.** No `NVIDIA_API_KEY` is set anywhere in CI. Two distinct
strategies cover the AI code:

- *Offline path* — force `NVIDIA_API_KEY=""`, which returns before any HTTP client is built.
- *Live path* — mock `ai_engine.services.OpenAI` and assert on parsing, retry counts, and
  fallback. This is what closed the pre-sprint gap where the retry loop, fence-stripping and
  JSON validation had **zero** coverage despite being most of the AI code by line count.

**Throttling is disabled under the test runner.** DRF throttle state is cache-backed and
persists across test methods in one process, so real limits would make unrelated tests fail
depending on execution order. `config/settings.py` sets the rates to `None` when `"test"` is in
`sys.argv`; `accounts.tests.ThrottlingTests` re-enables them deliberately.

> Note for anyone extending those tests: `override_settings(REST_FRAMEWORK=...)` does **not**
> work for throttle rates. `SimpleRateThrottle.THROTTLE_RATES` is bound as a class attribute at
> import time and keeps pointing at the original dict. Patch `SimpleRateThrottle.THROTTLE_RATES`
> directly (see `THROTTLE_TEST_RATES`) and clear the cache in `setUp`/`tearDown`.

**Assert behaviour, not implementation.** Tests carry docstrings explaining the scenario and
why it matters. Several are named for the specific bug they prevent from returning — the two
stale `prefetch_related` regressions, and the "question Elo must move exactly once per answer"
guard that protects against double-counting now that two mastery tracks reference it.

---

## Deliberately not tested

Honest list — these are gaps, not omissions by design:

| Area | Why |
|---|---|
| Frontend (3,800+ lines) | No test runner configured; the largest single coverage gap |
| Celery in non-eager mode | The suite runs `CELERY_TASK_ALWAYS_EAGER=True`; broker-backed execution is untested |
| Real concurrency | The resubmission guard is a compare-and-set by construction, but no load test proves it under genuine parallelism |
| Migration rollback | Forward migrations run in CI; reverse migrations are unverified |
| Performance / load | No benchmarks or budgets exist |
| Live NVIDIA NIM | Deliberately excluded from CI; verified manually |

---

## CI

`.github/workflows/ci.yml` runs on push to `main` and every PR:

**Backend** — install → migrate + full suite against a real `postgres:16-alpine` service
container → `makemigrations --check` (model/migration drift) → `check --deploy --fail-level
WARNING` (passes with zero issues) → `pip-audit`.

**Frontend** — install → `eslint` → production build → `npm audit`.

ESLint had never actually run before this sprint: `package.json` declared the script and the
plugins, but no `eslint.config.js` existed, so every invocation exited with "couldn't find a
config file". A v9 flat config now exists and the frontend lints clean (0 errors, 2
pre-existing `react-hooks/exhaustive-deps` warnings, allowed by `--max-warnings 5`).
