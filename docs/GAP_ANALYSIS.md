# Implementation Gap Analysis
## GxP Training Bot — commit `d783815`

Every finding below is anchored to a file and line. Findings were derived by reading the source;
the two highest-severity items (GAP-A1, GAP-A4) were additionally verified empirically against a
running Django instance.

**Priority scale:** P0 = fix before any real use · P1 = fix before pilot · P2 = fix before
production · P3 = worthwhile improvement.

---

## Category A — Broken (implemented but malfunctioning)

| ID | Finding | Impact | Priority |
|---|---|---|---|
| **A1** | **Answer key exposed to the client before answering.** `OptionSerializer` includes `is_correct` (`quiz/serializers.py:9`); `QuestionSerializer` nests options (`:13`); `GET /api/quiz/questions/?job_role=…&status=approved` is exactly what the learner quiz loads (`services/api.js:128`, `App.jsx:1300`). `explanation` leaks the same way. | Every assessment is trivially open-book via devtools. Scores, Elo ratings, FSRS schedules, mastery status, and the compliance record built on them are all unreliable. For a regulated training record this invalidates the primary evidence the system exists to produce. | **P0** |
| **A2** | **Completed attempts can be resubmitted indefinitely.** `submit()` never checks `attempt.completed_at` (`attempts/views.py:103-137`); it deletes prior answers and rewrites score/mastery/Elo on each call. | A learner can submit, read the results screen (which shows every correct answer and explanation), then resubmit a perfect score. Each pass appends another `quiz_attempt_submitted` audit row, so the trail shows the manipulation but nothing prevents it. Training records become unfalsifiable. | **P0** |
| **A3** | **Re-processing an SOP destroys all section-level learner history.** `process_sop_document_task` deletes every `SOPChunk` before rebuilding (`sops/tasks.py:20`). `ChunkMastery.sop_chunk` is `CASCADE` (`attempts/models.py:163`) and `Question.source_chunk` is `SET_NULL` (`quiz/models.py:35`). | One click on "process" silently erases every learner's per-section mastery for that SOP and orphans every question from its source text — destroying the grounding provenance that justifies the AI-drafted content. No warning, no audit entry describing the loss. | **P0** |
| **A4** | **The Difficulty selector on Generate Quiz does nothing.** The SPA renders it and sends it (`App.jsx:901-908`, `services/api.js:181`), but `generate_quiz` never reads `request.data["difficulty"]` (`ai_engine/views.py:16-47`) and never forwards it to the task. Grep-verified: the key appears nowhere in `ai_engine/views.py` or `tasks.py`. | Admins believe they are controlling question difficulty; they are not. Difficulty is whatever the LLM self-assigns. Silent no-op controls erode trust in every other control. | **P1** |
| **A5** | **`AttemptAnswer` has no uniqueness constraint**, so one question can be recorded many times in a single attempt (`attempts/models.py:29`). `analytics/tests.py:34-37` relies on this to fabricate fixtures, proving it is reachable. | A crafted submission can repeat a question to inflate its score and skew weak-topic analytics and Elo. | **P1** |

---

## Category B — Incomplete (partially implemented)

| ID | Finding | Impact | Priority |
|---|---|---|---|
| **B1** | **Nothing enforces exactly one correct option per question.** No DB constraint, no serializer validation (`quiz/serializers.py`, `quiz/models.py:71`). The AI path always writes one, but `POST /api/quiz/options/` does not. | A question with zero correct options is unpassable; one with several silently accepts multiple answers as correct. Reaches learners if approved. | **P1** |
| **B2** | **`submit()` does not validate submitted question IDs.** No check that a question belongs to the attempt's SOP, is `approved`, or is not duplicated (`attempts/views.py:115-132`). An unknown `question_id` produces an unhandled `IntegrityError` → HTTP 500. | Answers to arbitrary questions can be injected into an attempt and will feed Elo/mastery. Poor error behaviour on malformed input. | **P1** |
| **B3** | **`SOPChunk.page_number` is never populated by the real pipeline.** Only `seed_demo` sets it (`accounts/management/commands/seed_demo.py:120`); `sops/tasks.py:22` omits it, even though `extract_pdf_text` emits `[Page N]` markers (`sops/services.py:38`). | Page-level citation — the natural audit anchor for "where in the SOP did this come from" — is unavailable for genuinely uploaded documents. | **P2** |
| **B4** | **The offline quiz fallback is not fully deterministic.** `random.sample` and `random.shuffle` run unseeded (`ai_engine/services.py:143-145`). | The correct answer is always verbatim SOP text (the property that matters), but distractor selection and option ordering vary run to run, so "deterministic fallback" is only partly true and the output is not reproducible for validation purposes. | **P2** |
| **B5** | **Retry/fallback is tested only via a missing API key.** Every fallback test forces `NVIDIA_API_KEY=""` (`ai_engine/tests.py:69,176,188`; `sops/tests.py:155`), which short-circuits *before* the retry loop. Grep confirms no test mocks `chat.completions.create` with a `side_effect`. | The 3-attempt backoff loop (`ai_engine/services.py:98-115`) — the code that actually runs during a real outage — has **zero** test coverage. | **P2** |
| **B6** | **No frontend tests at all.** `package.json` declares no test runner; 3,837 lines of frontend code are unverified. `eslint` is configured but never run in CI. | Regressions in the entire user-facing layer are caught only by manual inspection. | **P2** |
| **B7** | **`attempts/apps.py` still imports a now-empty signals module** (`apps.py:9` → `signals.py`, which contains only comments). | Harmless dead wiring that misleads readers into looking for signal receivers that no longer exist. | **P3** |

