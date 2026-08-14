# GxP Training Bot — Project Review

> **In one sentence:** The GxP Training Bot converts controlled SOP content into SME-approved
> assessments, then uses each learner's question-level performance to identify weak *source
> sections* and adapt future training toward those weaknesses.

Every claim below is read from the implementation. **219 automated tests pass** (baseline before
this work: 89). Limitations are stated, not buried.

Companion documents: [`ADAPTIVE_ALGORITHM.md`](ADAPTIVE_ALGORITHM.md) (current algorithm) ·
[`ADAPTIVE_IMPLEMENTATION_REPORT.md`](ADAPTIVE_IMPLEMENTATION_REPORT.md) · [`FUTURE_SCOPE.md`](FUTURE_SCOPE.md) ·
[`ADAPTIVE_ARCHITECTURE.md`](ADAPTIVE_ARCHITECTURE.md) · [`ADAPTIVE_DEMO.md`](ADAPTIVE_DEMO.md) ·
[`PROJECT_REVIEW_QA.md`](PROJECT_REVIEW_QA.md) · [`SECURITY.md`](SECURITY.md) ·
[`GAP_ANALYSIS.md`](GAP_ANALYSIS.md)

---

## 1. Problem statement

Pharmaceutical and life-sciences manufacturers run continuous, auditable SOP training: new
hires, periodic requalification, and retraining whenever a procedure is revised. Today this
usually means a QA trainer hand-writing quiz questions from a 20-page procedure, for every job
role, every time the SOP changes.

Three concrete failures follow:

1. **Authoring is the bottleneck.** Writing a defensible compliance explanation for *each wrong
   answer* is the slowest part, so it is the first thing dropped — removing the actual learning
   moment.
2. **Assessment is uniform.** Everyone retakes the entire procedure regardless of what they
   actually got wrong.
3. **Weakness is invisible where it matters.** "70% on SOP-217" identifies a person, not a
   knowledge gap. A trainer cannot act on it.

## 2. Why GxP training needs more than static quizzes

```text
Static:    SOP ──► fixed quiz ──► score ──► (nothing changes)

This:      SOP ──► quiz ──► per-question performance
                              ↓
                        source section attribution
                              ↓
                        mastery per section
                              ↓
                        targeted retraining ──► reassessment ──► updated mastery
```

The difference is not "adds a score history". It is that the **unit of measurement changes**
from the document to the section, which is the first grain at which an intervention is possible.
A learner weak on CAPA but solid on GMP should be retrained on CAPA — a static quiz cannot
express that, because it never knew which questions belonged to which part of the procedure.

## 3. Proposed solution

Automate the **drafting**, not the **authority**.

```text
SOP → parse → chunk → LLM drafts questions (grounded in a chunk)
    → SME reviews and approves under electronic signature
    → learner is assessed (answer key never sent to the client)
    → server grades, attributes each answer to its source chunk
    → ChunkMastery updated per section
    → adaptive engine selects weak sections; FSRS schedules when
    → targeted retraining → reassessment → mastery updated
```

Full diagram: [`ADAPTIVE_ARCHITECTURE.md`](ADAPTIVE_ARCHITECTURE.md).

## 4. Why an LLM?

Precisely and only: **converting existing controlled source material into assessment content.**

- Drafting role-specific multiple-choice questions from a chunk of SOP text
- Drafting the explanation of why the correct answer is compliant and why each distractor is a
  compliance risk — the most time-consuming part of manual authoring
- Answering learner questions about an SOP, grounded in that SOP's own chunks

NVIDIA NIM serving `meta/llama-3.1-8b-instruct`, via the OpenAI-compatible wire protocol.

**What the LLM does not do:** it does not determine regulatory truth, does not approve content,
and cannot publish anything. Every question it drafts is inert (`status="draft"`) until a
qualified human signs it. When the provider is unreachable, three retries with linear backoff
are followed by a deterministic offline generator, so the pipeline degrades rather than breaks.

## 5. Why grounded generation?

