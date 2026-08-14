# Future Scope

Work deliberately **not** done, in three tiers. Each entry states what, why it matters, how it
would be built, the expected benefit — and why it was excluded before the review.

The common thread across the immediate tier: each needs a database migration over live learner
data. Two days before a project review, a migration that goes wrong costs the demo. Every item
here was weighed against *"does this defend the core claim?"* — and none of them does, because the
core claim is already demonstrable and tested.

---

# TIER 1 — IMMEDIATE FUTURE

The next sprint. All four are well-understood, scoped, and blocked only by risk appetite.

## 1.1 Server-side offered-question binding *(the top priority)*

**What.** Persist the exact question set offered when a `QuizAttempt` is created, and validate
submissions against it.

**Why.** Today the server computes which questions the adaptive engine selected but does not
record them. Validation confirms a submitted question belongs to the attempt's SOP, matches the
role, and is approved — but not that it was in the selection. A modified client can therefore
answer an approved question the engine excluded, and that advances mastery for the section.
Verified: a section at `priority=none` had its streak go 3→4 and Elo 1544→1556.

It also means results are not reproducible: nothing records what was *offered*, so a past attempt
cannot be reconstructed as it was sat.

**How.**
```python
class QuizAttemptQuestion(models.Model):
    attempt  = FK(QuizAttempt, related_name="offered_questions")
    question = FK(Question)
    position = PositiveSmallIntegerField()
    class Meta: unique_together = ("attempt", "question")
```
Written at attempt creation; `submit()` rejects anything not in `attempt.offered_questions`. The
frontend's "Start Quiz" path also stops silently bypassing adaptation.

**Benefit.** Converts the adaptive decision from **advisory to enforced** — closing the single
strongest reviewer objection — and makes training records reproducible.

**Complexity.** Medium: one model, one migration, changes to the submission path (which also
carries the atomic single-submission claim and the mastery cascade) plus a frontend change.

**Why not now.** Migration risk on the highest-consequence code path. The low-risk half —
validating SOP membership and approval status — was shipped instead.

## 1.2 SOP version lifecycle

**What.** A `SOPVersion` entity; chunks, questions and mastery bind to a version; a new version
supersedes rather than replaces.

**Why.** Reprocessing a revised SOP would delete its chunks, cascade away every learner's
`ChunkMastery`, and orphan approved questions from their source text. It is currently **blocked**
(`409`) when approved questions exist — a guard, not a workflow. **Revising a procedure has no
supported path**, which in a regulated environment is an operational limit, not a theoretical one.
It is also the only remaining gap that can silently destroy learner data.

**How.** Insert `SOPVersion` between `SOPDocument` and `SOPChunk`. Questions and `ChunkMastery`
point at a version. Publishing v2 leaves v1's chunks, questions and mastery intact; a policy
decides whether a revision invalidates prior mastery (requalification trigger).

**Benefit.** Makes historical training records reproducible — "Rohit mastered the gowning section"
stops being ambiguous after a revision.

**Complexity.** High — new entity plus a data migration over live rows.

**Why not now.** Highest-risk item in the backlog; explicitly excluded by the audit.

## 1.3 Question revision history

**What.** Immutable `QuestionVersion` rows; the e-signature binds to a version rather than
mutating the row.

**Why.** Reject → edit → re-approve overwrites the superseded wording. A past attempt cannot be
reconstructed as the learner saw it, which weakens the training record.

**How.** On approval, snapshot the content into a version row carrying the content hash, approver
and timestamp. Editing a rejected question creates a new draft version.

**Benefit.** Full content lineage — directly strengthens the GxP story.

**Complexity.** Medium — migration on the e-signature path.

**Why not now.** That path is the strongest, best-tested part of the system (14 tests). Not worth
destabilising for a review.

## 1.4 Difficulty-aware priority

**What.** Weight each answer by the question's live Elo when computing the recency-weighted
accuracy used for selection.

