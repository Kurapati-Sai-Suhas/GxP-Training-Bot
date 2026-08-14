# The Review Story — 5 to 10 Minutes

A narrative you can deliver at a whiteboard, with the technical backing to survive follow-up.
Every number is real and reproducible via `manage.py demo_adaptive`.

---

## The one-sentence version

> The GxP Training Bot converts controlled SOP content into SME-approved assessments, then uses
> each learner's **question-level** performance to identify weak **source sections** and aim
> future training at those weaknesses.

The load-bearing words are *source sections*. That is what separates this from an LLM that
generates quizzes.

---

## The arc

### 1. The problem
Pharma manufacturers run continuous, auditable SOP training. Today a QA trainer hand-writes quiz
questions from a 20-page procedure, per job role, every time the SOP changes.

### 2. Why static training fails
`SOP → fixed quiz → score`. Three failures:
- Writing a compliance explanation for *every wrong answer* is the slowest part, so it gets
  dropped — removing the actual learning moment.
- Everyone retakes the whole procedure regardless of what they got wrong.
- "70% on SOP-217" identifies a *person*, not a *knowledge gap*. A trainer cannot act on it.

### 3. AI-generated assessment
NVIDIA NIM (Llama 3.1 8B) drafts role-specific questions **from one chunk of SOP text at a
time**, including the explanation of why the correct answer is compliant and why each distractor
is a risk. Three retries, then a deterministic offline fallback — the pipeline degrades, never
breaks.

### 4. Why SME review is non-negotiable
A 2026 systematic review of 71 studies found up to **45%** of LLM-generated MCQs contain
factually or clinically implausible content. In compliance training, a wrong question can
certify someone as competent in a procedure they don't understand — and that record is what an
inspector later relies on.

So: `status="draft"` on creation, and the learner-facing queryset filters to `approved`
**server-side**. Approval requires the reviewer to re-enter their password, and the signature is
bound to a SHA-256 hash of the exact content approved. Approved content is immutable through the
API.

> **The line to say out loud:** "The LLM drafts. It never decides. There is no code path from
> generation to a learner that does not pass through a human signature."

### 5. Learner performance
The learner sits the quiz. Grading is entirely server-side from `Option.is_correct` in the
database — and the answer key is **never sent to the client** before submission.

### 6. Why an aggregate score isn't enough
A score conflates everything. We keep one `AttemptAnswer` row per question per attempt, and every
question carries a foreign key to the chunk it was generated from:

```text
AttemptAnswer.is_correct → Question.source_chunk → SOPChunk → ChunkMastery
```

**That FK is the whole idea.** Remove it and this is a conventional quiz with a score.

### 7. Chunk-level mastery
`ChunkMastery` is per (learner, section). An answer becomes evidence about *a specific passage of
the procedure* — the first grain at which a trainer can actually intervene.

### 8. Adaptive selection — *which* content
Lifetime accuracy per section → priority:

| Condition | Priority | Selected |
|---|---|---|
| Never assessed | HIGH | ✅ |
| Accuracy < 60% | HIGH | ✅ |
| Accuracy < 80% | MEDIUM | ✅ |
| ≥ 80%, not yet mastered | LOW | ❌ |
| 3 passes in a row | NONE | ❌ |

### 9. FSRS — *when*
Separate algorithm, separate question. FSRS-4.5 fits a per-(learner, section) memory model and
schedules the next review at the point recall decays to ~90%.

> **They cannot be merged, and here's the proof:** FSRS retrievability is `R(0,S) = 1.0` for
> every stability value. Immediately after an attempt, a section just *failed* and a section just
> *passed* both score 1.0. Retrievability answers "is it time?"; accuracy answers "is this
> learner weak?"

### 10. Targeted retraining → reassessment
The weak sections' questions become the next quiz. The learner retakes, mastery updates, and the
loop closes.

---

## The concrete learner example

Real output, live NVIDIA NIM:

