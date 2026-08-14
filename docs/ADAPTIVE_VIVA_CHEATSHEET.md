# Viva Cheat Sheet

One page. Read it on the way in.

---

## THE ONE SENTENCE

> The system converts controlled SOP content into SME-approved assessments, then uses each
> learner's **question-level** performance to identify weak **source sections** and aim future
> training at those weaknesses.

The load-bearing words are **source sections**. That is what separates this from an LLM that
generates quizzes.

---

## 10 FORMULAS / FACTS

| # | Fact |
|---|---|
| 1 | `w_i = 0.5^(i/5)` — recency weight, `i = 0` is the newest answer |
| 2 | `0/5 → 5/5` = **66.7%** weighted (lifetime says 50%) |
| 3 | `5/5 → 0/5` = **33.3%** weighted — decline caught sooner |
| 4 | `MIN_EVIDENCE = 3` — under 3 answers you cannot exclude a section |
| 5 | **HIGH** < 60% · **MEDIUM** 60–<80% · **LOW** ≥ 80% (boundaries fall upward) |
| 6 | `R(0, S) = 1.0` **for every S** — why FSRS cannot select |
| 7 | Mastered = **3** consecutive passing attempts → priority NONE |
| 8 | Pass signal = confidence-filtered, Elo-weighted, **≥ 80%** |
| 9 | Elo: learner **K = 32**, question **K = 16** (ability-only at section level) |
| 10 | Duplicate = answer similarity **≥ 0.8** AND stem **≥ 0.4** |

---

## 10 ARCHITECTURE FACTS

| # | Fact |
|---|---|
| 1 | 7 Django apps · React SPA · Postgres/SQLite · Redis · Celery · NVIDIA NIM |
| 2 | `meta/llama-3.1-8b-instruct` + `nvidia/nv-embedqa-e5-v5` (embeddings) |
| 3 | Chunking cascade: **heading → semantic → fixed-length**, tier recorded per chunk |
| 4 | 3 Celery tasks, all `.delay().get()` → **process isolation, not async** |
| 5 | `MasteryState` abstract → `TopicMastery` (SOP) + `ChunkMastery` (section) |
| 6 | `Question.source_chunk` is the FK the whole claim rests on (`SET_NULL`) |
| 7 | `adaptive.py` = **WHAT** · `fsrs.py` = **WHEN** |
| 8 | Learner vs reviewer serializer chosen by **role**, not by a parameter |
| 9 | Audit: 17 action types; append-only in the Django admin only |
| 10 | Gunicorn + WhiteNoise in prod; `runserver` in dev; timeout 180s on purpose |

---

## 10 ADAPTIVE FACTS

| # | Fact |
|---|---|
| 1 | The adaptive unit is the **`SOPChunk`** (section) |
| 2 | The only raw signal is **`AttemptAnswer.is_correct`** |
| 3 | Never assessed → **HIGH**, checked **first** (absence ≠ competence) |
| 4 | Mastered → **NONE**, retired |
| 5 | `selected_for_retraining` + `is_due` = **`available_now`** |
| 6 | Section-level FSRS due-ness drives assignment (was computed, never read) |
| 7 | Unlinked questions → one explicit bucket, never dropped |
| 8 | Learning gain = oldest half vs newest half, needs ≥ 4 answers |
| 9 | Two learners → verifiably **different question sets**, not just isolation |
| 10 | Adaptation is **between** assessments, not within one (CAT deliberately declined) |

---

## 10 SECURITY FACTS

| # | Fact |
|---|---|
| 1 | Answer key absent from the learner payload — 11 tests, incl. raw-body check |
| 2 | `/api/quiz/options/` is reviewer-only (closed side channel) |
| 3 | Resubmission → **409**, atomic compare-and-set |
| 4 | Foreign-SOP or draft question → **400** |
| 5 | Editing an approved question → **403** |
| 6 | `signature_is_intact()` detects out-of-band tampering (on demand) |
| 7 | `/media/` route removed; authenticated download endpoint only |
| 8 | Login 10/min per IP; e-signature 20/min per user |
| 9 | Dashboard + learning path scoped to the requesting learner |
| 10 | **RED #1:** offered set not pinned — same SOP, approved only, own attempts |