**Why.** A genuine internal inconsistency: Elo already weights the *pass signal* that drives
mastery and FSRS, but not the accuracy that drives *priority*. So an easy question missed and a
hard question missed contribute identically to whether a section is called weak. Verified.

**How.** Multiply each answer's recency weight by its `_elo_weight(question)` (1.0–2.0) inside
`weighted_accuracy`.

**Benefit.** Removes the inconsistency; a learner failing only the hard questions in a section is
distinguished from one failing everything.

**Complexity.** Low — roughly two hours plus tests.

**Why not now.** The metric was just changed to recency weighting. Changing it twice in one sprint
means two behaviour shifts to validate simultaneously. First candidate for the next sprint.

---

# TIER 2 — ADVANCED FUTURE

Valuable, larger, and none of them corrects a defect.

## 2.1 Semantic / embedding duplicate detection

**What.** Embedding cosine similarity in addition to the current lexical check.

**Why.** Lexical near-duplicate detection (answer ≥ 0.8, stem ≥ 0.4) catches rewording. It does
**not** catch semantic duplicates sharing no vocabulary — *"How soon must a deviation be
recorded?"* versus *"What is the maximum permitted delay for deviation documentation?"*

**How.** Embed each draft's question + correct answer; compare against stored embeddings for that
SOP; flag above a tuned threshold.

**Benefit.** Cleaner review queue; less reviewer fatigue.

**Complexity.** Medium — needs an embedding store and threshold tuning.

**Why not now.** Puts an embedding call in every generation run, adding latency and cost, and makes
de-duplication depend on the provider being reachable — which the pipeline is deliberately designed
not to require.

## 2.2 Entailment verification *(the most technically impressive item here)*

**What.** Mechanically check that the stated correct answer is *supported by* its source chunk.

**Why.** Grounding today is prompt constraint plus stored provenance. Nothing verifies the answer
follows from the passage — the SME is the only correctness control. This is the honest gap behind
*"how do you know the generated question is correct?"*

**How.** An NLI model scoring entailment of (chunk → correct answer), or a second LLM call acting
as judge, surfaced to the reviewer as a confidence signal alongside the model's self-report.

**Benefit.** Upgrades grounding from *provenance* to *verification*. Would not replace the SME
gate — it would inform it, and let reviewers triage low-entailment questions first.

**Complexity.** High — model selection, latency, cost, calibration, and a false-positive policy.

**Why not now.** Research-scale, and the SME gate already covers the risk.

## 2.3 Mastery decay over elapsed time

**What.** Let calendar time, not just answer position, erode a section's standing.

**Why.** Decay is currently by *position*: ten answers ago weighs the same whether it was yesterday
or last year. A section assessed a year ago at 90% still reads LOW priority indefinitely. Time
enters only through FSRS scheduling.

**How.** Either fold FSRS retrievability into priority once a section is overdue, or decay answer
weights by elapsed days rather than index.

**Benefit.** Stale competence stops looking like current competence — which matters in a
requalification context.

**Complexity.** Low–medium, but it interacts with recency weighting and must be designed together
rather than bolted on.

## 2.4 Prerequisite graphs

**What.** Model that some sections underpin others — e.g. Documentation Practices before CAPA.

**Why.** Retraining currently sequences by weakness alone. If a learner is weak on both a
foundational and a dependent section, teaching the dependent one first wastes the attempt.

**How.** An explicit edge list authored by the SME, or inferred from co-occurrence of errors.
Selection then orders by topological depth within a priority band.

**Benefit.** More pedagogically sensible sequencing.

**Complexity.** Medium — the graph has to come from somewhere, and authoring it is real work.

## 2.5 Cross-SOP concept modelling

**What.** Recognise that "deviation reporting" in two different SOPs is one concept, so mastery
transfers.

