# Project Review Preparation

Everything you need to explain and defend the project. Technically accurate to the repository —
where a rationale is engineering judgement rather than a recorded design decision, it says so.

**Verified state:** 219 backend tests passing · 0 failed · 0 skipped · frontend build clean ·
ESLint 0 errors (2 pre-existing warnings).

---

# PART 1 — The project in plain terms

## What problem does GxP Training Bot solve?

> Pharmaceutical manufacturers must retrain staff on Standard Operating Procedures continuously —
> new hires, periodic requalification, every time a procedure changes. Today a QA trainer
> hand-writes quiz questions from a 20-page document, for every job role, every time. It's slow,
> and worse, everyone gets the same quiz regardless of what they actually got wrong. A score of
> 70% tells you *who* is struggling but not *what* they're struggling with — so nobody can act on
> it.
>
> This system drafts the questions with an LLM, has a qualified human approve each one under an
> electronic signature, and then tracks performance **per section of the procedure** — so when a
> learner fails the CAPA section but passes GMP, the next quiz is CAPA questions, not the whole
> document again.

## The fifteen steps

### 1. SOP ingestion
**What:** An Admin uploads a PDF/DOCX/TXT/MD file with metadata (code, version, department).
**Why:** SOPs are the controlled source of truth — everything downstream derives from them.
**Tech:** Django REST, `SOPDocumentSerializer` validates extension allow-list + 20 MB cap
server-side.
**In:** multipart file + metadata. **Out:** `SOPDocument` row (status `uploaded`) + audit entry.
**Why this way:** validation is server-side, not just an `<input accept=…>`, so a direct API call
can't bypass it.

### 2. Document extraction
**What:** Text is pulled out of the file.
**Why:** The LLM needs text, not a binary.
**Tech:** PyMuPDF for PDF, python-docx for DOCX, direct read for TXT/MD.
**In:** file path. **Out:** plain text (PDFs carry `[Page N]` markers).
**Why this way:** both are the mature, permissively-licensed standards for their formats and
require no external service. *(Engineering rationale — not a recorded decision.)*

### 3. Chunking — a three-tier cascade
**What:** The text is split into sections.
```
Tier 1  heading-aware   regex on "Section 2:" / "3.1" style headings
Tier 2  semantic        Max-Min cosine chunking via NVIDIA nv-embedqa-e5-v5   (only if no headings)
Tier 3  fixed-length    last resort (no API key, or the embedding call failed)
```
**Why:** **This is the most important architectural decision in the project.** The chunk is the
unit everything adaptive is built on. A 20-page SOP in one prompt produces vague questions; more
importantly, without chunks a wrong answer tells you only "they failed the SOP".
**In:** extracted text. **Out:** `SOPChunk` rows, each recording which tier produced it
(`chunking_strategy`).
**Why heading-first:** SOPs are structurally regular by regulatory convention — numbered,
titled sections. Splitting on that gives chunks that map to a coherent part of the procedure, the
heading becomes the section title a learner sees, and it costs no embedding call.

### 4. AI question generation
**What:** For each chunk, the LLM drafts role-specific multiple-choice questions with
explanations.
**Why:** Authoring is the bottleneck — especially writing *why each wrong answer is a compliance
risk*, which is the first thing dropped under time pressure and the actual learning moment.
**Tech:** NVIDIA NIM serving `meta/llama-3.1-8b-instruct`, via the OpenAI-compatible protocol.
Three retries with linear backoff, then a deterministic offline generator.
**In:** chunk text + job role name. **Out:** `Question` rows in `status="draft"` + `Option` rows.
**Why this way:** the offline fallback means the pipeline degrades instead of breaking — no
demo or pilot ever hard-depends on the provider being reachable.

### 5. Source grounding
**What:** Every question stores `source_chunk` — a foreign key to the exact passage it came from.
**Why:** Two reasons. A reviewer can check faithfulness without hunting through the PDF; and —
critically — every *answer* becomes evidence about that *passage*, which is what makes
section-level adaptation possible at all.
**In/Out:** FK set at creation (`ai_engine/tasks.py`), surfaced to the reviewer as `source_text`.
**Honest limit:** this is prompt constraint + stored provenance, **not verified entailment**.

### 6. SME review
**What:** A qualified reviewer sees the question, options, answer key, explanation, **the source
passage**, the chunking strategy, the model's self-reported confidence, and whether it came from
the live model or the offline fallback.
**Why:** A 2026 systematic review of 71 studies found up to **45%** of LLM-generated MCQs contain
factually or clinically implausible content. In compliance training, a wrong question can certify
someone as competent in a procedure they don't understand.
**Tech:** `IsReviewerUser` permission; reviewer-only serializer.

### 7. Electronic signature
**What:** Approval requires the reviewer to **re-enter their password**, verified with
`check_password()`. The system stores a SHA-256 hash of the exact content approved, plus who
approved it and when.
**Why:** So an approval is attributable to a person who re-authenticated at that moment, and so
the signature is bound to *what was signed* — if the text changes afterwards, the hash no longer
matches.
**Out:** `content_hash`, `approved_by`, `approved_at`, plus an audit entry.
**Enforcement:** approved questions return `403` on PATCH/PUT/DELETE; `signature_is_intact()`
detects any change made by a route that bypasses the API.

### 8. Quiz delivery
**What:** The learner receives question text and option text — **and nothing else**.
**Why:** The answer key must never be on the wire before submission.
**Tech:** `LearnerQuestionSerializer` omits `is_correct` and `explanation`; the serializer is
chosen from the user's *role*, not from a query parameter, so a learner can't opt back in. The
queryset also forces `status="approved"` server-side.

### 9. Learner assessment
**What:** Answers are submitted; grading happens entirely server-side from `Option.is_correct`.
**Why:** The client's opinion of correctness is never trusted.
**Protections:** an atomic compare-and-set claims the attempt, so a completed attempt cannot be
resubmitted (`409`); submitted question ids are validated to belong to this attempt's SOP and to
be approved.
**Out:** `AttemptAnswer` rows (one per question) + `QuizAttempt.score`.