---

## 10 LIMITATIONS / FUTURE SCOPE

| # | Limitation |
|---|---|
| 1 | Adaptive selection **advisory** at submission (RED #1) — fix: `QuizAttemptQuestion` |
| 2 | **No SOP versioning** — reprocessing returns 409 (RED #2) |
| 3 | Grounding = provenance + prompt, **not entailment** |
| 4 | Difficulty weights the pass signal but **not** priority |
| 5 | No mastery decay by elapsed time (decay is by answer position) |
| 6 | Semantic duplicates with no shared vocabulary still pass |
| 7 | Audit **not tamper-evident** — admin-UI enforcement only |
| 8 | **No separation of duties** — an Admin can generate *and* approve |
| 9 | No training assignment / qualification model |
| 10 | No frontend tests; Docker & CI written but never executed as stacks |

---

## THE THREE ANSWERS THAT DECIDE THE VIVA

### "Why is this adaptive?"
> "The content selected for the next assessment changes based on measured per-section performance.
> A learner scores 33% on nine questions and gets six back — only the two weak sections. Because
> every question keeps a foreign key to the SOP chunk it came from, every answer is evidence about
> that passage, not about the document."

### "Can I bypass your adaptive decision?"
> "Partly, and I'll be precise. The server validates that every submitted question belongs to this
> attempt's SOP and is approved — I can show you the 400s. What it doesn't yet do is pin the exact
> set that was *offered*, so a modified client could answer an approved question the engine
> excluded. Bounded: same SOP, approved content, own attempts, all audited. The fix is one join
> table. I didn't ship a migration two days before review."

### "Is this GxP compliant?"
> "No, and I wouldn't claim it. Several Part 11-*style* technical controls — password-verified
> approval bound to a content hash, attributed audit, RBAC, immutable approved content. Missing:
> tamper-evident audit storage, separation of duties, a qualification record, and any validation
> exercise."

---

## NEVER SAY THESE

| ❌ Don't say | ✅ Say instead |
|---|---|
| "GxP compliant" / "validated" | "Part 11-**style** technical controls" |
| "prevents hallucinations" | "mitigation at three layers, prevention at one — the human" |
| "provider-agnostic" | "provider-**portable** — two constants in two files" |
| "verified grounding" | "provenance + prompt constraint, not entailment" |
| "the adaptive decision is enforced" | "computed and validated server-side; the offered set isn't pinned yet" |
| "fully asynchronous" | "process isolation; the request still waits" |
| "semantic duplicate detection" | "lexical near-duplicate detection" |
| "difficulty-aware adaptation" | "difficulty weights the pass signal, not the priority" |
| "production ready" | "not deployed; Docker and CI written but not executed" |
| "we have 219 tests" | "here's what the tests establish…" |

---

## THE STORY TO TELL IF THINGS GO QUIET

> "Before I touched the adaptive engine I wrote a controlled scenario — one SOP, three sections, a
> learner strong on GMP and weak on the other two — and asserted the behaviour the system claimed
> to have. Three of seven assertions failed immediately.
>
> The worst: whole-SOP mastery was hiding weak sections. Ace three quizzes covering one section and
> the entire SOP was marked mastered and vanished from retraining, while another section was still
> failing. The test returned no assignments at all.
>
> Each traced to a concrete design flaw. Each has a regression test named for the bug it prevents."

That story is worth more than the test count, because it shows you tested the *claim*.

---

## NUMBERS TO HAVE READY

| | |
|---|---|
| Backend tests | **219** (baseline 89, zero deleted) |
| Adaptive-relevant tests | ~100 in `attempts` |
| Demo | 9 questions, 3 sections, 33% first attempt |
| Selection | **6 of 9** — GMP excluded |
| Learning gain | **50% → 100%, +50 points**, both weak sections |
| Elo separation from one attempt | 1545 vs ~1454 (from 1500) |
| FSRS separation | failed sections due 14 Aug, passed one 16 Aug |
| LLM hallucination rate (cited) | up to **45%** (PMJ 2026, 71 studies) |
