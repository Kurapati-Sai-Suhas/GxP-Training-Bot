# Live Demo Script — 7 to 10 minutes

Exact commands, exact clicks, exact words. Everything here has been executed and verified.

---

## BEFORE THE DEMO — do this 15 minutes early

### 1. Reset to the demo starting state

```bash
cd backend
uv run python manage.py demo_adaptive --stop-after-analysis
```

This wipes and recreates `SOP-DEMO`, `demo_sme` and `demo_learner`, runs steps 1–8, and **stops
with the learner in the weak state** — which is exactly where you want the UI to be.

> ⚠️ Without this, the database is left in the *post*-retraining state from a previous full run:
> both sections mastered, nothing recommended. The demo falls flat.

### 2. Start both servers, in two terminals

```bash
cd backend && uv run python manage.py runserver 8000
```
```bash
cd frontend && npm run dev
```

### 3. Prepare three browser tabs

| Tab | URL | Logged in as | Left on screen |
|---|---|---|---|
| **1** | `http://localhost:5173` | `demo_sme` / `demo12345` | **Question Review** |
| **2** | `http://localhost:5173` | `demo_learner` / `demo12345` | **My Learning Path** |
| **3** | terminal | — | ready for the final command |

Use a private/incognito window for tab 2 — the auth token lives in `localStorage`, so two logins
in the same browser profile will fight.

### 4. Confidence check

```bash
cd backend && uv run python manage.py test attempts 2>&1 | tail -3
```
Expect `Ran 97 tests … OK`. Have that terminal visible if asked.

### Credentials

| Who | Username | Password |
|---|---|---|
| SME reviewer | `demo_sme` | `demo12345` |
| Learner | `demo_learner` | `demo12345` |
| Admin (seed data) | `anjali` | `demo12345` |

---

## STEP 1 — The SOP (30s)

**Where:** Tab 1 → sidebar → **SOP Library** (2nd icon).

**Point at:** `SOP-DEMO — Quality Management Essentials`, status **Processed**.

> "This is a controlled SOP with three sections — Good Manufacturing Practice, CAPA and Root
> Cause Analysis, and Documentation Practices. The system extracted the text with PyMuPDF and
> split it into three chunks using heading-aware chunking — it detected the numbered section
> headings, so each chunk maps to one real section of the procedure rather than an arbitrary
> character cut. That chunk is the unit everything adaptive is built on."

---

## STEP 2 — Generated questions (45s)

**Where:** Tab 1 → sidebar → **Question Review** (3rd icon).

**Point at:** the badges on any card — role, difficulty, **90% confidence**, **NVIDIA NIM**, Elo.

> "Nine questions, three per section, generated live by NVIDIA NIM running Llama 3.1 8B. Each one
> was generated from a single chunk, with a prompt that supplies only that chunk's text and
> forbids outside knowledge.
>
> Three per section is deliberate — the adaptive engine needs at least three answers before it
> will exclude a section, so two wouldn't let me demonstrate a section being retired."

---

## STEP 3 — Source provenance (60s) ⭐

**Where:** same screen. **Click** the *"View source text (heading chunking)"* disclosure on the
first question.

> "This is how a reviewer answers 'where did this question come from?' The passage shown is the
> exact `SOPChunk` the question was generated from — I've verified the API returns the stored
> chunk text byte-for-byte.
>
> Being precise: this is **provenance plus prompt constraint. It is not verified entailment.**
> Nothing mechanically checks that the correct answer is actually supported by this passage.
> That's what the human gate is for."

---

## STEP 4 — Approve with e-signature (60s) ⭐

**Where:** same screen. Questions are already approved by the reset, so **describe** rather than
click — or reject one and re-approve it if you want it live.

> "Approval requires the reviewer to re-enter their own password, verified server-side with
> `check_password` — not just an already-authenticated session. On success the system stores a
> SHA-256 hash of the exact content approved: question text, explanation, difficulty, and the full
> option set including which one is correct.
>
> After that the question is immutable — PATCH, PUT and DELETE all return 403. And if anything
> changes it by another route, the ORM or the admin site, `signature_is_intact()` recomputes the
> hash and detects it.
>
> Studies find up to 45% of LLM-generated multiple-choice questions contain implausible content.
> That's why this gate is mandatory — the LLM drafts, it never decides."