### 10. Section-level mastery
**What:** Each answer is attributed to its `source_chunk`, and per-section state is updated.
```
AttemptAnswer.is_correct → Question.source_chunk → SOPChunk → ChunkMastery
```
**Why:** A whole-SOP score conflates everything. The section is the first grain at which a
trainer can actually intervene.
**Out:** `ChunkMastery` (learner × section) and `TopicMastery` (learner × SOP), both carrying
streak, mastery status, Elo ability, FSRS stability/difficulty, and the next review date.

### 11. Adaptive learning — *what* to train
**What:** Each section is classified by **recency-weighted accuracy** into HIGH / MEDIUM / LOW /
NONE; HIGH and MEDIUM are selected for retraining.
**Why:** It's the signal the learner actually generates, and weighting by recency lets the system
recognise improvement.
**Out:** priority + reason string + question ids.

### 12. FSRS — *when* to train
**What:** FSRS-4.5 fits a per-(learner, section) memory model (stability, difficulty) and sets
the next review for the point where recall decays to ~90%.
**Why:** Spaced re-testing improves retention in high-stakes procedural training (BMC Medical
Education 2024, nurse-anaesthesia RCT — a regulated clinical context structurally close to GxP).
**Out:** `next_eligible_at` per section and per SOP.

### 13. Targeted retraining
**What:** For sections that are both **selected** and **due**, a `QuizAttempt` is pre-created and
scoped to those sections' questions.
**Why:** So the learner is taken into the right material, not the whole document.
**Out:** an assignment carrying `question_ids`, the per-section reasons, and the schedule.

### 14. Reassessment
**What:** The learner retakes the targeted quiz; the same loop runs again.
**Why:** This is what closes the loop — the second pass selects different content from the first.

### 15. Learning gain
**What:** For sections with ≥4 answers, the oldest half is compared with the newest half.
**Why:** It turns "CAPA is at 75%" into "CAPA went from 50% to 100%, +50 points" — the visible
proof the loop worked.
**Out:** `initial_accuracy`, `current_accuracy`, `improvement`, computed from stored answers,
never fabricated.

---

# PART 2 — Why this is an adaptive system

## The distinction

```
TRADITIONAL:   SOP → questions → quiz → score → (nothing changes)

THIS SYSTEM:   SOP → questions → SME validation → learner performance
                     → section-level mastery → identify gaps
                     → select targeted content → retrain → reassess
                     → measure improvement → schedule next review
```

The difference is **not** "we keep a score history". It's that the **unit of measurement changes
from the document to the section**, and the *output* of the assessment changes the *input* of the
next one.

Four things a quiz generator does not do:

1. **Attribution** — every answer is evidence about a specific passage, via `source_chunk`.
2. **Per-learner state** — `ChunkMastery` per (learner, section), not a global score.
3. **Differential selection** — the next quiz is a *different set of questions* for a learner weak
   on CAPA than for one weak on Documentation. Proven by test, not asserted.
4. **Timing** — FSRS decides when, independently of what.

## Concrete example — Learner A

Three sections, three questions each. GMP strong, CAPA weak, Documentation medium.

```
Attempt 1
  GMP            3/3 correct
  CAPA           0/3 correct
  Documentation  2/3 correct   →  overall 5/9 = 55.6%
```

A traditional system stops here: *"You scored 56%. Retake the quiz."* — all nine questions.

This system continues:

```
Per-section mastery updated:
  GMP            streak=1  elo≈1545   next_review = +3 days
  CAPA           streak=0  elo≈1454   next_review = +1 day
  Documentation  streak=0  elo≈1470   next_review = +1 day

Adaptive analysis:
  GMP            adaptive score 100%  →  LOW      not selected
  CAPA           adaptive score   0%  →  HIGH     selected
  Documentation  adaptive score  67%  →  MEDIUM   selected   (below the 80% pass mark)

Selection: 6 questions (CAPA + Documentation).  GMP excluded.
```

Note FSRS scheduled the two failed sections **sooner** than the passed one, from the same
attempt, with no special-casing.

After targeted retraining and reassessment, CAPA's adaptive score climbs, its priority drops, and
the learning gain shows `0% → 100% (+100 points)`. GMP was never revisited.

---

# PART 3 — Why chunk-level mastery?

**The question:** *"Why not just store the learner's overall quiz score?"*

## The problem with an overall score

A score identifies a **person**, not a **knowledge gap**. "Rohit scored 70% on SOP-217" tells a
trainer that Rohit needs help — but not with what. The only available intervention is "retake the
whole thing", which wastes time on material he already knows and doesn't concentrate on what he
doesn't.

It's also unstable: 70% could be 7/10 spread evenly, or perfect on nine sections and zero on one.
Those are completely different training needs and identical scores.

## Why the chunk is the actionable unit

The chain is one foreign key:

```
AttemptAnswer.is_correct  →  Question.source_chunk  →  SOPChunk  →  ChunkMastery
```

Because every question was generated *from* a specific passage, every answer *to* it is evidence
*about* that passage. Remove that FK and the system degrades to a conventional quiz with a score —
which is exactly why there is a regression test asserting generation populates it.

The section is the **first grain at which an intervention exists**: you can re-teach "CAPA and
Root Cause Analysis". You cannot re-teach "70%".

## Numerical example

```
SOP-217, three sections, 3 questions each.

Learner answers:  GMP 3/3   CAPA 0/3   Documentation 2/3
Overall: 5/9 = 55.6%
```

| System | What it knows | What it does |
|---|---|---|
| Score-only | "55.6%" | retake all 9 questions |
| This system | GMP 100%, CAPA 0%, Doc 67% | retrain 6 questions — CAPA (HIGH) + Doc (MEDIUM); exclude GMP |

**Why not question-level mastery instead?** A single question is too fine a grain — you'd be
tracking hundreds of states per learner, most with one or two observations, and "you got question
#412 wrong" is not an actionable training statement. Question difficulty *is* tracked separately
as `Question.elo_rating`; the chunk is the unit for the *learner's* knowledge.

**Why keep whole-SOP `TopicMastery` too?** Because a SOP can contain questions with no chunk
linkage (manually authored, or whose chunk was later removed). If whole-SOP mastery were derived
from section mastery, those would make it unreachable. The two are independent signals computed
from the same attempt.