**Why.** Mastery is currently per (learner, chunk). A learner who has demonstrated a concept in
SOP-211 is re-tested on it from scratch in SOP-217.

**How.** Cluster chunks across SOPs by embedding similarity (LECTOR-style); maintain concept-level
mastery alongside chunk-level.

**Benefit.** Less redundant retraining across a procedure library; the value grows with the number
of SOPs.

**Complexity.** High — clustering quality, concept drift, and a second mastery grain to keep
coherent.

---

# TIER 3 — RESEARCH LEVEL

Interesting, defensible to decline, and worth being able to discuss.

## 3.1 Item Response Theory

**What.** Model each question with difficulty and discrimination parameters and each learner with
a latent ability, fitted from response data.

**Why.** Elo is a cheap online approximation of the same idea. IRT is the principled version and
would give calibrated item parameters.

**How.** Fit a 2PL model over logged responses; replace or inform the Elo ratings.

**Benefit.** Better difficulty estimates and a proper ability scale.

**Complexity.** High — and it needs far more responses per item than this deployment produces.

## 3.2 Computerised Adaptive Testing (within-quiz adaptation)

**What.** Choose each question from the previous answer, rather than fixing the set upfront.

**Why.** A reviewer may expect this when they hear "adaptive". The current design adapts *between*
assessments.

**How.** Requires 3.1 first — CAT selects the item that maximises information at the learner's
current ability estimate, which needs calibrated parameters.

**Benefit.** Shorter, more precise assessments.

**Complexity.** High, and gated on IRT. **Declining this is the defensible position at this data
scale** — say so plainly rather than treating it as a gap.

## 3.3 Per-user FSRS parameter optimisation

**What.** Fit FSRS's 17 weights per learner instead of using the published defaults.

**Why.** FSRS supports it, and per-learner memory models would in principle schedule better.

**How.** FSRS's own optimiser over each learner's review log.

**Benefit.** Marginal at realistic training volumes.

**Complexity.** Medium, but data-starved.

**Why declined.** It needs a volume of logged reviews a training deployment this size will never
produce — the same data-scale reasoning that ruled out neural knowledge tracing. The published
defaults were fit on hundreds of millions of reviews. **This is a decision, not a gap.**

---

# ALSO OPEN (outside the adaptive core)

| Item | Why it matters | Complexity |
|---|---|---|
| **Training assignment & qualification model** | The system tracks *mastery*, not *obligation*. It cannot answer "is this person qualified for this role?" — arguably the question a regulated employer most needs. Largest missing **product** capability | High |
| **Tamper-evident audit storage** | Append-only is enforced in the Django admin only — no hash chaining, no WORM. Any code path can modify audit rows | Medium |
| **Separation of duties** | An Admin can generate *and* approve the same question | Low |
| **Reviewer rationale on approval** | Audit records *that* they approved, not *what they checked* | Low |
| **Single-question regenerate** | Reviewers expect it; only whole-batch regeneration exists | Medium |
| **Frontend test suite** | ~4,000 lines verified only by lint, build and manual checks | Medium |
| **Celery job-state polling** | Tasks are awaited synchronously; process isolation without thread liberation | Medium |
| **Automated question quality scoring** | No relevance / distractor-plausibility check beyond the SME's eye | Medium |
| **PDF/DOCX positive-path tests** | Only `.txt` and a corrupt-PDF failure case are covered | Low |
| **Pagination** | No list endpoint is paginated; the audit log returns the entire trail | Low |

---

# IF EXACTLY ONE MORE SPRINT WERE AVAILABLE

1. **Server-side offered-question binding** (1.1) — converts the adaptive decision from advisory
   to enforced. The single highest-value change in this document.
2. **Difficulty-aware priority** (1.4) — two hours, removes a real inconsistency.
3. **SOP version lifecycle** (1.2) — the only gap that can silently destroy learner data; needs a
   sprint of its own.

In that order. The first two are safe. The third is not, and should not be rushed.
