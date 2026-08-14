# FINAL MEMORIZE SHEET

Everything on this page was verified against the running code on 14 Aug 2026. Nothing here is
aspirational. If it is not on this page, do not claim it.

---

## THE ONE SENTENCE

> The system turns controlled SOP content into SME-approved assessments, then uses each
> learner's **question-level** performance to identify weak **source sections** and aim future
> training at those weaknesses.

The load-bearing words are **source sections**. That is what separates this from "an LLM that
makes quizzes."

---

## TECH STACK

| Layer | What |
|---|---|
| Backend | Django 5 + Django REST Framework, **7 apps**: accounts, sops, quiz, ai_engine, attempts, analytics, audit |
| Frontend | React 18 + Vite SPA (single `App.jsx`) |
| Database | PostgreSQL or SQLite, chosen by `DATABASE_URL` |
| Queue | Redis + Celery — **3 tasks**, all `.delay().get()` → **process isolation, not async** |
| AI | NVIDIA NIM — `meta/llama-3.1-8b-instruct` (generation), `nvidia/nv-embedqa-e5-v5` (embeddings) |
| Prod | Gunicorn + WhiteNoise; `runserver` in dev |

**Chunking cascade:** heading-aware regex → Max-Min semantic (embeddings) → fixed-length.
The tier actually used is stored per chunk in `chunking_strategy`.

---

## ADAPTIVE CLASSIFICATION — say this exactly

> "The priority engine is **rule-based over a recency-weighted statistic**. Two model-based
> components — **Elo** and **FSRS** — estimate parameters online from learner responses. It is
> **adaptive**; it is **not machine learning**, and the LLM has **no vote** in any adaptive
> decision."

| Component | Type | Learns from data? |
|---|---|---|
| Priority engine (`_classify`) | **Rule-based** thresholds over a **statistical** signal | Statistic yes, thresholds no |
| Elo | **Model-based**, online parameter estimation | **Yes** |
| FSRS | **Model-based** memory model (DSR) | State yes, weights no |
| Overall | **Hybrid** | — |

**Estimated online:** learner ability, section ability, question difficulty (Elo);
`fsrs_stability`, `fsrs_difficulty` per section.
**Fixed:** 60/80% thresholds, half-life 5, MIN_EVIDENCE 3, streak 3, both K-factors,
all 17 FSRS weights, desired retention 0.9.

**Never call it:** machine learning · a trained model · deep/neural knowledge tracing · IRT ·
"AI-driven adaptation" · computerised adaptive testing.

---

## NUMBERS — 10 that matter

| # | Fact |
|---|---|
| 1 | `w_i = 0.5^(i/5)` — recency weight, `i=0` is the newest answer |
| 2 | Half-life **5** answers; decay factor **0.870551** |
| 3 | `MIN_EVIDENCE = 3` — below 3 answers you cannot *exclude* a section |
| 4 | **HIGH < 60%** · **MEDIUM 60–<80%** · **LOW ≥ 80%** (boundaries fall upward) |
| 5 | Mastered = **3** consecutive passing attempts → priority **NONE** |
| 6 | Pass signal = confidence-filtered (≥0.5), Elo-weighted, **≥ 80%** |
| 7 | Elo: learner **K=32**, question **K=16**, both start at **1500** |
| 8 | FSRS: **17** published weights, desired retention **0.9**, min interval **1 day** |
| 9 | `R(0,S) = 1.0` for **every** S — the reason FSRS cannot select |
| 10 | Learning gain = oldest half vs newest half, needs **≥ 4** answers |

**Worked recency examples (know these cold):**
- `0/5 then 5/5` → **66.7%** weighted (lifetime says 50%)
- `5/5 then 0/5` → **33.3%** weighted — decline caught sooner
- Five more correct → **85.7%** → LOW

---

## ADAPTIVE FACTS

1. The adaptive unit is the **`SOPChunk`** (a section), not the document.
2. The only raw signal is **`AttemptAnswer.is_correct`**.
3. `Question.source_chunk` is the FK the entire claim rests on (`SET_NULL`).
4. `adaptive.py` answers **WHAT**. `fsrs.py` answers **WHEN**.
5. Never assessed → **HIGH**, checked **first** (absence ≠ competence).
6. `selected_for_retraining` **+** `is_due` **=** `available_now`.
7. Questions with no chunk link go to **one explicit bucket** — never silently dropped.
8. `MasteryState` abstract → `TopicMastery` (learner × SOP) + `ChunkMastery` (learner × chunk).
9. Section-level Elo uses **ability-only** update, so one answer can't move difficulty twice.
10. Adaptation is **between** assessments, not within one (CAT deliberately declined).

---

## SME & E-SIGNATURE

