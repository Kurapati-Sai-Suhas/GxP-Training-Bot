# 2–3 Day Completion Roadmap — GxP Training Bot (PS053)

Scope decision: in 2–3 days you cannot also do full 21 CFR Part 11 audit trails, Celery/Redis async, embeddings-based chunking, or CI/CD — those stay as "future work" in the SRS (Section 9). This roadmap targets **one thing**: a live, end-to-end demo where a real SOP goes in and a scored, explained quiz comes out, with nothing on screen that is fake data pretending to be real.

Each task has a rough time estimate assuming ~6–7 focus hours/day. Check items off as you go.

---

## Day 1 — Close the core loop (SOP → AI quiz → review) — ✅ DONE

This is the highest-value day: it directly proves the PS053 claim ("generates role-specific quizzes from SOPs and explains wrong answers").

- [x] **1.1 AI generation endpoint** — `POST /api/ai_engine/generate/` (`backend/ai_engine/views.py`, wired in `ai_engine/urls.py` and `config/urls.py`). Body: `{ sop, job_role, count, difficulty }`. Splits `count` across the SOP's chunks, calls `generate_questions()`, and persists `Question` (status="draft") + `Option` rows.
  - Offline mock generator (`generate_mock_questions` in `ai_engine/services.py`) kicks in automatically whenever `OPENAI_API_KEY` is unset or the live OpenAI call throws — verified by curl with no key configured. This is your demo insurance policy.
- [x] **1.2 Generate Quiz page — real wiring** (`frontend/src/App.jsx`) — SOP/job-role dropdowns now come from `getSopDocuments()`/`getJobRoles()`, generation hits the real endpoint, preview renders real questions with a badge when the offline generator was used, and "Send to Review" navigates to Question Review where the drafts are already sitting.
- [x] **1.3 SOP upload — real wiring** (`SopLibrary` in `App.jsx`) — real form (title/sop_code/version/department + file input) posts multipart to `/api/sops/documents/`, then auto-calls `/process/`. Verified via curl with a real `.txt` SOP: upload → extract → chunk → generate all worked.
- [x] **1.4 End-to-end smoke test** — done via curl + live browser: uploaded a real SOP, processed it, generated 5 questions, reviewed and approved one in the UI, confirmed status flips to "Approved" and persists.

**Bugs found and fixed while wiring this up (pre-existing in the scaffold, not introduced today):**
1. `sops/views.py` `process()` reported `chunks: 0` even when chunking succeeded — `sop.chunks.count()` was reading a stale Django prefetch cache from the `SOPDocumentViewSet` queryset (fetched before the new chunks existed). Fixed by counting via `SOPChunk.objects.filter(sop=sop).count()` instead.
2. The mock generator originally always picked the same first sentence because the view called it once per question (`number_of_questions=1`) in a loop, resetting the generator's internal index every time. Fixed by batching: the view now asks each chunk for its whole share of questions in one call, so the generator can vary sentences within a chunk.

**Known limitation:** the Browser-based smoke test could drive everything except the native OS file picker (no file-upload capability in this tool), so the "select a file" click itself wasn't automated — but the exact code path it triggers (multipart upload → `/process/`) was verified directly against the running backend with a real file.

**End of Day 1 checkpoint:** you can demo "upload an SOP, get an AI quiz draft, review it" without touching Django admin. ✅ Confirmed working.

---

## Day 2 — Learner experience, auth, and real analytics — ✅ DONE