---

# PART 4 — The adaptive algorithm

## 4.1 Recency weighting

```
w_i      = 0.5 ** (i / 5)              i = 0 is the most recent answer
weighted = Σ(w_i · correct_i) / Σ(w_i) × 100
```

- **`i`** is the answer's position counting back from the newest: the latest answer is `i=0`,
  the one before it `i=1`, and so on.
- **Half-life = 5** means an answer five answers ago counts **half** as much as the newest one;
  ten answers ago, a quarter.
- **Why 5?** It's the same window already used for the "recent accuracy" figure displayed in the
  UI, so *recent* means exactly one half-life everywhere in the system. It's a chosen default,
  not a fitted value — with real deployment data it's the first constant I'd tune.

### Why 0/5 then 5/5 gives 66.7%, not 50%

Newest-first the sequence is `[✓✓✓✓✓ ✗✗✗✗✗]` — the five correct answers are the *recent* ones.

```
correct weights (i=0..4):  1.000 + 0.871 + 0.758 + 0.660 + 0.574  =  3.863
wrong   weights (i=5..9):  0.500 + 0.435 + 0.379 + 0.330 + 0.287  =  1.931
weighted = 3.863 / (3.863 + 1.931) = 66.7%
```

Lifetime says 50% — five right, five wrong, all equal. But the learner has *plainly improved*, and
a flat average is structurally incapable of expressing that. Under lifetime they'd stay flagged
HIGH while the interface displayed "Recent: 100%" beside that verdict — the screen contradicting
itself.

**It works both ways.** A declining learner (`5/5 then 0/5`) scores **33.3%** and is flagged
*sooner* than lifetime would catch them. And it's not a reset button: one correct answer after
nine wrong still scores under 30%.

## 4.2 MIN_EVIDENCE = 3

| Answers | Accuracy | Priority | Why |
|---|---|---|---|
| 1/1 | 100% | **MEDIUM** | one lucky answer is not proof |
| 2/2 | 100% | **MEDIUM** | still insufficient |
| 3/3 | 100% | **LOW** (or NONE if mastered) | enough to exclude |
| 1/1 wrong | 0% | **HIGH** | weak is weak, small sample or not |

**Why it's useful:** without it, one correct answer would retire a section as thoroughly as fifty.
The reason string says so explicitly: *"100.0% accuracy (1/1 correct), but only 1 assessment(s)
so far — insufficient evidence to rule this section out (needs 3)."*

**Why asymmetric?** Under-training produces an unqualified operator; over-training costs a few
extra questions. The asymmetry follows the cost — a weak section on a small sample stays HIGH.

**Why 3?** It's the smallest sample that can show a trend at all. Chosen default, not fitted.

## 4.3 Thresholds

| Condition | Priority | Selected |
|---|---|---|
| `answered == 0` | **HIGH** | ✅ never assessed — checked *first* |
| `mastery_status == "mastered"` | **NONE** | ❌ retired |
| `answered < 3` and weighted ≥ 60% | **MEDIUM** | ✅ insufficient evidence |
| weighted **< 60%** | **HIGH** | ✅ |
| weighted **60–<80%** | **MEDIUM** | ✅ |
| weighted **≥ 80%** | **LOW** | ❌ |

- **80%** is `MasteryState.PASS_THRESHOLD` — the pass mark used system-wide, so "proficient"
  means the same thing everywhere.
- **60%** is a chosen default marking "clearly struggling". Say that plainly if asked.
- Boundaries fall **upward**: exactly 60.0 is MEDIUM, exactly 80.0 is LOW.

## 4.4 Mastery / NONE — a separate notion

`mastery_status` is **streak-based**: three consecutive *passing attempts* (≥80% on the
confidence-filtered, Elo-weighted pass signal) flip it to `mastered`, and a mastered section is
retired with priority NONE.

Two notions of "doing well" coexist deliberately:

| | Question it answers | Basis |
|---|---|---|
| `priority` | "is this learner weak *right now*?" | recency-weighted accuracy |
| `mastery_status` | "has this learner passed *repeatedly*?" | streak of 3 |

A section can be `in_progress` with 100% accuracy and LOW priority — competent, not yet retired.
Be ready for that; a sharp reviewer may spot it and think it's a bug. It isn't.

---

# PART 5 — FSRS in viva terms

## What is FSRS?

The Free Spaced Repetition Scheduler — the algorithm behind modern flashcard systems. It models
memory with two numbers per item per learner:

- **stability** — how many days until recall probability decays to ~90%
- **difficulty** — how inherently hard this material is for this learner (1–10)

After each review it updates both from the grade and the elapsed time, then schedules the next
review for the point where recall is predicted to drop to 90%.

We use FSRS-4.5 with the **published default weights**, and only two of its four grades
(`AGAIN` = failed, `GOOD` = passed), because this app has no Hard/Easy signal to give it.

## What problem does it solve?

Everyone forgets at different rates, and different material decays at different speeds. A fixed
"retest every 30 days" ladder over-tests what you know cold and under-tests what you barely
passed. FSRS fits the schedule to the actual learner and the actual section.

## Why not use FSRS to decide *which* topic?

**Because it mathematically cannot.** Retrievability is:

```
R(elapsed, S) = (1 + (19/81) · elapsed / S) ^ -0.5

R(0, S) = 1.0    for every S
```

At `elapsed = 0` — immediately after an assessment — a section the learner just **failed** and a
section they just **passed** both score retrievability 1.0. FSRS's own metric cannot distinguish
them at the moment you most need to.

That's the whole argument:

> **Retrievability answers "is it time to review?" Accuracy answers "is this learner weak?"**
> They are different questions, so they get different algorithms.

## How the two combine

```
selected_for_retraining   the adaptive engine's verdict   (WHAT)
is_due                    this section's FSRS schedule    (WHEN)
available_now             = selected AND is_due
```

- **`auto_assigned_retraining`** hands over only `available_now` sections.
- **`learning_path`** reports *all* selected sections, labelled "Available now" or "Scheduled for
  14 Aug".

Weak material is never hidden — only honestly scheduled. This matters: an earlier version
recommended a section the quiz screen wouldn't offer, and a reviewer clicking through would have
hit a dead end.

