# BASELINE_CURRENT — measured 19 August 2026

Every value below was measured by running the command, not read from documentation.

## Git

| | |
|---|---|
| Commit | `06b6e95` — *docs: project review deck, defence notes and video guide* |
| Branch / sync | `main`, 0 ahead / 0 behind origin |
| **Working tree** | ⚠️ **NOT CLEAN** — 5 modified source files, 2 untracked migrations |

**Uncommitted source changes (Batch A+B):**

```
backend/attempts/models.py       +53
backend/attempts/serializers.py  +12
backend/attempts/views.py       +143 / -25
backend/attempts/tests.py       +381
frontend/src/App.jsx             +20 / -9
backend/attempts/migrations/0006_attemptanswer_answered_at_and_more.py   (untracked)
backend/attempts/migrations/0007_backfill_offered_questions.py           (untracked)
```

> ⚠️ **Both migrations are APPLIED to the development database.** The schema is ahead of the
> committed code. See `FINAL_GAP_MATRIX.md` P0-1.

## Verification

| Check | Result |
|---|---|
| Backend tests | **244 passed, 0 failures, 0 errors, 0 skipped** (183 s) |
| Migration drift | No changes detected |
| Deploy check | 0 issues |
| ESLint | 0 errors, 0 warnings |
| Frontend build | Success — 217.89 kB JS / gzip 64.76 kB |
| Documentation | 40 markdown files |

## Database state

| Quantity | Value |
|---|---|
| Learners (non-staff) | 9 |
| SOP documents | 8 |
| Chunks | 20 |
| Questions total / approved | 93 / 63 |
| Completed attempts | 23 |
| **Interactions (`AttemptAnswer`)** | **42** |
| — with `answered_at` | **9** (all created today via the browser, post-Batch B) |
| — with `response_latency_ms` | **0** (frontend telemetry not wired) |
| Correct / incorrect | 9 / 33 |
| Per learner | demo_learner 18 · rohit 15 · Suhas 6 · priya 3 |

## Demo state

`demo_adaptive --stop-after-analysis` has been run. Current state: 9 approved questions, one
completed attempt at 33.33%, two sections HIGH, one LOW and excluded. **Demo-ready.**