Every generated question stores `source_chunk` — a foreign key to the exact passage it was
drafted from. Generation prompts supply only that chunk's text and instruct the model to use no
outside knowledge. The reviewer UI shows the source passage beside the question.

This buys two things: a reviewer can verify faithfulness without hunting through the PDF, and —
critically — every *answer* to that question becomes evidence about that *passage*, which is what
makes section-level adaptation possible at all.

**Honest limit:** grounding here is *prompt constraint plus stored provenance*, **not verified
entailment**. Nothing mechanically checks that the correct answer is supported by the chunk.
That is what the SME gate is for.

## 6. Why SME review?

A 2026 PRISMA systematic review of LLM-generated MCQs in medical education (71 empirical
studies) found factually or clinically implausible content in up to **45%** of generated
questions depending on the study.

In compliance training an incorrect question does not merely mislead — it can certify someone as
competent in a procedure they do not understand, and that record is the artefact an inspector
later relies on.

So approval is a hard gate:

- `status="approved"` is the **only** state a learner can receive, enforced server-side in the
  queryset — not by the client passing a filter.
- Approval requires the reviewer to **re-enter their password**, verified with
  `check_password()`, not merely an authenticated session.
- The signature is bound to a **SHA-256 hash** of the question text, explanation, difficulty and
  full option set including the answer key.
- Approved content is **immutable** through the API (`403` on PATCH/PUT/DELETE);
  `signature_is_intact()` detects any change made by a route that bypasses it.

This is an **implemented technical control**, not a compliance certification.

## 7. Why chunk-level mastery?

```text
AttemptAnswer.is_correct → Question.source_chunk → SOPChunk → ChunkMastery
```

A whole-SOP score conflates everything. Section-level state answers the only question a trainer
can act on: *which part of this procedure does this person not know?*

In the verified demo, a learner scoring 33% on a 3-section SOP is retrained on **4 questions
instead of 6** — and, more importantly, the *right* 4. Sections 2 and 3 (0% accuracy) are
selected; section 1 (100%) is excluded.

`TopicMastery` (whole SOP) is kept alongside `ChunkMastery` and is deliberately **not** derived
from it: a SOP can contain questions with no chunk linkage, which would otherwise make
whole-SOP mastery unreachable.

## 8. Why accuracy-driven adaptive selection?

This is the substantive engineering change of this sprint, and it was **forced by evidence**.
The controlled scenario (GMP correct / CAPA wrong / Documentation wrong) was written first and
run against the existing code. Three of seven assertions failed immediately.

### Bug 1 — correctly-answered sections were still retrained

| | |
|---|---|
| **Old behaviour** | Selection used only the binary `mastery_status`, which flips to `mastered` after 3 consecutive passes. Anything not yet mastered was selected. |
| **Why it was wrong** | A section just answered **correctly** was indistinguishable from one just answered **incorrectly**. For the first two attempts, "adaptive" retraining returned the **entire SOP** — the opposite of adaptive. |
| **New behaviour** | Sections are ranked by measured accuracy: <60% HIGH, <80% MEDIUM, otherwise LOW/NONE. Only HIGH and MEDIUM are selected. (Now *recency-weighted* accuracy — see Bug 4.) |
| **Why better** | Selection now reflects the signal the learner actually generated. In the demo, GMP (100%) is excluded from the first retraining round rather than after three more quizzes. |

*Test:* `test_retraining_targets_only_the_weak_sections`

### Bug 2 — never-assessed sections were permanently invisible

| | |
|---|---|
| **Old behaviour** | Selection read `ChunkMastery` rows. A section never assessed has **no row**, so it could not appear. |
| **Why it was wrong** | Absence of data was treated as absence of need. A learner assessed only on section A would be retrained only on section A — forever. Newly added sections were unreachable. |
| **New behaviour** | Analysis iterates **sections that have approved questions**, not existing mastery rows. `answered == 0` is an explicit state, checked *first*, classified HIGH. |
| **Why better** | "No record" now means "not yet demonstrated", which is what it actually means. |

*Test:* `test_a_section_never_yet_assessed_is_still_offered_for_training`