## The four cases

| Case | Learning path shows | Auto-assigned | Learner Quiz |
|---|---|---|---|
| **HIGH + due** | "Recommended Next" | offered | "Continue Assigned Retraining" |
| **HIGH + not due** | "Scheduled for Later — due 14 Aug" | not offered | nothing (consistent) |
| **LOW + due** | listed, not recommended | not offered | nothing |
| **MEDIUM + due** | "Recommended Next" | offered | "Continue Assigned Retraining" |

All four verified by execution.

---

# PART 6 — Why these technologies

> Where the repository doesn't record an original rationale, this is labelled **[engineering
> rationale]** — say it that way in the viva rather than claiming a design decision that wasn't
> made.

| Tech | Why | Alternative | Why this one |
|---|---|---|---|
| **Django** | The two hardest requirements are an attributable audit trail and RBAC. Django gives `Group`, a permission framework, and a `ModelAdmin` whose add/change/delete can be denied outright — the append-only audit log is ~20 lines, not custom infrastructure | FastAPI, Flask | Those would need auth, admin, ORM and migrations built or assembled. **[engineering rationale]** |
| **DRF** | Serializers gave the clean split between the reviewer view (with answer key) and the learner view (without) — that split is a security control, not a convenience | Hand-rolled JSON views | Per-action permissions and role-selected serializers come free |
| **PostgreSQL** | Production/CI database; CI runs the suite against a real `postgres:16-alpine` container so there's no SQLite-in-CI/Postgres-in-prod drift | MySQL | One `DATABASE_URL`-driven code path; SQLite for zero-setup dev. **[engineering rationale]** |
| **Redis** | Celery's broker and result backend. Nothing else uses it — no caching, no sessions | RabbitMQ | Simpler operationally for one queue. **[engineering rationale]** |
| **Celery** | Two operations are genuinely slow — PDF parsing and LLM calls (up to 120 s). Running them in-process risks one bad document taking down a worker | Threads, sync | **Be honest:** tasks are currently `.delay(...).get(timeout=…)`, so we get *process isolation*, not request-thread liberation |
| **NVIDIA NIM** | Serves `meta/llama-3.1-8b-instruct` behind an OpenAI-compatible API, so the `openai` client works unchanged — base URL and model name are the only constants that differ. Also the platform for the bootcamp track | OpenAI, Anthropic, local | **Never say "provider-agnostic"** — one provider, hardcoded in two modules. The honest word is *provider-portable* |
| **Llama 3.1 8B** | Large enough for structured MCQ generation from a short passage, small enough to be fast and cheap | 70B, GPT-4 | The task is constrained rewriting of supplied text, not open reasoning. **[engineering rationale]** |
| **PyMuPDF / python-docx** | Mature, permissive, no external service, and PyMuPDF preserves page structure (`[Page N]`) | pdfplumber, Tika | Tika needs a JVM service. **[engineering rationale]** |
| **Heading-aware chunking** | SOPs are structurally regular by regulatory convention. High precision at zero embedding cost, and the heading becomes the section title learners see | Fixed-size only | Fixed-size splits create "weak semantic boundaries" (Moreno-Cediel et al., KBS 2025) — and an arbitrary cut produces a chunk spanning two topics, which makes `ChunkMastery` for it meaningless |
| **Embeddings (`nv-embedqa-e5-v5`)** | Used *only* as the fallback chunker when a document has no detectable headings — Max-Min semantic chunking (Kiss et al., Discover Computing 2025) | Embeddings everywhere | Kept on the same provider so there's one key, one client, one failure mode |
| **FSRS-4.5** | Fits a per-learner memory model instead of one fixed ladder for everyone | SM-2, Leitner | The project *started* with a fixed 1/2/4/7/14/30-day Leitner ladder and moved off it. Per-user parameter optimisation deliberately **not** attempted — insufficient data at this scale |
| **SHA-256** | Binds the signature to exact content: text, explanation, difficulty and the full option set including the answer key | MD5/SHA-1, digital signature | MD5/SHA-1 are collision-broken. A true PKI signature is the fuller answer — this is a hash binding, not a cryptographic identity |
| **React + Vite** | Single SPA covering all seven screens; Vite for fast dev builds | Django templates | The learner quiz is stateful (question-by-question, progress, snapshot). **[engineering rationale]** |

---

# PART 7 — AI / LLM questions

**1. Why use an LLM?** To convert existing controlled source material into assessment content —
drafting questions and, more valuably, the explanation of why the correct answer is compliant and
each distractor is a risk. That explanation is the slowest part of manual authoring.

**2. What exactly does it do?** Reads one chunk, returns a JSON array of MCQs with a difficulty
label, explanation and self-reported confidence. That's all.

**3. What does it NOT decide?** It does not determine regulatory truth, does not approve content,
and cannot publish. Every question it drafts is `status="draft"` and inert until a human signs it.

**4. Can it hallucinate?** **Yes.** Say this plainly. Up to 45% of LLM-generated MCQs in published
studies contain implausible content.

**5. How do you mitigate it?**
> "Mitigation at three layers, prevention at one. The prompt supplies only the chunk text and
> forbids outside knowledge; output is schema-validated so malformed drafts never persist; the
> model self-reports confidence and low-confidence questions are excluded from mastery scoring.
> But the only actual *control* is the fourth layer — a qualified human signs every question."

**Never say "hallucinations are prevented."**

**6. How are questions grounded?** Each stores `source_chunk`, the prompt supplies only that
passage, and the reviewer sees the passage inline.

**7. Why source chunks?** Provenance for the reviewer, and — more fundamentally — it's what makes
an answer evidence about a *passage* rather than a document.

**8. Does provenance prove correctness?** **No.** It proves *where the question came from*, not
that it's *right*. Nothing mechanically checks the correct answer is entailed by the chunk. The
SME gate is the correctness control.

**9. Invalid JSON?** `_normalize_drafts` rejects it — the draft never persists. Markdown fences
are stripped first (instruct models emit them despite being told not to), then parsed, then each
item must have four required keys and ≥2 options. Anything unusable raises and triggers a retry.