- Approval requires the reviewer to **re-enter their own password**, checked server-side with
  `check_password` — not merely an authenticated session.
- On approval the system stores a **SHA-256 `content_hash`** over canonical JSON: question text,
  explanation, difficulty, and the full option set **including which option is correct**, with
  options ordered by id and keys sorted.
- After approval the question is **immutable** — PATCH / PUT / DELETE all return **403**.
- `signature_is_intact()` recomputes the hash on demand and detects out-of-band tampering.
- **17** audit action types; append-only enforced **in the Django admin only**.

---

## SECURITY — verified live

| Control | Result |
|---|---|
| Answer key in learner payload | **Absent** (raw-body checked) |
| `/api/quiz/options/` | Reviewer-only |
| Resubmission | **409**, atomic compare-and-set |
| Foreign-SOP or draft question | **400** |
| **Duplicate question ids** | **400** — no writes at all |
| Editing an approved question | **403** |
| `/media/` | Not routed; authenticated download endpoint only |
| Throttles | login 10/min per IP · e-signature 20/min per user · generation 30/hour · chat 60/hour |

---

## TESTS — 221, zero deleted

| App | Tests |
|---|---|
| attempts | **99** |
| quiz | 36 |
| ai_engine | 36 |
| sops | 19 |
| accounts | 16 |
| analytics | 10 |
| audit | 5 |

Baseline before this project's hardening was **89**. No test was deleted; four were updated for
legitimate behaviour changes, each with written rationale.

---

## DEMO NUMBERS — stable across runs

| | |
|---|---|
| Content | 9 questions, 3 sections, 3 per section |
| First attempt | **33.33%** (3/9) |
| Selection | **6 of 9** — GMP excluded |
| Retraining | 3 retakes at **100%** (6/6) |
| **Learning gain** | **50% → 100%, +50.0 points** on both weak sections |
| Adaptive vs lifetime | **87.9%** adaptive vs **75.0%** lifetime |
| FSRS separation | weak sections due **15 Aug**, strong section **17 Aug** |

⚠️ **Elo values vary run to run** (~1440–1455 weak, ~1545 strong) because questions are
regenerated live each time. Quote the *separation*, not the exact digits.

---

## THE BIGGEST LIMITATION — G2 (say this, not something softer)

> "The adaptive engine's exact offered question set is not yet persisted and enforced
> server-side. Current validation ensures submitted questions belong to the learner's permitted
> SOP/role/approved pool, but a modified client can submit a different eligible question or omit
> questions. A future `QuizAttemptQuestion` model would make the adaptive decision enforceable
> and the attempt reproducible."

Concretely: score is computed over **submitted** answers, so omitting questions inflates it. The
honest client always submits the full offered set (unanswered → `null` → graded wrong).
**Bounded:** same SOP, approved content, own attempts, fully audited.

---

## FUTURE SCOPE — in order

1. **Server-side `QuizAttemptQuestion`** — makes the adaptive decision *enforced*, not advisory
2. **SOP versioning** — the only gap that can silently destroy learner data
3. **Question revision history** — full content lineage
4. **Difficulty-aware priority** — Elo already weights the pass signal, not the priority
5. Semantic duplicate detection → 6. Entailment verification → 7. BKT/IRT → 8. Agentic orchestration

---

## NEVER SAY / SAY INSTEAD

| ❌ | ✅ |
|---|---|
| "GxP compliant" / "validated" | "**GxP-oriented**, with Part 11-**style** technical controls" |
| "prevents hallucinations" | "mitigation at three layers, prevention at one — the human" |
| "provider-agnostic" | "provider-**portable** — two constants in two files" |
| "verified grounding" | "provenance + prompt constraint, **not entailment**" |
| "the adaptive decision is enforced" | "computed and validated server-side; the offered set isn't pinned yet" |
| "fully asynchronous" | "process isolation; the request still waits" |
| "semantic duplicate detection" | "**lexical** near-duplicate detection" |
| "machine learning" | "rule-based over a statistic, with two model-based components" |
| "production ready" | "not deployed; Docker and CI written but not executed as stacks" |
| "we have 221 tests" | "here's what the tests **establish**…" |

---

## THE STORY TO TELL IF THINGS GO QUIET

> "Before I touched the adaptive engine I wrote a controlled scenario — one SOP, three sections,
> a learner strong on GMP and weak on the other two — and asserted the behaviour the system
> *claimed* to have. Three of seven assertions failed immediately.
>
> The worst: whole-SOP mastery was hiding weak sections. Ace three quizzes covering one section
> and the entire SOP was marked mastered and vanished from retraining, while another section was
> still failing. The test returned no assignments at all.
>
> Each failure traced to a concrete design flaw. Each now has a regression test named for the bug
> it prevents."

That story is worth more than the test count, because it shows you tested the **claim**.