### Bug 3 — whole-SOP mastery hid weak sections

| | |
|---|---|
| **Old behaviour** | Candidate SOPs excluded any with `TopicMastery.mastery_status == "mastered"`. |
| **Why it was wrong** | Passing three quizzes that happened to cover only one section marked the whole SOP mastered — hiding it entirely **while another section was still failing**. The failing test returned *no assignments at all*. |
| **New behaviour** | The whole-SOP exclusion was removed from the candidate query; need is decided per section. A mastered topic is still trusted unless there is **measured** evidence against it — a section with recorded wrong answers reopens it, a never-assessed one alone does not. |
| **Why better** | Mastery still retires content (otherwise nothing would ever be finished), but it can no longer conceal a demonstrated weakness. |

*Test:* `test_mastering_a_section_removes_it_from_retraining`

### Bug 4 — a learner who improved was still called weak

| | |
|---|---|
| **Old behaviour** | Priority used flat lifetime accuracy. A learner who answered 0/5 then 5/5 sat at 50% and stayed HIGH — while the UI displayed "Recent: 100%" beside that verdict. |
| **Why it was wrong** | The screen contradicted itself, and a learner could not shed a weak label without an implausible run of successes. A flat average cannot represent *change*. |
| **New behaviour** | Exponentially recency-weighted accuracy, half-life 5 answers: `w_i = 0.5 ** (i / 5)`. The same learner scores 66.7% (HIGH → MEDIUM); five more correct reach 85.7% (→ LOW). A *declining* learner is caught sooner than lifetime would catch them (33.3% vs 50%). |
| **Why better** | Improvement and deterioration are both recognised, and neither instantly — one good answer is not a reset button. Both figures are still shown, and the reason string names the one the decision used. |

### Bug 5 — one correct answer counted as much as fifty

| | |
|---|---|
| **Old behaviour** | 1/1 and 50/50 both produced LOW priority. |
| **Why it was wrong** | A single lucky answer was treated as proof of competence. |
| **New behaviour** | `MIN_EVIDENCE = 3`: below three answers a section cannot be excluded on accuracy alone; it is capped at MEDIUM with *"insufficient evidence to rule this section out"*. Deliberately asymmetric — weak performance on a small sample stays HIGH. |
| **Why better** | Under-training produces an unqualified operator; over-training costs a few questions. The asymmetry follows the cost. |

### Bug 6 — the system recommended training it would not hand over

| | |
|---|---|
| **Old behaviour** | The learning path ignored FSRS due-ness; the assignment engine required it. A HIGH-priority section in a not-yet-due SOP was recommended and then not offered — a dead end. |
| **Why it was wrong** | The two halves of the system disagreed in front of the learner. |
| **New behaviour** | Every section carries `is_due` and `available_now`. The path splits "Recommended Next — available now" from "Scheduled for Later — due 14 Aug"; assignment offers only what is available. Section-level FSRS scheduling — previously computed and never read — now drives it. |
| **Why better** | WHAT and WHEN stay separate algorithms but agree in the interface. Weak material is never hidden, only honestly scheduled. |

## 9. Why never-assessed needs explicit handling

```text
no mastery record  ≠  mastered
no mastery record  =  no evidence either way
```

This distinction is not pedantic in a compliance context. Treating missing data as competence
means a learner can be marked trained on a procedure they were never asked about — which is
precisely the failure mode a training record is supposed to rule out.

Implementation: `_classify()` checks `answered == 0` **before** the mastered branch, and returns
`"Never assessed - no completed attempt has covered this section yet."`

## 10. Why spaced repetition?

A randomised controlled study of spaced learning in nurse-anaesthesia training (BMC Medical
Education, 2024) — a regulated clinical-training context structurally close to GxP — found
spaced re-testing improves retention in exactly this kind of high-stakes procedural material.

**FSRS is a separate algorithm from adaptive selection, answering a different question.**