**10. NVIDIA NIM fails?** Three retries with linear backoff, then a deterministic offline
generator. The pipeline degrades, never breaks. Questions from that run are marked
`generation_source="mock"` so it's visible.

**11. Why retry?** A single blip — network, rate limit, occasionally-malformed JSON — shouldn't
drop the whole run to offline content. Verified by test: one transient failure recovers.

**12. Why a deterministic fallback?** So a demo or a pilot never hard-depends on a third-party
service. The fallback takes its correct answer verbatim from the SOP text, so it can't hallucinate
— it's lower quality, not lower integrity.

**13. Why not fine-tune?** No training data, and it would move correctness into the weights where
it can't be reviewed. The SME gate is a better use of the effort. **[engineering rationale]**

**14. Why not RAG?** There *is* a retrieval-augmented chatbot for free-text SOP questions. For
*generation* we don't need retrieval — we already know which chunk we're generating from, so
there's nothing to retrieve.

**15. Why not embeddings for everything?** Cost, latency, and a dependency the pipeline is
designed not to require. Embeddings are used exactly where they earn it — semantic chunking when
heading detection fails.

**16. How do you evaluate question quality?** Structurally (schema validation), by provenance
(source chunk shown), by the model's own confidence, and by the SME. **There is no automated
quality score** — say so.

**17. How do you detect duplicates?** Exact signature on (normalised question, normalised correct
answer), plus lexical near-duplicate detection: correct-answer similarity ≥ 0.8 **and** stem
similarity ≥ 0.4. Asymmetric because the *answer* identifies which fact is tested, while rewording
mangles the stem — "What must be done before batch release?" and "Prior to batch release, what is
required?" share only 0.40 stem overlap. **Semantic duplicates sharing no vocabulary still pass.**

---

# PART 8 — SME review + GxP

**Why is SME review required?** Up to 45% of generated MCQs are implausible (PMJ 2026, 71
studies). In compliance training a wrong question can certify someone as competent in a procedure
they don't understand — and that record is what an inspector relies on.

**Why can't the LLM publish directly?** There is no code path. Questions are created `draft`, and
the learner-facing queryset filters to `approved` **server-side** — not by the client passing a
filter. Test: `test_learner_cannot_request_drafts_explicitly`.

**What does the SME verify?** Question, options, answer key, explanation, source passage,
chunking strategy, model confidence, generation source, Elo. **Honest gap:** the system records
*that* they approved, not *what* they checked.

**How does the e-signature work?** The reviewer re-enters their password; it's verified with
`check_password()` — not merely an authenticated session. On success the system stores the content
hash, the approver and the timestamp, and writes an audit entry with `e_signature: true`.

**What is hashed?** Canonical JSON of: question text, explanation, difficulty, and the full option
list (text + `is_correct`), options ordered by id, keys sorted — so the digest is deterministic.

**Why SHA-256?** Collision-resistant and standard; MD5 and SHA-1 are broken. **Honest framing:**
this is a *content binding*, not a cryptographic identity — a true digital signature with a
per-user key pair would be the fuller answer.

**What if approved content is edited?** `403` through the API. And `signature_is_intact()`
recomputes the hash, so a change made by any other route — ORM, admin site, migration — is
*detectable*. **Honest gap:** nothing calls it automatically; it's a backstop, not a monitor.

**Can unsigned questions reach learners?** No.

**Is this GxP compliant?**
> "No, and I wouldn't claim it. It implements several 21 CFR Part 11-**style** technical
> controls — a password-verified approval bound to a content hash, an attributed audit trail,
> role-based access control, immutable approved content. What it does not have is tamper-evident
> audit storage, separation of duties, a training completion/qualification record, or any
> validation exercise. Those are the gap between credible controls and compliance."

**Never say:** "GxP compliant", "validated", "production-ready".

---

# PART 9 — Security

| Attack | Protection | Expected result |
|---|---|---|
| Read the answer key before answering | `LearnerQuestionSerializer` omits `is_correct`/`explanation`; role-selected; `/quiz/options/` reviewer-only | Field absent from payload — 11 tests incl. raw-body inspection |
| Resubmit a completed attempt | Atomic `UPDATE … WHERE completed_at IS NULL` | **409**, score unchanged, audited |
| Arbitrary question id | Validated against SOP + role + approved | **400** with the offending ids |
| Foreign-SOP question | Same | **400** ✔ verified |
| Unapproved (draft) question | Same | **400** ✔ verified |
| Read another learner's data | Queryset scoping on attempts, answers, dashboard, learning path | 404/403 or scoped result |
| Download an SOP unauthenticated | `/media/` route removed; authenticated download endpoint | **401** |
| Edit an approved question | Approval lock | **403** |
| Tamper with signed content out-of-band | `signature_is_intact()` | Detected (on demand) |
| Brute-force login | `LoginRateThrottle` 10/min per IP | **429**, and it blocks a later *correct* password too |
| Brute-force the e-signature | `ESignatureRateThrottle` 20/min per user | **429** |
| Escalate role | `is_staff` / group membership, per-action permissions | 403 |

---

# PART 10 — The two RED issues

## RED #1 — Adaptive decision enforcement

**1. The vulnerability.** The server computes which questions the adaptive engine selected, but
does not persist *the exact set that was offered*. Validation checks that a submitted question
belongs to this attempt's SOP, matches the role, and is approved — not that it was in the
selection.

**2. What an attacker can do.** With a modified client: answer an approved question from the same
SOP that the engine deliberately excluded, and thereby create or advance `ChunkMastery` for that
section. Verified: a section at `priority=none` had its streak go 3→4 and Elo 1544→1556.

**3. What they cannot do.** Inject a question from another SOP (**400**). Inject an unapproved
draft (**400**). Submit a malformed payload (**400**). Resubmit a completed attempt (**409**).
See the answer key. Touch another learner's data. Affect another SOP's mastery.

**4. Why the blast radius is limited.** Same SOP, same role, approved content, own attempts only —
and every attempt is a separate, audited record. The realistic harm is a learner inflating their
own mastery on material that was already approved for them, which the audit trail shows.