- [x] **2.2 Minimal authentication** — implemented as **token auth**, not session auth (simpler for an SPA, avoids CSRF-cookie plumbing): `rest_framework.authtoken` added, `POST /api/accounts/login/`, `/logout/`, `/me/` in `accounts/views.py`. `QuestionViewSet`, `SOPDocumentViewSet`, and `QuizAttemptViewSet` now require `IsAuthenticated` for write actions (approve/reject/create/process/submit) via `get_permissions()`, and stay `AllowAny` for reads. `QuizAttempt.learner` and `SOPDocument.uploaded_by` are now set server-side from `request.user`, not client input. `submit` also checks the attempt belongs to the requesting user (403 otherwise). **Known gap, as planned:** any authenticated user can approve/reject/upload — there's no Admin/SME/Learner role split yet (full RBAC is still Day-3-plus scope).
- [x] **2.1 Learner Quiz — real wiring** — `GET /api/quiz/questions/?job_role=<id>&status=approved` (query filtering added to `QuestionViewSet.get_queryset()`). Learner picks from SOPs that actually have approved questions for their role, "Start Quiz" creates a real `QuizAttempt`, submit posts real answers to `/quiz-attempts/{id}/submit/` and renders the actual score + per-question correctness/explanations from the response.
- [x] **2.3 Wire Analytics & Users/Roles pages to real APIs** — Analytics now reads `summary.attempts_by_role` / `summary.learner_progress` (dropped the "Weak Topics" card since the backend has no real aggregate for it — no fake numbers left standing next to real ones). Users & Roles reads `getJobRoles()` + `getLearnerProfiles()`; extended `LearnerProfileSerializer` to nest username/name/email/job_role_name (it only exposed raw FK ids before).
- [ ] **2.4 Seed data pass** — not done as a separate step; skipped intentionally to avoid wiping the real SOP/question/attempt data created while testing Day 1–2 live. `seed_demo` still exists and works if a clean reset is ever wanted, but re-running it now would erase the SOP-300 dataset and attempts used to verify this work.

**Bugs found and fixed while wiring this up:**
1. **Real bug, found via live UI testing:** `attempts/views.py`'s `submit()` action returned a response where `answers` was always empty and every question showed "Not answered" — even though the score and DB rows were correct. Same root cause as Day 1's chunk-count bug: `QuizAttemptViewSet.queryset` prefetches `answers` in `get_object()`, then `submit()` deletes and recreates the answers, so the serializer at the end was still reading the stale (pre-delete) prefetch cache. Fixed by re-fetching the attempt from `self.get_queryset()` right before serializing the response.
2. **Not a bug — a testing artifact worth recording:** an early automated run showed "0 of 0 correct" for a fully-answered quiz. Root cause was my own test script clicking an answer and Submit in the *same* synchronous JS call, so React's batched state update for the last answer hadn't flushed before `handleSubmit` read `answers`. A real user physically clicking two separate buttons can't trigger this (each click is a separate browser event; React flushes between them). Confirmed by re-running with normal per-click pacing — worked correctly, 100% score with all 4 answers recorded.

**End of Day 2 checkpoint:** a learner can log in (`rohit` / `demo12345`, etc. — see seed data), take a real quiz tied to their role, see their score and per-question explanations, and Analytics/Users & Roles show real backend numbers. ✅ Confirmed working end-to-end in the browser, including logout.

---

## Day 3 — NVIDIA NIM swap, hardening, tests, demo prep — ✅ DONE

- [x] **3.0 (unplanned, done first) Swap OpenAI → NVIDIA NIM** — `ai_engine/services.py`: `generate_questions_with_nvidia_nim()` now calls `https://integrate.api.nvidia.com/v1` (OpenAI-compatible client) with model `meta/llama-3.1-8b-instruct`, keyed off `NVIDIA_API_KEY` in `.env`/`.env.example`. Added markdown-fence stripping and a hard cap (`drafts[:number_of_questions]`) since the live model sometimes over-generates relative to what was asked. Source label renamed `"openai"` → `"nvidia_nim"` end-to-end (backend response, frontend badge copy). Verified live: real key tested directly via curl first (confirms auth + model id), then through the full `/api/ai_engine/generate/` pipeline (single-chunk and multi-chunk, exact count respected), then through the browser UI showing the "generated live by NVIDIA NIM" badge.
- [x] **3.2 Basic backend tests** — used Django's built-in `APITestCase` (no new dependency needed; pytest wasn't installed and DRF's test client covers this fine). 15 tests across `accounts`, `sops`, `ai_engine`, `quiz`, `attempts` — login/logout/me, SOP upload→process, AI generation (forced onto the offline path via `mock.patch.dict` so tests never need a live key or network), approve/reject + auth gating, question filtering, and quiz submission scoring + ownership. Two of these are **regression tests** for the exact bugs found on Day 1 and Day 2 (stale chunk count, empty `answers` on submit) — run `cd backend && uv run python manage.py test`.
- [x] **3.3 Fix rough edges found in Day 1–2 testing** — fixed a React duplicate-key warning in `QuestionReview` (`key={item.question}` → `key={item.id}`; the mock generator can produce identical `question_text` across different questions, which was causing React to warn about non-unique keys).
- [x] **3.4 Demo script + fallback plan** — see [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md): a literal walkthrough (login → upload → generate → review → learner quiz → analytics), a fallback plan for the "NVIDIA NIM is down" and "fresh upload fails" cases, and a table of anticipated judge questions with honest answers.
- [x] **3.5 README + SRS status update** — `README.md` fully rewritten (was still describing the pre-Day-1 state) to list what's actually implemented, the real API surface incl. auth, and known gaps. `docs/SRS_GxP_Training_Bot.docx` bumped to v2.0: Section 8's status table now shows the real implemented/gap split, Section 9's roadmap only lists what's genuinely still open (RBAC, Celery, embeddings chunking, 21 CFR Part 11 audit trail, etc.), all NVIDIA NIM references replace the old OpenAI/gpt-4o-mini ones, and Section 4.2's API table lists every endpoint including auth.

