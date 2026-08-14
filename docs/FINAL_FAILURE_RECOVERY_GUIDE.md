# FINAL FAILURE RECOVERY GUIDE

Read this **before** the demo, not during it.

**The rule for every entry below: name it, explain the design, move on.** Never troubleshoot
live. A calm "that's the fallback working as designed" beats a silent five minutes of typing.

---

## 0. THE ONE THAT WILL ACTUALLY BITE YOU — sidebar positions

**SYMPTOM** — You reach for "the 5th icon" and land on the wrong screen.

**LIKELY CAUSE** — The sidebar is **role-gated** (`App.jsx:68-75`). `Generate Quiz` requires
admin; `Question Review` requires reviewer. So the icon order is different for `anjali` (admin,
8 items), `demo_sme` (reviewer, no Generate Quiz) and `demo_learner` (neither).

**EXACT FIX** — Navigate by **label**, never by position. Hover to reveal the label.

**WHAT TO SAY** — Nothing. This is invisible if you navigate by name.

---

## 1. Demo data is in the wrong state (sections already mastered)

**SYMPTOM** — My Learning Path shows everything green / "No sections currently need retraining."
Nothing is flagged HIGH. The centrepiece falls flat.

**LIKELY CAUSE** — A previous **full** `demo_adaptive` run left the database in the
*post*-retraining state.

**EXACT FIX**
```bash
cd backend && uv run python manage.py demo_adaptive --stop-after-analysis
```
Takes ~30s. Then refresh the browser.

**WHAT TO SAY** — "Let me reset to the starting state — this command rebuilds the whole scenario
in about thirty seconds."

> ✅ As of the final verification the database **is** in the correct pre-retraining state.
> Re-run the command anyway if you have run anything since.

---

## 2. NVIDIA NIM unreachable or very slow

**SYMPTOM** — Generation hangs, then questions appear tagged `mock`.

**LIKELY CAUSE** — Provider unreachable, rate-limited, or the API key is invalid. Three retries
with backoff run first.

**EXACT FIX** — None. Let it fall back. Do not edit `.env` live.

**WHAT TO SAY** — "The provider is unreachable, so it's fallen back to the deterministic offline
generator. That's designed behaviour — three retries with backoff, then a fallback whose correct
answer is taken verbatim from the SOP text, so it cannot hallucinate. Lower quality, not lower
integrity. The questions are tagged `generation_source='mock'` so a reviewer can see which path
produced them. `classify_llm_error` buckets the failure into one of nine categories in the logs."

---

## 3. Question generation fails outright

**SYMPTOM** — Error banner on Generate Quiz; no drafts appear.

**EXACT FIX** — Switch to **Question Review**; the 9 questions from the reset are already there.

**WHAT TO SAY** — "The nine approved questions are already in the review queue — those were
generated live during the reset. Let me show you those."

---

## 4. "Nothing is available right now" when you wanted retraining

**SYMPTOM** — Sections show **HIGH** priority but the badge reads *"Scheduled for <date>"* and
the header says *"0 available now."*

**LIKELY CAUSE** — **This is correct behaviour**, not a failure. Adaptive selection says *what*;
FSRS says *when*, and it has not come due yet.

**EXACT FIX** — Nothing. Use it — it is one of the strongest points in the demo. To advance,
run the full `demo_adaptive`.

**WHAT TO SAY** — "This is the reconciliation I mentioned. The engine says these sections are
weak; FSRS says not yet. Rather than recommend something the quiz screen cannot deliver, the
interface reports both. An earlier version recommended CAPA and then offered nothing when you
clicked through — I found that in the audit and fixed it."

---

## 5. SME approval rejected / password not accepted

**SYMPTOM** — 400 on approve; nothing changes.

**LIKELY CAUSE** — Wrong password, or you hit the 20/min e-signature throttle.

**EXACT FIX** — Re-type `demo12345`. If throttled, wait 60 seconds.