**5. Why it wasn't fixed.** The fix needs a new model and a database migration touching the
submission path — which also carries the atomic single-submission claim and the mastery cascade.
Two days before a review, a migration that goes wrong costs the demo. I shipped the *validation*
half, which is low-risk, and documented the rest.

**6. The correct solution.** Persist the offered question set when the attempt is created, and
validate submissions against it.

**7. The model.**
```python
class QuizAttemptQuestion(models.Model):
    attempt  = FK(QuizAttempt, related_name="offered_questions")
    question = FK(Question)
    position = PositiveSmallIntegerField()
    class Meta: unique_together = ("attempt", "question")
```
`submit()` then rejects anything not in `attempt.offered_questions`.

**8. Why future scope.** It's item #1 in `FUTURE_SCOPE.md` — the first thing I'd build next.

### The viva answer
> "Enforcement is layered, and I'm clear about where the layers stop. The server validates that
> every submitted question belongs to this attempt's SOP and is approved — I can show you the
> 400s. What it doesn't yet do is pin the exact set that was *offered*, so a determined client
> could answer an approved question the engine excluded. The blast radius is bounded: same SOP,
> approved content, own attempts, all audited. The fix is one join table validated at submission,
> and I deliberately didn't ship a migration two days before review. It's the first item in my
> future scope."

## RED #2 — SOP version lifecycle

**1. Why blocked?** Reprocessing deletes and rebuilds every `SOPChunk`. `ChunkMastery.sop_chunk`
cascades, so every learner's section mastery for that SOP would be destroyed; and
`Question.source_chunk` is `SET_NULL`, so approved questions would be orphaned from their source
text. Rather than let that happen silently, `POST /process/` returns **409** when approved
questions exist.

**2. Draft-only SOPs.** Reprocessing is still allowed — regenerating before review is the normal
workflow, and there's no approved content or learner history to lose.

**3. Historical attempts.** They survive — `QuizAttempt`, `AttemptAnswer`, `TopicMastery` are all
untouched. But they point at the same **mutable** `SOPDocument` row; there is no version snapshot.

**4. Why it's not a complete solution.** It's a guard, not a workflow. Today there is **no
supported way to publish a revised procedure** — which in a regulated environment is a real
operational limit, not a theoretical one.

**5. The correct architecture.** A `SOPVersion` entity: chunks, questions and mastery bind to a
version; a new version *supersedes* rather than replaces; requalification triggers on version
change.

**6. Why `SOPVersion`?** Because the thing that changes is the *content*, not the *identity*.
SOP-217 is one procedure with many versions; training records must point at the version actually
taken.

**7. Why bind mastery to a version?** Otherwise "Rohit mastered the gowning section" is ambiguous
after a revision — mastered under which text? Binding makes historical records reproducible and
lets you decide deliberately whether a revision invalidates prior mastery.

### The viva answer
> "It's blocked, not solved — deliberately. Reprocessing would delete the chunks, cascade away
> every learner's section mastery and orphan approved questions from their source. Rather than
> let that happen silently I made it return 409. So today revising a procedure has no supported
> path, and that's my number-one limitation. The fix is a SOPVersion entity so a new version
> supersedes rather than replaces, with mastery bound to a version. That's a data migration over
> live learner records — not something I'd ship two days before a review."

---

# PART 11 — The bug story

**Frame it as engineering, not features.** The through-line: *we tested the claim instead of
assuming it.*

## The method

Before touching the adaptive engine, I wrote a controlled scenario — one SOP, three sections
(GMP/CAPA/Documentation), a learner who gets GMP right and the other two wrong — and asserted the
behaviour the system *claimed* to have. Then I ran it against the existing code.

**Three of seven assertions failed immediately.**

### Bug 1 — correctly-answered sections were still retrained

| | |
|---|---|
| **Old** | Selection consumed only the binary `mastery_status`, which flips to `mastered` after 3 consecutive passes. Anything not yet mastered was selected |
| **Wrong because** | A section just answered **correctly** was indistinguishable from one just answered **incorrectly**. For the first two attempts, "adaptive" retraining returned the **entire SOP** — the opposite of adaptive |
| **Discovered** | `test_retraining_targets_only_the_weak_sections` — GMP's questions appeared in the targeted set |
| **Root cause** | A binary state used where a graded one was needed |
| **Fix** | Rank by measured accuracy: <60% HIGH, <80% MEDIUM, else LOW/NONE |
| **Test** | same test, now passing |
| **Result** | GMP excluded from the first retraining round instead of after three more quizzes |

### Bug 2 — never-assessed sections were invisible

| | |
|---|---|
| **Old** | Selection iterated `ChunkMastery` rows. A section never assessed has **no row** |
| **Wrong because** | Absence of data was treated as absence of need. A learner tested only on section A would be retrained only on section A — forever. Newly added sections were unreachable |
| **Discovered** | `test_a_section_never_yet_assessed_is_still_offered_for_training` returned only the already-seen section |
| **Root cause** | Iterating *state* rather than *content* |
| **Fix** | Iterate sections that have approved questions; `answered == 0` is an explicit state, checked *first*, classified HIGH |
| **Result** | "No record" now means "not yet demonstrated", which is what it actually means |

### Bug 3 — whole-SOP mastery hid weak sections

| | |
|---|---|
| **Old** | Candidate SOPs excluded any where `TopicMastery.mastery_status == "mastered"` |
| **Wrong because** | Passing three quizzes that happened to cover one section marked the **whole SOP** mastered — hiding it entirely while another section was still failing |
| **Discovered** | `test_mastering_a_section_removes_it_from_retraining` — an `IndexError`: **no assignments at all** |
| **Root cause** | A coarse-grained gate in front of a fine-grained decision |
| **Fix** | Removed the whole-SOP exclusion; need is decided per section. A mastered topic is still trusted unless there's *measured* evidence against it |
| **Result** | Mastery still retires content, but can no longer conceal a demonstrated weakness |

## The three found later, by audit

### Bug 4 — lifetime accuracy contradicted recent improvement
A learner going 0/5 → 5/5 sat at 50% lifetime and stayed **HIGH**, while the UI displayed
"Recent: 100%" beside that verdict. **The screen contradicted itself**, and the learner couldn't
shed a weak label. Fixed with exponential recency weighting, half-life 5 → 66.7% → MEDIUM, then
85.7% → LOW.