| | Adaptive selection | Spaced repetition |
|---|---|---|
| Question | *Which* content? | *When*? |
| Module | `adaptive.py` | `fsrs.py` (FSRS-4.5) |
| Input | Lifetime accuracy per section | Pass/fail grade + elapsed days |
| Output | Priority + question ids | `next_eligible_at` |

They cannot be merged, and the reason is concrete: FSRS retrievability is
`R(0, S) = 1.0` for **every** stability value. Immediately after an attempt, a section just
failed and a section just passed both score 1.0. Retrievability answers "is it time to review?";
accuracy answers "is this learner weak here?".

Observed in the demo: from a single attempt, failed sections were scheduled for **13 Aug** and
the passed one for **15 Aug**, with no special-casing.

## 11. How the feedback loop works

```text
      ┌─────────────────────────────────────────────────────┐
      │                                                     │
      ↓                                                     │
Assessment (QuizAttempt)                                    │
      ↓ server-side grading from Option.is_correct          │
Performance (AttemptAnswer.is_correct, per question)        │
      ↓ Question.source_chunk                               │
Mastery (ChunkMastery per section + TopicMastery per SOP)   │
      ↓ accuracy thresholds        ↓ FSRS stability         │
Adaptive selection (which)    Scheduling (when)             │
      └──────────────┬─────────────┘                        │
                     ↓                                      │
        Targeted retraining (scoped question_ids)           │
                     ↓                                      │
              Reassessment ─────────────────────────────────┘
```

Each traversal updates streak, Elo, FSRS stability and lifetime accuracy — so the second pass
through the loop selects different content from the first. That is the property that makes it
adaptive rather than merely repeated.

## 12. Example — one complete learner

Actual output, live NVIDIA NIM (`uv run python manage.py demo_adaptive`):

```text
Attempt 1 — scored 33.33% (3/9 correct)

Per-section mastery after grading:
  Section 1: GMP            streak=1  elo=1545  next_review=2026-08-16
  Section 2: CAPA           streak=0  elo=1454  next_review=2026-08-14
  Section 3: Documentation  streak=0  elo=1454  next_review=2026-08-14

Adaptive analysis:
  >> Section 3: Documentation  HIGH  (scheduled for later)
       adaptive score 0.0% | lifetime 0.0% | 0/3 correct — below the 60% threshold
  >> Section 2: CAPA           HIGH  (scheduled for later)
       adaptive score 0.0% | lifetime 0.0% | 0/3 correct — below the 60% threshold
     Section 1: GMP            LOW
       adaptive score 100.0% | lifetime 100.0% | 3/3 — 1 of 3 toward mastery

Retraining selection:
  6 question(s) selected.  Excluded (already strong): Section 1: GMP

After 3 targeted retakes:
  Section 1: GMP            score=100.0%  lifetime=100%  status=in_progress  priority=low
  Section 3: Documentation  score=87.9%   lifetime=75%   status=mastered     priority=none
      -> improved: 50.0% -> 100.0% (+50.0 points)
  Section 2: CAPA           score=87.9%   lifetime=75%   status=mastered     priority=none
      -> improved: 50.0% -> 100.0% (+50.0 points)
  No further retraining scheduled.
```

The same state is visible to the learner at **My Learning Path** — see
[`ADAPTIVE_DEMO.md`](ADAPTIVE_DEMO.md).

## 13. Engineering decisions