**WHAT TO SAY** — "That's the e-signature control working — a missing or wrong password returns
400 and changes nothing. Approval re-verifies the reviewer's own password server-side."
*(This is a **good** accidental demo. Use it.)*

---

## 6. Approved question won't edit

**SYMPTOM** — 403 when trying to change an approved question.

**LIKELY CAUSE** — Intended. Approved content is immutable.

**WHAT TO SAY** — "That's by design. Once a question is signed, the e-signature is bound to a
SHA-256 hash of its exact content. Allowing edits would silently invalidate the signature, so
edits are rejected outright rather than tracked."

---

## 7. Learning gain doesn't appear

**SYMPTOM** — No "improved: X → Y" line on a section.

**LIKELY CAUSE** — That section has fewer than **4** answers.

**WHAT TO SAY** — "Gain needs at least four answers on a section — it compares the oldest half
against the newest half. With fewer than that there aren't two halves to compare, so we don't
show a number we can't support."

---

## 8. 401 errors in the browser console

**SYMPTOM** — Red 401s on load.

**LIKELY CAUSE** — The SPA fetches dashboard data on mount before the auth token is restored
from `localStorage`. Pre-existing and cosmetic.

**EXACT FIX** — Ignore. Do not open devtools voluntarily.

**WHAT TO SAY** *(only if asked)* — "The SPA fires its first fetches before the token is
restored, so those 401 and it retries. Cosmetic, pre-existing, and on the list."

---

## 9. Two accounts fighting over the session

**SYMPTOM** — Logging in as the learner logs out the SME, or you see the wrong role's screens.

**LIKELY CAUSE** — The auth token lives in `localStorage`, shared across tabs in one profile.

**EXACT FIX** — Use a **private/incognito window** for the learner. Set this up beforehand.

**WHAT TO SAY** — Nothing; avoid it by preparing the windows in advance.

---

## 10. Backend not running / connection refused

**SYMPTOM** — Every screen empty; `ERR_CONNECTION_REFUSED` in console.

**EXACT FIX**
```bash
cd backend && uv run python manage.py runserver 8000
```

**WHAT TO SAY** — "While that comes up — the management command you're about to see runs the
entire loop end to end and prints every step, so I can show the whole thing without the UI."

---

## 11. SOP upload fails

**SYMPTOM** — Upload rejected.

**LIKELY CAUSE** — Extension allow-list or the 20 MB cap — both server-side.

**WHAT TO SAY** — "Upload validation is server-side: an extension allow-list and a 20 MB cap.
Let me use the SOP that's already processed."

---

## 12. Reprocessing an SOP returns 409

**SYMPTOM** — Conflict when reprocessing a document that has approved questions.

**LIKELY CAUSE** — Intended guard. Reprocessing would delete chunks, cascade away every
learner's `ChunkMastery`, and orphan approved questions.

**WHAT TO SAY** — "That's a deliberate guard, and I'll be honest that it's a guard rather than a
workflow — there is no supported path for revising a procedure yet. That's SOP versioning, and
it's number two on my roadmap precisely because it's the only remaining gap that can silently
destroy learner data."

---

## 13. You are asked something you don't know

**WHAT TO SAY** — "I don't know that off the top of my head — let me tell you what I do know and
where I'd look."

Never invent an implementation detail. Every document in `docs/` is honest about every gap;
being caught guessing costs far more than not knowing.

---

## PRE-FLIGHT CHECKLIST — 15 minutes before

- [ ] `demo_adaptive --stop-after-analysis` has been run
- [ ] Backend running on **8000**, frontend on **5173**
- [ ] Window A: `demo_sme` / `demo12345` → **Question Review**
- [ ] Window B *(incognito)*: `demo_learner` / `demo12345` → **My Learning Path**
- [ ] Terminal ready, sitting in `backend/`
- [ ] `uv run python manage.py test attempts` in a spare terminal → expect **99 OK**
- [ ] Phone/laptop with `FINAL_MEMORIZE_SHEET.md` open

**Credentials:** `demo_sme` / `demo_learner` / `anjali` — all `demo12345`.