### Bug 5 — small samples weren't discounted
1/1 correct was treated exactly like 50/50. Fixed with `MIN_EVIDENCE = 3`.

### Bug 6 — the recommendation could disagree with availability
A HIGH-priority section in a not-yet-due SOP was recommended by the learning path while the
assignment engine offered nothing — **a live-demo dead end**. Fixed by surfacing `is_due` and
`available_now`, and by making section-level FSRS scheduling (previously computed and never read)
actually drive assignment.

## The sentence to use

> "Instead of assuming the adaptive engine worked, we built a controlled learner scenario and
> tested the expected behaviour. Three failures appeared immediately. We traced each to a concrete
> design flaw, fixed them, added regression tests, and then verified the complete loop."

---

# PART 12 — What the 219 tests actually prove

Don't say "we have 219 tests". Say what they establish.

| Area | Count | What it proves |
|---|---|---|
| **Adaptive scenario** | 7 | The GMP/CAPA/Documentation loop behaves as claimed end-to-end |
| **Recency metric** | 14 | The formula is correct (9 pure) and changes behaviour end-to-end (5) |
| **Evidence sufficiency** | 8 | The full 0/1/2/3+ × high/low-accuracy matrix |
| **Adaptive ↔ FSRS** | 4 | The learning path and the assignment engine cannot contradict each other |
| **Two-learner** | 2 | Different histories produce *different question sets* — not just isolation |
| **Section mastery** | 7 | A miss in one section doesn't reset another; Elo moves exactly once per answer |
| **FSRS** | 10 | The memory model (7 pure) and its scheduling integration (3) |
| **Explainability** | 10 | The reason shown to the learner matches the engine's decision |
| **Retraining/escalation** | 12 | Due-ness, mastery exclusion, targeting, compliance escalation |
| **Assessment integrity** | 21 | Answer key withheld (11), single submission (10) |
| **SME / e-signature** | 14 | Password step-up, content hash, immutability, tamper detection |
| **Submission validation** | 6 | Foreign/unapproved/malformed ids rejected without consuming the attempt |
| **AI generation** | 36 | Live path via mocked provider, retry, malformed JSON, fallback, **grounding**, near-duplicates |
| **Security/RBAC/throttling** | ~25 | Boundaries tested from **both** the permitted and denied side |
| **SOP pipeline** | 19 | Upload validation, chunking cascade, file access control, mutation audit |

**Baseline 89 → 219. Zero tests deleted.** Four were updated for legitimate behaviour changes,
each with the rationale in the test body.

### Why scenario-first mattered
The tests that *found* bugs weren't unit tests of functions — they were a scenario asserting what
the system claimed about a learner. Unit tests would all have passed: each function did what it
said. The defect was in the *composition*.

**The most valuable single test is one that didn't exist before the audit:**
`test_generation_links_questions_to_their_source_chunk`. Every adaptive test built its fixtures by
hand with `source_chunk=chunk` — testing the *consumer* of the grounding link, never the
*producer*. Generation could have stopped populating it and all 176 tests would have stayed green
while chunk-level mastery silently died.

---

# PART 16 — Future scope

See [`FUTURE_SCOPE.md`](FUTURE_SCOPE.md) for the full hierarchy with rationale.

**Immediate:** server-side offered-question binding · SOP versioning · question revision history ·
difficulty-aware priority.
**Advanced:** semantic duplicate detection · entailment verification · mastery decay ·
prerequisite graphs · cross-SOP concept modelling.
**Research:** IRT · computerised adaptive testing · per-user FSRS optimisation.

---

# PART 17 — The 60-second pitch

> "Pharma companies retrain staff on standard operating procedures constantly, and today that
> means a QA trainer hand-writing quiz questions from a 20-page document — for every job role,
> every time the procedure changes.
>
> I built a system that drafts those questions with an LLM — NVIDIA NIM running Llama 3.1 —
> generating each question from one specific *section* of the procedure and keeping a link back
> to it.
>
> Because AI-generated compliance content can't be trusted blind — published studies find up to
> 45% of generated questions are implausible — every question goes to a subject-matter expert who
> approves it by re-entering their password. That signature is bound to a SHA-256 hash of the
> exact content, and approved questions become immutable.
>
> The interesting part is what happens after the learner answers. Because every question knows
> which section it came from, every answer becomes evidence about that section. So instead of
> 'you scored 55%', the system knows you're strong on GMP and weak on CAPA — and the next quiz is
> CAPA questions, not the whole document. A recency-weighted accuracy metric decides *what* needs
> work; FSRS, the spaced-repetition algorithm, decides *when* you see it again.
>
> The demo ends with a measured result: a learner's weak sections going from 50% to 100%, +50
> points, computed from their stored answers.
>
> It's not GxP compliant — there's no validation exercise and no SOP versioning yet — but the
> adaptive loop is real, tested, and I can show you the three bugs I found in it by testing the
> claim instead of assuming it."

---

# PART 18 — The 3-minute technical explanation

**Architecture.** Django REST backend, React SPA, PostgreSQL (SQLite in dev, driven by one
`DATABASE_URL`), Redis + Celery for the two slow operations, NVIDIA NIM as the LLM. Seven Django
apps split by concern: `sops` (ingestion/chunking), `ai_engine` (generation/RAG), `quiz`
(questions + approval), `attempts` (assessment + adaptive), `accounts` (RBAC), `analytics`,
`audit`.

**Data flow.** An SOP is uploaded, text is extracted with PyMuPDF/python-docx, then chunked
through a three-tier cascade: heading-aware regex first, embeddings-based Max-Min semantic
chunking if no headings are found, fixed-length as last resort — and which tier fired is recorded
per chunk. Each chunk goes to the LLM with a prompt constraining it to that text; drafts are
schema-validated, de-duplicated, and persisted with a foreign key to their source chunk. An SME
reviews with the source passage visible and approves under a password-verified signature bound to
a SHA-256 content hash. Only approved questions reach learners, enforced in the queryset.