**End of Day 3 checkpoint:** live NVIDIA NIM generation confirmed working end-to-end, 15 passing backend tests (including 2 regression tests), a rehearsable demo script with a fallback plan, and documentation (README + SRS) that matches the code instead of describing an earlier state of it. ✅ Confirmed working.

---

## Day 4 — "Do everything" pass: closing the remaining gaps — ✅ DONE

Everything the Day 1–3 "what's left" list called out as future work, closed for real (not scaffolded) and verified with Docker (which was available in this environment) rather than just written and hoped-for.

- [x] **4.1 Full RBAC** — `accounts/permissions.py`: `IsAdminUser` (staff or `Admin` Django Group) and `IsReviewerUser` (Admin or `SME` Group). Applied across `sops`, `quiz`, `ai_engine`, `attempts`, `accounts` views. Global default permission flipped from `AllowAny` to `IsAuthenticated` (`config/settings.py`). `QuizAttemptViewSet`/`AttemptAnswerViewSet` now scope reads to the requesting learner's own rows unless Admin. New seeded account `vikram` (SME Reviewer, no upload/generate rights, no learner profile) makes the three tiers actually demoable, not just enforced invisibly. Frontend: nav items and action buttons (SOP upload form, Approve/Reject) hide/disable per role (`currentUser.roles.is_admin` / `is_reviewer`), with a redirect-to-dashboard guard if a URL/state somehow points at a page the current role can't see. 9 new RBAC tests, verified live via curl across all three tiers.
- [x] **4.2 Audit trail (21 CFR Part 11 style)** — new `audit` app: append-only `AuditLog` model (immutable in Django admin — add/change/delete permissions all return `False`), a `log_action()` helper called from every write action (SOP upload/process/fail, question generate/approve/reject, quiz submit), and an Admin-only read endpoint at `/api/audit/logs/`.
- [x] **4.3 Duplicate-question detection** — `ai_engine` generation now skips a draft whose `(question_text, correct_answer)` signature already exists for that SOP+role, and reports `skipped_duplicates` in the response (surfaced in the Generate Quiz UI). Comparing on question-text alone was wrong — the mock generator reuses a fixed stem across genuinely different facts — so the signature includes the correct answer too; caught by a test before it shipped.
- [x] **4.4 Weak-topics analytics** — `dashboard-summary` now aggregates per-question correct-rate from `AttemptAnswer` and returns the 5 lowest; the Analytics page's "Weak Topics" card (removed on Day 2 for lack of real data) is back with real numbers.
- [x] **4.5 Heading-aware SOP chunking** — `sops/services.py chunk_text()` now splits on detected section headings ("Section 2: Gowning Sequence", "3.1 Cleaning Verification", ...) instead of blind 1200-character cuts, falling back to length-based splitting when no headings exist, and sub-splitting an overlong section. Verified on the real SOP-300 dataset: 1 blind chunk → 5 real sections, and follow-on generated questions now carry meaningful `source_section` labels instead of "Auto chunk 1".
- [x] **4.6 Celery + Redis** — `config/celery.py` + per-app `tasks.py` (`sops.tasks.process_sop_document_task`, `ai_engine.tasks.generate_quiz_task`). Defaults to `CELERY_TASK_ALWAYS_EAGER=True` (synchronous, no broker needed) so local dev and `manage.py test` are unaffected. Verified for real: ran an actual Redis container + `celery -A config worker`, dispatched both tasks through real HTTP requests, and confirmed in the worker's own log (`Task ... received` / `succeeded`, including a live NVIDIA NIM call happening inside the worker process).
- [x] **4.7 PostgreSQL** — ran a real `postgres:16-alpine` container, pointed `DATABASE_URL` at it, ran migrations, the full test suite (35/35), `seed_demo`, and a JSONB round-trip check on `AuditLog.details` (nested dict/list survived correctly) — not just "should work via `DATABASE_URL`," actually exercised.
- [x] **4.8 Docker** — `backend/Dockerfile`, `frontend/Dockerfile` (multi-stage: Node build → nginx serve), `docker-compose.yml` (db, redis, backend, celery-worker, frontend). Ran `docker compose up --build`, seeded demo data inside the container, logged in, hit the dashboard, generated a quiz through backend → Redis → the containerized Celery worker (confirmed via its logs, including the live NVIDIA NIM call), and confirmed the frontend served real built HTML on port 8080. Torn down after verification (`docker compose down -v`) to hand back a clean environment.
- [x] **4.9 CI** — `.github/workflows/ci.yml`: backend tests against a real Postgres service container, frontend build. YAML syntax validated locally (no git remote in this environment to actually trigger a run, but every command in it is the same one already proven to work locally and via Docker).
- [x] **4.10 Docs** — README rewritten again to describe all of the above (roles, audit trail, Celery/Postgres/Docker instructions, testing), SRS bumped further, this file updated.