---

## STEP 5 — The learner's position (30s)

**Where:** Tab 2 (`demo_learner`) → **My Learning Path** (5th icon).

> "The learner has taken one quiz and scored 33% — three of nine. But a percentage tells a trainer
> *who* is struggling, not *what* with. Watch what the system knows instead."

---

## STEP 6 + 7 — The adaptive analysis (2 min) ⭐⭐ **THE CENTREPIECE**

**Where:** same screen. Read the section cards top to bottom.

**Point at each element in this order:**

| Element | Say |
|---|---|
| **Section 3 & 2, red border, "Needs Review", "Priority: HIGH"** | "Two sections flagged weak" |
| `Adaptive score: 0%` | "This is the number the decision was made on" |
| `Lifetime: 0%` / `Recent 3: 0%` | "Both shown, so the screen can never contradict its own verdict" |
| `Answered: 0/3` | "Three answers — enough evidence to act on" |
| `Ability: 1455` / `Memory: ~0.4d` | "Elo ability, and the FSRS memory model" |
| The reason line | "Every recommendation carries the measured evidence that produced it" |
| **Section 1, green, "Strong"** | "100% — **excluded**. The learner will not be dragged back through it" |
| **"Scheduled for Aug 14"** badge | ⭐ see below |
| **"2 section(s) need review · 0 available now"** | ⭐ see below |

> "Now the part I'm most pleased with. These sections are flagged HIGH — the adaptive engine says
> they need work. But the badge says **'Scheduled for Aug 14'**, and the header says **'0
> available now'**.
>
> That's two different algorithms answering two different questions. Adaptive selection answers
> *what* needs attention — recency-weighted accuracy per section. FSRS, the spaced-repetition
> algorithm, answers *when* — it has scheduled these for tomorrow because revisiting them right
> now is less effective.
>
> They can't be merged, and there's a mathematical reason: FSRS retrievability is
> `R(0,S) = 1.0` for every stability value. Immediately after an assessment, a section you just
> failed and a section you just passed both score 1.0 — FSRS literally cannot tell them apart at
> the moment you most need it to.
>
> So the interface reports both, and it never promises a quiz that doesn't exist. An earlier
> version of this recommended CAPA and then offered nothing when you clicked through. I found
> that in the audit and fixed it."

---

## STEP 8 — Targeted retraining becomes available (60s)

**Where:** Tab 3 (terminal).

```bash
cd backend && uv run python manage.py demo_adaptive
```

While it runs, narrate. Then **switch to Tab 2 and refresh**.

> "I'm running the full loop. It fast-forwards the schedule — only the schedule, and the command
> prints a warning saying so. Every score, every mastery update and every selection comes from the
> real engine.
>
> Now the same sections say **'Available now'**, and the header says **'2 available now'**."

**Then click Learner Quiz (4th icon)** and point at *"Continue Assigned Retraining — SOP-DEMO"*.

> "And there it is on the quiz screen. What the learning path promised, the quiz screen delivers."

---

## STEP 9 + 10 — Reassessment and measured gain (90s) ⭐⭐ **THE PAYOFF**

**Where:** Tab 3 terminal output, steps 9–11.

**Read out:**

```
[9]  6 question(s) selected for targeted retraining.
     Excluded (already strong): Section 1: Good Manufacturing Practice

[11] Section 3: Documentation Practices  score=87.9% lifetime=75.0% status=mastered
        -> improved: 50.0% -> 100.0% (+50.0 points)
     Section 2: CAPA and Root Cause Analysis  score=87.9% lifetime=75.0% status=mastered
        -> improved: 50.0% -> 100.0% (+50.0 points)
```

> "**Six questions, not nine.** GMP was excluded — the learner was never re-tested on material
> they'd demonstrated.
>
> And this is the number that matters: **50% to 100%, plus 50 points**, on both weak sections.
> That's computed from their stored answers — the oldest half of that section's history compared
> with the newest half. It isn't asserted, it's measured.
>
> One more detail: the adaptive score is 87.9% while lifetime is 75%. That's the recency weighting
> recognising that the learner has improved. A flat lifetime average would still be dragging their
> early failures along — which was a real bug I found: the screen said 'Recent: 100%' next to a
> HIGH priority badge, contradicting itself."