**Adaptive algorithm.** Grading is server-side. Each answer is attributed to its source chunk, so
we maintain `ChunkMastery` per (learner, section). Priority comes from exponentially
recency-weighted accuracy — `w_i = 0.5^(i/5)`, so an answer five back counts half — because a flat
lifetime average can't represent improvement. Thresholds: below 60% HIGH, below 80% MEDIUM, above
LOW; and a `MIN_EVIDENCE = 3` gate so one correct answer can't retire a section.

**FSRS.** Separate algorithm answering a separate question. FSRS-4.5 with published default
weights fits stability and difficulty per learner per section and schedules the next review at
90% predicted recall. It can't drive *selection*, because retrievability `R(0,S) = 1` for every
stability — right after an attempt a just-failed and a just-passed section look identical. So
adaptive answers *what*, FSRS answers *when*, and `available_now` is their conjunction.

**Security.** The answer key never reaches the client before submission — separate serializers
chosen by role. Grading is server-side; a completed attempt can't be resubmitted, enforced by an
atomic compare-and-set. Submitted question ids are validated against the attempt's SOP and
approval status. Approved content is immutable, uploaded documents require authentication, and
login and signature verification are rate-limited.

**Testing.** 219 tests, up from 89, none deleted. The valuable ones are scenario-first: a
controlled three-section learner scenario that found three real adaptive bugs — including one
where mastering a single section hid a still-failing one.

---

# PART 19 — Memorize this

## 10 formulas / facts
1. `w_i = 0.5^(i/5)` — recency weight, `i=0` newest
2. `0/5 → 5/5` = **66.7%** weighted vs 50% lifetime
3. `5/5 → 0/5` = **33.3%** weighted
4. `MIN_EVIDENCE = 3`
5. HIGH < 60% · MEDIUM 60–<80% · LOW ≥ 80%
6. `R(0,S) = 1.0` for every S — why FSRS can't select
7. Mastery = **3** consecutive passing attempts
8. Pass signal = confidence-filtered, Elo-weighted, **≥80%**
9. Elo: learner **K=32**, question **K=16**
10. Duplicate: answer sim **≥0.8** AND stem sim **≥0.4**

## 10 architecture facts
1. 7 Django apps · React SPA · Postgres/SQLite · Redis · Celery · NVIDIA NIM
2. `meta/llama-3.1-8b-instruct` + `nvidia/nv-embedqa-e5-v5`
3. Chunking cascade: heading → semantic → fixed-length
4. 3 Celery tasks — all `.delay().get()`, so **process isolation, not async**
5. `MasteryState` abstract → `TopicMastery` + `ChunkMastery`
6. `Question.source_chunk` is `SET_NULL` — the FK the whole claim rests on
7. `adaptive.py` = WHAT · `fsrs.py` = WHEN
8. Learner vs reviewer serializers chosen by **role**
9. Audit: 17 action types, append-only in the Django admin only
10. Gunicorn + WhiteNoise for prod; `runserver` for dev

## 10 adaptive facts
1. Adaptive unit = `SOPChunk`
2. Only raw signal = `AttemptAnswer.is_correct`
3. Never-assessed → HIGH, checked **first**
4. Mastered → NONE, retired
5. `selected_for_retraining` + `is_due` = `available_now`
6. Section-level FSRS due-ness drives assignment
7. Unlinked questions → one explicit bucket, never dropped
8. Learning gain = oldest half vs newest half, ≥4 answers
9. Two learners → verifiably different question sets
10. Adaptation is **between** assessments, not within one

## 10 security facts
1. Answer key absent from the learner payload — 11 tests
2. `/quiz/options/` is reviewer-only
3. Resubmission → **409**, atomic compare-and-set
4. Foreign-SOP / draft question → **400**
5. Approved question edit → **403**
6. `signature_is_intact()` detects out-of-band tampering
7. `/media/` removed; authenticated download endpoint
8. Login 10/min per IP; e-signature 20/min per user
9. Dashboard scoped — learners see only their own progress
10. **RED #1:** offered set not pinned — same SOP, approved only, bounded

## 10 limitations / future scope
1. Adaptive selection advisory at submission (RED #1)
2. No SOP versioning — reprocessing returns 409 (RED #2)
3. Grounding = provenance + prompt, **not entailment**
4. Difficulty doesn't affect priority
5. No mastery decay by elapsed time
6. Semantic duplicates (no shared vocabulary) still pass
7. Audit not tamper-evident — admin-UI enforcement only
8. No separation of duties — an Admin can generate *and* approve
9. No training assignment/qualification model
10. No frontend tests; Docker/CI written but not executed

---

# PART 20 — Final review score

| Dimension | Score | One-line justification |
|---|---|---|
| Adaptive Learning | **8**/10 | Recency weighting, evidence gate and FSRS reconciliation all verified by execution; loses points for advisory enforcement and no difficulty weighting |
| Quiz Generation | **7.5**/10 | Three-tier chunking, retry, schema validation and near-duplicate detection; no semantic dedup or automated quality scoring |
| SME Workflow | **8.5**/10 | Content-bound signature with enforced immutability and tamper detection; no revision history or per-question regenerate |
| AI Engineering | **8**/10 | Graceful degradation at three points, nine error categories, mocked-provider tests; single hardcoded provider, no entailment check |
| Assessment Integrity | **8**/10 | Answer key withheld, single submission, ids validated; the offered set is not yet pinned |
| Security | **8**/10 | Fifteen attacks considered, thirteen blocked and tested; RED #1 and non-expiring tokens remain |
| GxP-oriented Design | **6.5**/10 | Real e-signature and 17 audit action types, but no versioning, no qualification record and audit is not tamper-evident |
| Architecture | **7**/10 | Clean app boundaries and framework-free algorithm modules; no server-side quiz entity and Celery is awaited synchronously |
| Testing | **8.5**/10 | 219 tests, scenario-first, none deleted, regression tests named for real bugs; no frontend tests |
| Demo | **8.5**/10 | Full loop on live NVIDIA NIM ending in a measured +50-point gain, both FSRS states verified in the browser |
| **Overall Review Readiness** | **8**/10 | The core claim is demonstrable, tested and honestly bounded; the two REDs are known, scoped and answerable |