```text
Attempt 1 — 33.33% (3/9 correct)

Per-section mastery after grading:
  Section 1: GMP            streak=1  elo=1545  next_review=2026-08-16
  Section 2: CAPA           streak=0  elo=1454  next_review=2026-08-14
  Section 3: Documentation  streak=0  elo=1454  next_review=2026-08-14

Adaptive analysis:
  >> Section 3: Documentation  HIGH  adaptive score 0.0% | 0/3 correct — below the 60% threshold
  >> Section 2: CAPA           HIGH  adaptive score 0.0% | 0/3 correct — below the 60% threshold
     Section 1: GMP            LOW   adaptive score 100.0% | 3/3 — 1 of 3 toward mastery

Selection: 6 questions.  Excluded (already strong): Section 1: GMP

After 3 targeted retakes:
  Documentation  score=87.9%  lifetime=75%  status=mastered  -> improved 50% -> 100% (+50 points)
  CAPA           score=87.9%  lifetime=75%  status=mastered  -> improved 50% -> 100% (+50 points)
```

**Three details worth pausing on:**

- **Elo separated the sections from a single attempt** — 1545 vs ~1454, from a common 1500 start.
- **FSRS scheduled the failed sections two days earlier than the passed one**, with no
  special-casing. That is the spaced-repetition layer doing its own job, visibly.
- **The adaptive score (87.9%) sits above lifetime (75%)** — recency weighting recognising that
  the learner has improved, which is exactly what a flat average cannot express.

**6 questions, not 9.** The learner is not dragged back through material they demonstrated they
knew. And the **+50 points** is measured from stored answers, not asserted.

---

## The three bugs — tell this story, it is your strongest material

The controlled scenario was written **first**, then run against the existing code. Three of seven
assertions failed immediately:

| Bug | Old behaviour | Why it was wrong |
|---|---|---|
| 1 | Selection used only binary `mastery_status` | A section just answered **correctly** was indistinguishable from one answered **incorrectly** until a 3-streak — so early "adaptive" retraining returned the **whole SOP** |
| 2 | Selection read `ChunkMastery` rows | A never-assessed section has **no row** — so it was permanently invisible. A learner tested only on section A would be retrained only on section A, forever |
| 3 | Mastered SOPs excluded wholesale | Acing three quizzes covering one section marked the whole SOP mastered — **hiding it while another section was still failing** |

> **Why this is your best story:** it shows you tested the claim rather than assuming it, and
> that "adaptive" was not adaptive until you proved it wasn't. Reviewers trust that far more than
> a clean demo.

---

## Answering "why is this adaptive rather than just an LLM quiz generator?"

Four things an LLM quiz generator does not do:

1. **Attribution** — every answer is evidence about a specific passage, via `source_chunk`.
2. **Per-learner state** — `ChunkMastery` per (learner, section), not a global score.
3. **Differential selection** — the next quiz is a *different set of questions* for a learner
   weak on CAPA than for one weak on Documentation.
4. **Timing** — FSRS decides when, independently of what.

An LLM alone gives you step one of fifteen.

---

## Where to be honest before they ask

Lead with these; being first to name them converts weakness into credibility.

1. **Grounding is provenance + prompt constraint, not verified entailment.** Nothing mechanically
   checks the correct answer is supported by the chunk. The SME gate is the control.
2. **The adaptive selection is advisory at submission time.** Submitted questions are validated
   to belong to this attempt's SOP and to be approved, but no server-side record pins the exact
   set that was *offered* — so a modified client could answer an approved question the engine
   excluded. Bounded: same SOP, approved content, own attempts only.
3. **No SOP versioning.** Reprocessing a revised procedure is blocked (`409`) rather than
   versioned, so revising a procedure has no supported workflow yet.
4. **Difficulty does not affect priority.** Elo weights the pass signal but not the accuracy used
   for selection, so an easy miss and a hard miss count the same.
5. **Decay is by answer position, not elapsed time.** Ten answers ago weighs the same whether it
   was yesterday or last year; calendar time enters only through FSRS.
6. **Not GxP compliant** — several Part 11-*style* controls, no validation exercise.

*(Two items that used to be on this list are now fixed and are worth mentioning as such: priority
was lifetime-based and ignored improvement — it is now recency-weighted with a half-life of 5
answers; and small samples were not discounted — `MIN_EVIDENCE = 3` now prevents a one-answer
section from being excluded.)*

---

## If you have only 60 seconds

> "An LLM drafts questions from one section of an SOP at a time. An SME signs each one, and the
> signature is bound to a hash of the content. When a learner answers, we don't just record a
> score — we record which *section* each answer came from. That gives us per-section mastery, so
> when the learner fails CAPA but passes GMP, the next quiz is CAPA questions, not the whole
> document. FSRS decides *when* they see it again; accuracy decides *what*. We found three bugs
> in that engine by writing the test first — including one where mastering a single section hid a
> still-failing one."
