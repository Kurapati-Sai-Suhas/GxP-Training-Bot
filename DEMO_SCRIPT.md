# Demo Script — GxP Training Bot (PS053)

A literal, rehearsable walkthrough. Follow it in order; don't improvise on stage. Total runtime: ~6–8 minutes.

## Before you go on stage

- [ ] Backend running: `cd backend && uv run python manage.py runserver` → `http://localhost:8000`
- [ ] Frontend running: `cd frontend && npm run dev` → `http://localhost:5173`
- [ ] `backend/.env` has `NVIDIA_API_KEY` set (get one free at https://build.nvidia.com/ if it's missing or expired)
- [ ] Have a real SOP file ready to upload live (a `.txt`/`.docx`/`.pdf`, 1–2 paragraphs of realistic SOP text works well — see the sample text at the bottom of this file if you need one)
- [ ] Know your login accounts (all seeded, password `demo12345`): `anjali` (Admin — uploads + generates + approves + reviews everyone's attempts), `vikram` (SME Reviewer — can approve/reject only, cannot upload or generate), `rohit` (Production Operator learner)
- [ ] **Test the NVIDIA NIM call once right before going on stage** — if it's down or your venue's Wi-Fi is bad, the app will silently fall back to the offline mock generator, which is fine, but you should know in advance which one you'll be demoing so you're not surprised live.

## The walkthrough

### 1. Frame the problem (30 sec, no screen)

"Pharma SOPs are dense, static PDFs. Someone has to manually write role-specific training quizzes from them, and when a learner gets a question wrong, they usually just see 'incorrect' with no explanation of *why* — which means the training moment is lost. Our bot automates the quiz-writing and keeps the explanation front and center."

### 2. Log in as the QA/Admin (`anjali` / `demo12345`)

Show the Dashboard: real counts (SOPs processed, questions approved, learner attempts, average score) — say explicitly "this is live data from our Django backend, not a mockup." Point out the **Export Audit Log** button (Admin only) — click it to show a real CSV download of the compliance trail.

### 3. Upload a real SOP (SOP Library)

- Fill Title / SOP Code / Version / Department, pick your prepared file, click **Upload & Process**.
- Point out the success message showing the actual chunk count — this proves PyMuPDF/python-docx extraction ran for real, not a canned demo.

### 4. Generate a quiz (Generate Quiz)

- Select the SOP you just uploaded, pick a job role, click **Generate Quiz**.
- Watch the "Generated Preview" label: if it says **"generated live by NVIDIA NIM"**, say so — "this call just went to NVIDIA's hosted Llama 3.1 model." If it says **"offline mock generator"**, say: "our AI call has an automatic offline fallback, so a bad Wi-Fi connection never kills the demo — this is running our deterministic generator instead, but the pipeline is identical." Either path is a legitimate thing to show off.
- Click **Send to Review**.

### 5. Review and approve (Question Review)

- Show a question card: options, the highlighted correct answer, a **confidence badge** (say: "this is the model's own estimate of how sure it was — low-confidence drafts are the ones we want a human looking at hardest"), and — this is the PS053 core requirement — the **explanation**, which states why the correct answer is compliant and why the others are risky.
- Click **Approve** on one. A password-confirmation modal appears — "**Confirm Electronic Signature**." Say: "this is our 21 CFR Part 11 electronic signature — approving isn't just a click, it's a re-authenticated, audit-logged decision." Enter the password and confirm. Click **Reject** on another the same way, to show the human-in-the-loop QA gate before anything reaches a learner.
- **Optional role-boundary beat:** log out, log in as `vikram` (SME Reviewer). Point out the sidebar — Generate Quiz and SOP upload are gone, but Question Review is still there, and Approve/Reject still work (with the same signature prompt). "This account can review, but can't upload or generate — that's enforced on the backend too, not just hidden in the UI."

### 6. Log out, log in as a learner (`rohit` / `demo12345`)

- Point out the topbar now shows "Rohit Mehta · Production Operator" — pulled from the authenticated session, not hardcoded.

### 7. Take the quiz (Learner Quiz)

- If a **Recommended Refresher** card is showing, point it out first: "this is computed from this learner's own past wrong answers — a real adaptive-retraining signal, not a static suggestion."
- Pick the SOP quiz you just approved questions for, click **Start Quiz**.
- Answer through it — **deliberately get one wrong** so you can show the payoff feature.
- Submit. On the result screen, show: the score, and for the wrong answer specifically, "your answer / correct answer / explanation" — this is the "explains wrong answers" requirement from PS053, working end-to-end from a real backend scoring call.

### 8. Analytics (as either user)

- Show Role-wise Performance and Learner Progress — real aggregates from the attempt you just submitted, updating live.

### 9. Close (30 sec)

"Every screen you just saw is backed by a real Django REST API — SOP upload and extraction, AI generation via NVIDIA NIM with an automatic offline fallback, a human approval workflow gated by real role-based permissions, an append-only compliance audit log, and real scoring. It also runs on Postgres and Celery/Redis via Docker Compose for production-style async processing — all verified, not just written down. What's left for a production rollout is electronic signatures for full 21 CFR Part 11 parity and embeddings-based chunking for very large documents."

## If something breaks live

- **NVIDIA NIM is down / no internet:** Nothing to do — the app already silently falls back to the offline generator. Don't apologize for it; it's a designed feature, not a bug. Just say "and here's our offline fallback kicking in automatically" like it's the demo.
- **A fresh upload fails to extract text:** Fall back to one of the pre-seeded, already-processed SOPs (SOP-300 "Cleanroom Entry and Gowning" or SOP-204 "HPLC Calibration" both already have real generated questions ready to review/approve/take).
- **Login fails:** Confirm you're using the exact seeded usernames (lowercase, no email) and password `demo12345`. If the DB got reset, re-run `uv run python manage.py seed_demo`.
- **Backend/frontend crashed:** Restart both dev servers; the SQLite DB persists between restarts so your uploaded data survives.

## Anticipated judge questions

| Question | Answer |
|---|---|
| "Is this using a real LLM?" | Yes — NVIDIA NIM's hosted `meta/llama-3.1-8b-instruct`, OpenAI-compatible API. There's also a deterministic offline fallback so a demo never depends on network access. |
| "Is this secure enough for real employee data?" | We have token auth, three role tiers (Admin / SME Reviewer / Learner) enforced on both the API and UI, ownership checks on quiz attempts, an append-only audit log of every approve/reject/upload/generate/submit action, and — as of Day 5 — an electronic signature (password re-entry) required at the exact point a question is approved or rejected, the piece that was previously the main gap for full 21 CFR Part 11 parity. |
| "Does it scale to big SOPs?" | The chunker splits on detected section headings rather than blind character cuts, and both SOP processing and AI generation run as Celery tasks — synchronous by default for simple local dev, genuinely async against Redis when deployed (verified via Docker). A literature review of chunking-strategy studies on structured technical documents found semantic/embeddings chunking doesn't reliably beat structure-aware chunking, so the current approach is a considered trade-off, not a shortcut — still a candidate for very large or unstructured documents. |
| "Do you have tests?" | Yes — 45 backend tests (accounts, sops, ai_engine, quiz, attempts, analytics, audit), including RBAC boundary tests per role, electronic-signature boundary tests, a forced offline-fallback path for AI generation so tests never need a live API key, and regression tests for real bugs we found and fixed during development. CI runs the suite against a real Postgres service container. |
| "Does this run in Docker / is it production-deployable?" | Yes — `docker compose up --build` brings up Postgres, Redis, the Django backend, a Celery worker, and the frontend behind nginx. We ran the full stack this way and generated a real quiz through it end to end, including watching the Celery worker's own log show the NVIDIA NIM call happening inside the worker process. |
| "Is any of this grounded in research, or just built from intuition?" | We did a literature pass across LLM-based MCQ/distractor generation, RAG for regulatory domains, adaptive retraining, and chunking-strategy evaluation (15 sources). Two findings directly validated existing design choices: a 2026 systematic review found ungated AI-generated MCQs can have factual-error rates up to 45%, which is the evidence base for our mandatory SME-approval gate; and independent 2026 chunking studies found semantic chunking doesn't reliably beat our heading-aware approach on structured documents like SOPs. |

---

### Sample SOP text (paste into a `.txt` file if you need a backup upload)

```
Standard Operating Procedure: Equipment Cleaning and Sanitization

Section 1: Scope
This SOP applies to all stainless steel product-contact surfaces in the Production area between batch changeovers.

Section 2: Cleaning Sequence
Operators must first remove visible residue with a lint-free wipe, then apply the approved sanitizing agent and allow a minimum contact time of two minutes before rinsing with purified water.

Section 3: Verification
A visual inspection under UV light is required after cleaning. Any residue detected under UV light requires the full cleaning cycle to be repeated before the equipment can be released for the next batch.
```