---

## Category C — Missing (absent capabilities)

| ID | Finding | Impact | Priority |
|---|---|---|---|
| **C1** | **No user lifecycle management.** No signup, password change, password reset, or deactivation endpoint. Users exist only via `seed_demo` or Django admin. | Not operable outside a demo. Also means no way to revoke a departing employee's access via the application. | **P1** |
| **C2** | **No training-assignment model.** Nothing links a `JobRole` to the SOPs it *must* complete. Quizzes are discoverable (any approved question for your role) rather than assigned, and `auto-assigned` only revisits SOPs a learner has already attempted. | A learner who has never touched a required SOP is indistinguishable from one with no obligations. Training *completeness* — the core regulated question, "is this person qualified?" — cannot be answered. | **P1** |
| **C3** | **No training-completion or certification record.** No pass certificate, no completion date per requirement, no "currently qualified" state. `mastery_status` is the nearest analogue and is a scheduling artefact. | The system cannot produce the record an inspector actually asks for. | **P1** |
| **C4** | **No SOP version lifecycle.** `unique_together(sop_code, version)` gives version *identity* only — no supersedes link, no effective date, no current-version flag, no requalification trigger when a new version lands. | The stated driver ("SOP-version changes force requalification") is not implemented. Learners can remain "mastered" on a superseded procedure. | **P1** |
| **C5** | **No question-content versioning.** Editing a question mutates the row in place; the previously-approved wording is gone. | Cannot reconstruct what a learner was actually shown at attempt time — a direct record-integrity failure. Compounds A2/E4. | **P1** |
| **C6** | **No structured logging or observability.** Grep for `LOGGING`/`logger`/`logging` across the backend returns **zero matches**. No metrics, no tracing, no request IDs, no error tracking. | Production failures are invisible. LLM failures in particular vanish silently (see E9). | **P2** |
| **C7** | **No API documentation/schema.** No OpenAPI generator; `docs/API.md` (this analysis) is the first API reference. | Integration and review both require reading source. | **P3** |
| **C8** | **No task-status endpoint or async job polling.** All three tasks are awaited synchronously. | No way to run a genuinely long job; clients cannot poll. | **P2** |

---

## Category D — Technical debt (works, poorly designed)

| ID | Finding | Impact | Priority |
|---|---|---|---|
| **D1** | **`GET /api/attempts/auto-assigned/` mutates state.** It creates `QuizAttempt` rows and writes `quiz_attempt_auto_assigned` + `retraining_escalation` audit entries (`attempts/views.py:253-280`), and the SPA calls it on every page load (`App.jsx:1279`). | Violates HTTP semantics; a browser prefetch or refresh emits compliance-escalation records. Idempotency is preserved by attempt reuse, but escalation logging is a side effect of a read. | **P2** |
| **D2** | **`submit()` is a 94-line transaction script** holding grading, audit, Elo, and two mastery-update paths inline (`attempts/views.py:102-195`), with no service-layer counterpart — unlike every other domain concern in the codebase. | Highest-complexity, highest-consequence code path is the hardest to test and change. | **P2** |
| **D3** | **`NVIDIA_NIM_BASE_URL` is duplicated** in `ai_engine/services.py:9` and `sops/services.py:14`. | A provider or endpoint change silently half-applies. | **P3** |
| **D4** | **No committed lockfile on either side.** `backend/uv.lock` and `frontend/package-lock.json` are both gitignored (`.gitignore:6,11`); CI uses `npm install`, acknowledged in a workflow comment. | No build is reproducible. For regulated software, an unreproducible build undermines any validation exercise. | **P2** |
| **D5** | **Celery is used without gaining its main benefit.** Every task is `.delay(...).get(timeout=…)` — process isolation is real, request-thread liberation is not, despite code comments claiming it (`ai_engine/views.py:43`). | Misleading comments; concurrency bounded by web workers; `.get()` in-request risks pool deadlock. | **P2** |
| **D6** | **Fallback demo data embedded in production components.** `fallbackStats`, `fallbackSops`, `reviewQuestions`, `activity`, `compliance` (`App.jsx:316-369,492-499,982-1006`) render invented numbers whenever the API is unreachable. | A user can be shown fabricated compliance percentages and SOP rows that look real; only a small "Using demo fallback" badge distinguishes them. In a compliance product this is actively hazardous. | **P1** |
| **D7** | **Inconsistent error contract.** Hand-written views return `{"error": …}`; DRF validation returns `{"field": [...]}`. The SPA reads only `.error` (`services/api.js:25`). | Serializer validation messages (e.g. file-type/size rejection) never reach the user; they see a generic failure. | **P2** |
| **D8** | **`config/settings.py` has no environment separation.** One module, `DEBUG` from env, defaults tuned for development (`DEBUG=True`, `SECRET_KEY="dev-only-secret-key"`). | Safe-by-default is inverted: forgetting an env var yields an insecure configuration rather than a refusal to boot. | **P2** |