---

## STEP 11 — Next review scheduled (20s)

**Where:** Tab 2 → refresh **My Learning Path**.

> "Both sections are now mastered and retired from retraining. FSRS has scheduled their next
> review based on the memory model it fitted from the actual answers — not a fixed 30-day ladder."

---

## CLOSING LINE

> "That's a closed adaptive learning loop rather than static quiz generation: the SOP produced the
> questions, a qualified human signed them, the learner's per-section performance identified the
> gaps, the system selected only the weak material, the learner improved, and the improvement was
> measured — not claimed."

---

## If they ask for more (in priority order)

1. **"Show me the tests"** → `uv run python manage.py test attempts` → 97 tests
2. **"Can a learner see the answers?"** → devtools → Network → `/api/quiz/questions/` → no
   `is_correct` field anywhere in the payload
3. **"What if the AI fails?"** → `ai_engine/services.py` — 3 retries then the offline generator;
   `classify_llm_error` buckets failures into 9 categories
4. **"Show me the bug you found"** → `attempts/tests.py::AdaptiveLearningScenarioTests`

---

# FAILURE RECOVERY

Read this before the demo. The rule for all of them: **name it, explain the design, move on.**
Never troubleshoot live.

### NVIDIA NIM unavailable / generation slow
**Do:** let it run — after 3 retries it falls back automatically.
**Say:** "The provider is unreachable, so it's fallen back to the deterministic offline generator.
That's designed behaviour — three retries with backoff, then a fallback whose correct answer is
taken verbatim from the SOP text, so it can't hallucinate. Lower quality, not lower integrity. The
questions are tagged `generation_source='mock'` so a reviewer can see which path produced them."
**Don't say:** "it's broken" / "it should work" / start editing `.env`.

### Quiz generation fails outright
**Do:** move to Tab 1 and use the questions already approved by the reset.
**Say:** "The nine approved questions are already in the review queue — let me show you those,
they were generated live earlier."

### Demo data looks wrong / sections already mastered
**Do:** run `uv run python manage.py demo_adaptive --stop-after-analysis` and refresh.
**Say:** "Let me reset to the starting state — this command rebuilds the scenario in about
fifteen seconds."

### "Nothing is due right now" and you wanted the available state
**Do:** nothing — this is **correct behaviour**, use it.
**Say:** "This is exactly the reconciliation I mentioned. The engine says these sections are weak;
FSRS says not yet. Rather than recommend something the quiz screen can't deliver, the interface
says 'Scheduled for Aug 14'." Then run the full demo command to advance it.

### Learning gain doesn't appear
**Do:** check the section has ≥4 answers.
**Say:** "Gain needs at least four answers on a section — it compares the oldest half against the
newest half. With fewer than that there aren't two halves to compare, so we don't show a number we
can't support."

### Frontend shows 401s in the console
**Do:** ignore.
**Say (only if asked):** "The SPA fetches dashboard data on mount before the token is restored, so
the first requests 401 and it falls back. Cosmetic, pre-existing, on the list."

### SOP upload fails
**Do:** use the existing `SOP-DEMO`.
**Say:** "Upload validation is server-side — extension allow-list and a 20 MB cap. Let me use the
SOP that's already processed."

### SME approval fails / password rejected
**Do:** confirm you typed `demo12345`.
**Say:** "That's the e-signature control working — a missing or wrong password returns 400 and
changes nothing. Let me re-enter it." (This is a *good* accidental demo — use it.)

### Backend not running / connection refused
**Do:** restart it; meanwhile talk over the terminal output.
**Say:** "While that comes up — the command you're looking at runs the entire loop end to end and
prints every step, so I can show the whole thing without the UI if needed."

### You're asked something you don't know
**Say:** "I don't know that off the top of my head — let me tell you what I do know and where I'd
look." Never invent an implementation detail. The audit documents in `docs/` are honest about
every gap; being caught guessing costs more than not knowing.