**Bugs found and fixed during this pass:**
1. My first duplicate-detection cut compared only `question_text` — the mock generator's question stem is a fixed template regardless of which fact is being tested, so it flagged genuinely different questions as duplicates of each other. Fixed by keying on `(question_text, correct_answer_text)` instead; caught by a test (`test_generate_falls_back_to_mock_and_creates_draft_questions` briefly asserted `3 != 2`) before it shipped.
2. Scoping `QuizAttemptViewSet`'s queryset to the requesting learner changed the non-owner-submit response from 403 to 404 (the object is filtered out of their queryset entirely before the explicit ownership check even runs) — actually a security improvement (doesn't confirm to a stranger that an attempt ID exists), but the existing test needed updating to match, and a new test covers the Admin-can-view-but-not-submit-for-others case explicitly.
3. **Environment quirk, not a code bug, but worth recording:** verifying Celery+Redis was working took a long detour because (a) Celery logs task execution to **stderr**, not stdout, and I kept re-checking the wrong log file, and (b) repeated `manage.py runserver`/`celery worker` restarts left multiple stale background processes (including duplicate-named Celery workers) that `pkill` couldn't reliably kill because at least one was running under a different Python interpreter path than expected. Resolved by identifying and killing the exact PIDs via `Get-CimInstance Win32_Process` + `Stop-Process`, then re-verifying cleanly.

**End of Day 4 checkpoint:** every gap from the Day 1–3 "what's left" list is closed and independently verified (tests + live curl + live Docker), not just written. ✅ Confirmed working.

---

## What's left (genuinely out of scope, not just deferred busywork)

- Electronic signatures (re-authentication at the point of approval) — the audit trail itself is done, e-signature capture is a further step for full 21 CFR Part 11 parity
- Embeddings-based/vector-search chunking (the chunker is heading-aware now, but not semantic/embedding-based)
- Adaptive retraining (auto-assigning a targeted quiz off the weak-topics data, rather than just surfacing it)
- Frontend test suite (backend has 35 tests; frontend has none)
- Committing `frontend/package-lock.json` (currently gitignored) so CI can use `npm ci` instead of `npm install`

These are captured in `docs/SRS_GxP_Training_Bot.docx` Section 9 as the real remaining "future work" list, and `DEMO_SCRIPT.md`'s judge-question table covers how to talk about them live.