---

## Category E — Security / compliance risk

| ID | Finding | Impact | Priority |
|---|---|---|---|
| **E1** | **Deployment path runs Django's dev server with `DEBUG=True` and unauthenticated media.** `docker-compose.yml:24,42,49` (`runserver`, `DEBUG: "True"`); `config/urls.py:18` serves `MEDIA_URL` only when `DEBUG` — so in this stack every uploaded SOP is world-readable at `/media/sops/...` with no auth check, and any error returns a full traceback including settings. Plus hardcoded `gxp_pass`, `SECRET_KEY=change-me-for-development`, no TLS, no `SECURE_*` settings. | Confidential pharma procedures publicly downloadable; debug disclosure; forgeable sessions. The README calls this "the actual, tested path to a … deployment". | **P0** |
| **E2** | **No rate limiting anywhere.** Grep for `throttle`/`THROTTLE` returns zero matches. Applies to `login` **and** to the e-signature password check on approve/reject. | Unlimited credential brute force, and unlimited attempts against the one control the 21 CFR Part 11 claim rests on. | **P0** |
| **E3** | **`dashboard-summary` leaks all learners' performance to any authenticated user.** No `@permission_classes` declared (`analytics/views.py:14`), so it inherits `IsAuthenticated`; `learner_progress` returns other learners' names, roles, SOPs, scores, and pass/fail (`analytics/views.py:40-51`). `learner-profiles` similarly exposes every name and email to all roles. | Personal performance data — disciplinary-relevant in a regulated employer — is visible to peers. Likely a data-protection issue. | **P0** |
| **E4** | **Destructive and content-changing operations are not audited.** No `AuditLog` entry for: SOP delete or update (`sops/views.py` has no `perform_destroy`/`perform_update`), question edit or delete, job-role changes, or learner-profile/role assignment. Only 10 action types exist and none cover deletion. | An Admin can delete an SOP — cascading away its questions, attempts, answers, and mastery records — leaving **no trace**. This is the single largest hole in the append-only-trail claim. | **P0** |
| **E5** | **Append-only is enforced only in the Django admin UI.** `audit/admin.py:13-21` blocks add/change/delete for that interface; there is no DB trigger, no ORM guard, no WORM storage, no hash chaining or digital signature. | Any code path or DB user can `UPDATE`/`DELETE` audit rows. The trail is not tamper-*evident*, only tamper-*inconvenient* through one UI. | **P1** |
| **E6** | **Deleting a user anonymises their audit history.** `AuditLog.user` is `on_delete=SET_NULL` (`audit/models.py:23`). | Attribution — the "who" of who-did-what-when — is destroyed by an ordinary user deletion. Directly contrary to the audit trail's purpose. | **P1** |
| **E7** | **Upload validation is extension + size only.** `sops/serializers.py:39-53` checks `os.path.splitext` and byte count; content is never sniffed, no AV scan, and the original filename is preserved into `MEDIA_ROOT`. | A renamed malicious file is accepted and stored; combined with E1's unauthenticated media serving, the app hosts arbitrary attacker-supplied files. | **P1** |
| **E8** | **CSV injection in the audit export.** `audit/views.py:27-36` writes values unescaped; `details` embeds user-supplied chat questions (`ai_engine/tasks.py:116`). | A learner can craft a chat question beginning `=`/`+`/`-`/`@` that executes as a formula when an inspector opens the export in Excel. | **P2** |
| **E9** | **All LLM failures are silently swallowed.** Five bare `except Exception` blocks discard the exception (`ai_engine/services.py:111,179,260,289`; `sops/services.py:100`) with no logging. | An expired API key, a quota exhaustion, or a persistent outage is indistinguishable from normal operation; the system quietly serves mock content. Only the `generation_source` column hints at it. | **P1** |
| **E10** | **Prompt injection is unmitigated.** SOP text is interpolated directly into prompts (`ai_engine/services.py:41,233`). Instructions embedded in an uploaded document are indistinguishable from the system's own. | A malicious or careless SOP could steer question generation or chatbot answers. Mitigated in practice by the SME approval gate for quizzes — but **not** for the chatbot, whose output reaches learners with no human review. | **P2** |
| **E11** | **Tokens never expire and are stored in `localStorage`.** `get_or_create` returns the same key forever (`accounts/views.py:72`); the SPA persists it in `localStorage` (`services/api.js:13`). | A stolen token is valid indefinitely; `localStorage` is XSS-readable. No rotation, no idle timeout, no absolute expiry — the last of which is a standard expectation for regulated systems. | **P1** |
| **E12** | **Logout is global.** `Token.objects.filter(user=…).delete()` (`accounts/views.py:79`) drops every token for the user. | Logging out on one device silently signs the user out everywhere. | **P3** |