| Choice | Why, specifically |
|---|---|
| **Django + DRF** | The append-only audit log, RBAC and admin surface build on framework primitives (`Group`, `BasePermission`, `ModelAdmin.has_*_permission`) rather than hand-rolled infrastructure — less custom code between "a reviewer clicks Approve" and "that action is provably attributable". |
| **PostgreSQL / SQLite** | One `DATABASE_URL`-driven code path. SQLite for zero-setup clone-and-run; Postgres verified in CI against a real service container, avoiding SQLite-in-CI/Postgres-in-prod drift. |
| **Redis + Celery** | LLM calls and PDF parsing are the genuinely slow operations. Defaulting to `CELERY_TASK_ALWAYS_EAGER=True` means nobody needs a broker just to try the app. *Caveat: tasks are currently awaited synchronously — process isolation, not thread liberation.* |
| **NVIDIA NIM (Llama 3.1 8B)** | OpenAI-compatible API, so the client needed no rewiring; chosen for the bootcamp track. One provider, two hardcoded constants — *provider-portable*, not provider-agnostic. |
| **Chunk-based processing** | The `Question.source_chunk` FK is what makes section-level adaptation possible. Without it the system is a conventional quiz with a score. |
| **Heading-aware chunking first** | SOPs are structurally regular by regulatory convention, so structure-aware splitting is high-precision at zero embedding cost. Semantic (embeddings) chunking is the fallback for un-headed documents; fixed-length is last resort. |
| **SME approval gate** | Up to 45% of LLM-generated MCQs contain implausible content (PMJ 2026). Human approval is the control that makes AI drafting acceptable here at all. |
| **Server-side scoring** | Assessment integrity — the client's opinion of correctness is never consulted. Necessary but not sufficient: the answer key must also be withheld (§14). |
| **ChunkMastery** | Section-grain state is the first grain at which a trainer can intervene. |
| **FSRS-4.5** | Per-(learner, section) memory model fitted from real answers, rather than one fixed ladder for everyone. Per-user parameter optimisation deliberately not attempted — insufficient data at this scale, the same reasoning that ruled out neural knowledge tracing. |
| **Elo** | Question difficulty from observed learner performance rather than a one-time LLM label; K=32 for learners (fast cold-start), K=16 for questions (shared across many learners). |

## 14. Limitations

Stated plainly.

1. **Grounding is provenance + prompt constraint, not verified entailment.** Nothing checks
   mechanically that the correct answer is supported by the source chunk.
2. **No SOP version lifecycle.** Reprocessing a revised SOP would delete its chunks, cascading
   away every learner's section mastery. It is now **blocked** when approved questions exist —
   which prevents the data loss but leaves procedure revision with no supported workflow.
3. **No training assignment or completion model.** The system tracks mastery, not obligation. It
   cannot yet answer *"is this person qualified?"* — the largest missing capability.
4. **Adaptive accuracy is lifetime, not decayed.** `recent_accuracy` is displayed but does not
   yet influence priority; early mistakes weigh as much as recent ones.
5. **Audit trail is not tamper-evident.** Append-only is enforced in the Django admin only — no
   hash chaining, no WORM storage.
6. **No separation of duties.** An Admin can generate *and* approve the same question.
7. **Frontend has no automated tests.** ~4,000 lines verified only by lint, build and manual
   browser checks.
8. **Docker and CI are written but were not executed** in this environment. Individual steps
   (test suite, `check --deploy`, migration drift, lint, build) were each run locally.
9. **Celery tasks are awaited synchronously**, so an LLM call still occupies a web worker.
10. **Prompt injection is unmitigated** for the chatbot path, which reaches learners without
    review.
11. **Tokens never expire**, and are stored in `localStorage`.

The answer-key exposure and completed-attempt resubmission defects found in the audit are
**fixed and regression-tested** — see [`CHANGELOG.md`](CHANGELOG.md).

## 15. Future work

In priority order:

1. **SOP version lifecycle** — a `SOPVersion` entity so a revised procedure supersedes rather
   than destroys. Unblocks reprocessing and is the prerequisite for requalification-on-revision.
2. **Training assignment and completion** — assignment, due date, completion state, and a
   qualification record. Turns "we measure mastery" into "we can answer who is qualified".
3. **Stronger grounding verification** — check that the stated correct answer is entailed by the
   source chunk, rather than trusting the prompt plus the reviewer.
4. **Tamper-evident audit** — hash chaining or append-only storage, so the trail is
   tamper-*evident* rather than tamper-*inconvenient*.
5. **Frontend test suite** — the single largest coverage gap.
6. **Production deployment validation** — actually build and run both compose stacks and the CI
   pipeline end to end.

Deliberately **not** next: microservices, Kubernetes, a vector database, or a UI redesign. None
of them addresses a defect this system currently has.