---

## Category F — Performance / scalability risk

| ID | Finding | Impact | Priority |
|---|---|---|---|
| **F1** | **N+1 queries and full-table loads in analytics.** `retraining_status` runs a `COUNT` per row (`attempts/views.py:361`); `section_mastery_status` is similar; `dashboard_summary` loads every completed attempt into memory (`analytics/views.py:87`) and `auto_assigned_retraining` queries per due mastery row. | Dashboard latency grows linearly-to-quadratically with attempt volume; memory grows unbounded. | **P2** |
| **F2** | **No indexes on any filtered column.** No `db_index=True` and no `Meta.indexes` anywhere. Unindexed hot columns include `Question.status`, `SOPDocument.status`, `QuizAttempt.completed_at`, `AttemptAnswer.is_correct`, and `MasteryState.next_eligible_at` — the latter being both an ordering key and the auto-assignment filter predicate. | Full scans on the most frequent queries. | **P2** |
| **F3** | **No pagination on any list endpoint.** No `DEFAULT_PAGINATION_CLASS`. `GET /api/audit/logs/` returns the entire, permanently-growing audit trail in one response; `GET /api/quiz/questions/` returns every question with all options. | Guaranteed to degrade and eventually fail as data accumulates. The audit log is the worst case since it only ever grows. | **P1** |
| **F4** | **Synchronous `.get()` on every Celery call** ties a web worker for up to 120 s (`ai_engine/views.py:47`). | Concurrent generations exhaust the worker pool; documented Celery deadlock risk. | **P2** |
| **F5** | **Whole-file in-memory extraction.** `extract_text_from_file` loads and concatenates the full document (`sops/services.py:32-44`); a 20 MB PDF is processed entirely in RAM, and `_embed_sentences` sends every sentence in one embedding call (`sops/services.py:77`). | Memory spikes; possible provider payload limits on large SOPs. | **P3** |

---

## Summary

| Category | Count | P0 | P1 | P2 | P3 |
|---|---:|---:|---:|---:|---:|
| A — Broken | 5 | 3 | 2 | 0 | 0 |
| B — Incomplete | 7 | 0 | 2 | 4 | 1 |
| C — Missing | 8 | 0 | 5 | 2 | 1 |
| D — Technical debt | 8 | 0 | 1 | 5 | 2 |
| E — Security/compliance | 12 | 4 | 5 | 2 | 1 |
| F — Performance | 5 | 0 | 1 | 3 | 1 |
| **Total** | **45** | **7** | **16** | **16** | **6** |

### The seven P0 items

1. **A1** — answer key shipped to the client
2. **A2** — completed attempts resubmittable
3. **A3** — re-processing destroys section mastery and question provenance
4. **E1** — dev server + `DEBUG=True` + unauthenticated media in the deployment path
5. **E2** — no rate limiting on login or e-signature
6. **E3** — cross-learner performance data exposed to all authenticated users
7. **E4** — deletions and content edits are unaudited

**A1, A2, and E4 interact.** Together they mean a learner can read the answers, submit a
perfect score, and — if an Admin later deletes or edits the underlying records — leave a
compliance trail that neither detects nor records any of it. Fixing these three is the
difference between a convincing prototype and a system whose training records can be relied upon.
