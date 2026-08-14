# Reviewer Gap Analysis

> ## ⚠ STATUS UPDATE — this document is the AUDIT RECORD (pre-sprint)
>
> The findings below were true at audit time and are preserved so the ❌ rows stay meaningful.
> A review-readiness sprint has since closed several of them. Current state:
>
> | Audit finding | Status now |
> |---|---|
> | Priority ignores recent performance | **FIXED** — recency-weighted accuracy, half-life 5 |
> | 1/1 treated like 50/50 | **FIXED** — `MIN_EVIDENCE = 3` evidence gate |
> | Learning path vs auto-assigned disagree | **FIXED** — `is_due` / `available_now` reconciliation |
> | `ChunkMastery.next_eligible_at` never consumed | **FIXED** — section-level due-ness drives assignment |
> | No test that generation sets `source_chunk` | **FIXED** — `GenerationGroundingTests` |
> | Reworded duplicates pass | **FIXED** — lexical near-duplicate detection |
> | Submitted question ids unvalidated | **FIXED** — SOP + approved-status validation |
> | Adaptive selection advisory (no server-side quiz session) | **STILL OPEN** — `FUTURE_SCOPE.md` §1 |
> | No SOP version lifecycle | **STILL OPEN** — `FUTURE_SCOPE.md` §2 |
> | Difficulty does not affect priority | **STILL OPEN** — `FUTURE_SCOPE.md` §5 |
>
> Tests: 176 at audit time → **219 now**.
> See [`ADAPTIVE_IMPLEMENTATION_REPORT.md`](ADAPTIVE_IMPLEMENTATION_REPORT.md) and
> [`ADAPTIVE_ALGORITHM.md`](ADAPTIVE_ALGORITHM.md).

---


Written from the examiner's chair, not the developer's. Each question is answered with what the
repository actually shows, then the risk, then what a demanding reviewer would expect.

**Priority:** P0 = will be challenged and the answer is weak · P1 = will be challenged, answer
is adequate but incomplete · P2 = may be challenged · P3 = unlikely to come up.

---

## Architecture

**Q1–Q2. Why this architecture / why Django?**
*Evidence:* audit trail, RBAC and the admin surface build on `Group`, `BasePermission`,
`ModelAdmin.has_*_permission`. Append-only audit is ~20 lines instead of custom infrastructure.
*Risk:* none. *Priority:* **P3**.

**Q3. Why Celery?**
*Evidence:* three `@shared_task`s — SOP processing, generation, chat. *Risk:* **every task is
dispatched and immediately awaited** (`.delay(...).get(timeout=...)`), so the web thread blocks
anyway; the code comment claiming otherwise is wrong. *Expect:* either job-state polling or an
honest "process isolation only". *Fix:* say it plainly in the viva — it is defensible as
isolation, indefensible as async. *Priority:* **P1**.

**Q4. Why Redis?** Broker + result backend only; no caching or sessions. Accurate. **P3**.

**Q5. Why NVIDIA NIM?** OpenAI-compatible wire protocol, so the `openai` client works unchanged;
bootcamp track. **P3**.
*Trap:* do **not** say "provider-agnostic". One provider, hardcoded in two modules, zero matches
for any alternative. The honest word is *provider-portable*.

**Q6. Why not call the LLM from the frontend?** Key exposure; content must persist as `draft`
and pass the SME gate; dedup needs the existing question set; audit must attribute
server-side. Strong answer. **P3**.

---

## Quiz generation

**Q7. How do you know an AI-generated question is correct?**
*Evidence:* you don't — mechanically. Schema validation checks *shape*, not truth. The SME gate
is the only correctness control. *Risk:* if answered as "the prompt constrains it", the reviewer
will press and the answer collapses. *Expect:* candour. *Fix:* "We don't verify correctness
automatically; a qualified human does, and that's why the gate is mandatory." **P1**.

**Q8. How do you prevent hallucinations?**
*Evidence:* four layers — chunk-only prompt, schema validation, self-reported confidence
(excluded below 0.5 from mastery scoring), SME approval.
*Risk:* **only the fourth is a real control.** Prompt constraint is a request, not a guarantee.
*Fix:* never call provenance "hallucination prevention". Say: "mitigation at three layers,
prevention at one — the human." **P0 (framing).**

**Q9. How do you prevent duplicate questions?**
*Evidence:* normalised `(question_text, correct_answer)` signature, exact match.
*Risk:* trivially defeated by rewording — "What must be done before batch release?" vs "Prior to
batch release, what is required?" are distinct signatures, identical content. The demo generated
two Documentation questions that are near-paraphrases.
*Expect:* at least embedding-similarity or n-gram overlap. *Fix:* semantic dedup (see roadmap
P2-2). **P1**.

**Q10–Q11. Why chunking / why heading-aware?**
*Evidence:* SOPs are structurally regular by regulatory convention; heading-aware is
high-precision at zero embedding cost, and the heading becomes the section title learners see.
Architecturally the chunk is the adaptive unit. Strong answer. **P3**.

**Q12. What happens when chunking fails?**
*Evidence:* 3-tier cascade — heading → Max-Min semantic → fixed-length; the tier is recorded in
`chunking_strategy`. *Risk:* an SOP with no headings and no API key gets fixed-length chunks that
may straddle topics, making `ChunkMastery` for them semantically meaningless. Not surfaced
anywhere. *Fix:* warn reviewers when a SOP chunked as `fixed_length`. **P2**.

---

## SME workflow

**Q13. Why is an SME necessary?** Up to 45% of LLM-generated MCQs contain implausible content
(PMJ 2026, 71 studies); a wrong question can certify someone as competent in a procedure they
don't understand. Strong. **P3**.

**Q14. Can an AI-generated question reach a learner directly?**
*Evidence:* no — `status="draft"` on creation, and `get_queryset()` forces `status="approved"`
for non-reviewers server-side. Verified by test. Strong. **P3**.

**Q15. What does the SME actually verify?**
*Evidence:* they see question, options, answer key, explanation, SOP code, section title,
**the source passage**, chunking strategy, confidence, generation source, Elo.
*Risk:* nothing records *what* they checked or why they approved — only that they did.
*Expect:* a review comment or checklist. *Fix:* optional reviewer note on approve. **P2**.

**Q16–Q17. What happens after signing / can signed content be modified?**
*Evidence:* `403` on PATCH/PUT/DELETE for approved questions; signature bound to a SHA-256 hash
over text, explanation, difficulty and full option set; `signature_is_intact()` detects
out-of-band change. Verified by 14 tests. **Strong — lead with this.** **P3**.
*Follow-up they will ask:* "what if a bad question is already published?" → reject → edit →
re-approve. *Gap:* the superseded version is **not retained** — no revision history. **P1**.

**Q18. Can an SME regenerate a question?** ❌ Not implemented. Reject-and-regenerate-the-batch
is the only path. **P2**.

---

## Adaptive learning — the section that decides the review

**Q19. What makes this system adaptive?**
*Evidence:* content selected for the next assessment changes based on measured per-section
performance. Demonstrated: 33% attempt → 4 of 6 questions selected, the strong section excluded.
*Risk:* **the selection is advisory, not binding** — see Q31. *Priority:* **P0**.

**Q20. How is mastery calculated?**
*Evidence:* per attempt — confidence-filtered, Elo-weighted pass signal at ≥80%; streak of 3 ⇒
mastered; FSRS updates stability/difficulty. Priority separately from lifetime accuracy.
*Risk:* two different notions of "mastery" coexist (`mastery_status` streak-based, priority
accuracy-based) and can disagree. A section can be `in_progress` with 100% accuracy and LOW
priority. Explicable, but the reviewer must not catch you unaware. **P1**.

**Q21. Why chunk-level mastery?** An answer is evidence about a *passage*, not a document; the
section is the first grain at which intervention is possible. Strong. **P3**.

**Q22. Why not just use total quiz percentage?** A percentage identifies a person, not a
knowledge gap; it cannot tell you *what* to retrain. Strong. **P3**.

**Q23. How do you identify weak topics?** Lifetime accuracy per section: <60% HIGH, <80% MEDIUM.
**P1** — see Q26/Q27 for why the metric is challengeable.

**Q24. What happens when a learner has never been tested on a topic?**
*Evidence:* explicit `answered == 0` → HIGH, checked *before* the mastered branch. This was a
real bug (never-assessed sections were invisible), found and fixed. **Strong — tell that
story.** **P3**.

**Q25. Why these thresholds (60 / 80)?**
*Evidence:* 80 matches `PASS_THRESHOLD` used everywhere else. 60 is **arbitrary** — no
justification in code or literature.
*Risk:* "why 60?" has no good answer today. *Expect:* either a cited basis or an admission that
it is a tunable default. *Fix:* say "80 is the pass mark used system-wide; 60 is a chosen
default we would tune with real data." **P1**.

**Q26. How do you handle limited data? (1/1 vs 50/50)**
*Evidence — verified by execution:*
```text
OneAnswer      answered=1   acc=100.0%  priority=low
FiftyAnswers   answered=50  acc=100.0%  priority=low
```
**Identical treatment.** One lucky guess is treated as equivalent to fifty demonstrations.
*Risk:* **high** — this is a standard EdTech objection and the system has no answer.
*Expect:* a confidence interval (Wilson score), a minimum-sample rule, or a Bayesian prior.
*Fix:* minimum-sample gate or Wilson lower bound (roadmap P2-1). **P0**.

**Q27. How does recent performance influence adaptation?**
*Evidence — verified by execution:* a learner who answered 0/5 then 5/5:
```text
lifetime=50.0%   recent=100.0%   priority=HIGH
reason: "50.0% accuracy (5/10 correct) - below the 60% weakness threshold."
```
**It doesn't.** `recent_accuracy` is computed, returned by the API, and *rendered in the UI next
to the HIGH badge* — but `_classify()` never reads it.
*Risk:* **the worst finding in this audit.** The learner's own screen shows "Recent: 100%"
beside "Needs Review / Priority: HIGH". A reviewer reading that screen will ask why a learner at
100% recent accuracy is still being called weak, and the honest answer is "because we ignore
that number". It also means **a learner cannot escape a weak label promptly** — after 5 failures
they need 5+ consecutive successes just to cross 60%.
*Expect:* recency weighting, or at minimum not displaying a metric that contradicts the decision.
*Fix:* recency-weighted accuracy (roadmap **P0-2**). **P0**.

**Q28. Does difficulty affect adaptation?**
*Evidence — verified:* an easy question missed and a hard question missed both → HIGH, identical.
Elo weights the *pass signal* (mastery/FSRS) but **not the priority**.
*Risk:* moderate — defensible as a design choice, but it's inconsistent: difficulty matters for
"did you pass?" and not for "are you weak?". *Fix:* Elo-weight the accuracy used for priority.
**P1**.

**Q29. Does mastery decay over time?** No. Priority is lifetime accuracy; time enters only via
FSRS scheduling. A learner assessed a year ago with 90% still reads LOW priority forever.
*Fix:* couple retrievability into priority, or decay old answers. **P1**.

**Q30. What happens when a learner improves?** Mastery status and Elo move immediately; **priority
lags badly** (Q27). **P0**.

**Q31. When does a weak topic stop being recommended?** At ≥60% lifetime accuracy (→ MEDIUM),
≥80% (→ LOW), or 3 consecutive passing attempts (→ mastered, NONE). Coherent. **P2**.

**Q32. Can two learners receive different training?**
*Evidence:* yes — `ChunkMastery` is per (learner, chunk) and `analyse_sections` is per learner;
`test_path_is_scoped_to_the_requesting_learner` proves isolation.
*Risk:* no test demonstrates two learners receiving *different content*, only that they can't
see each other's. Easy to demo live, weak on paper. **P2**.

**Q33. Does the system adapt *within* a quiz?**
*Evidence:* no. The client snapshots the question set at start; adaptation happens between
attempts.
*Risk:* a reviewer expecting CAT (computerised adaptive testing) may call this "not really
adaptive". *Expect:* a clear articulation of *between-assessment* adaptation as a deliberate
choice — item-level CAT needs calibrated item parameters this data scale cannot support.
*Fix:* rehearse this answer; it is defensible but must be stated confidently. **P1**.

---

## Spaced repetition

**Q34. Why FSRS, and why separate from adaptive selection?**
*Evidence:* they answer different questions; `R(0,S)=1.0` for all S means retrievability cannot
distinguish a just-failed from a just-passed section. Genuinely strong, well-reasoned. **P3**.

**Q35. How is the next review date determined?** Point where modelled recall decays to 90%,
floored at 1 day. Verified: failed sections scheduled 2 days earlier than the passed one from a
single attempt. Strong. **P3**.

**Q36. What happens when priority and FSRS disagree?**
*Evidence — verified by execution:*
```text
CAPA 0% accuracy → HIGH, selected_for_retraining=True
TopicMastery due in +10 days → is_due=False

My Learning Path : "Recommended Next: CAPA"   "1 weak section(s)"
auto-assigned    : 0 assignments offered
```
**They are not reconciled.** The learner is told a section is urgent and given no way to act.
*Risk:* **P0 — this is a live-demo failure.** A reviewer clicking from the learning path to the
quiz screen sees a recommendation with no corresponding action.
*Expect:* either the learning path respects due-ness, or the UI explains "recommended, scheduled
for 23 Aug", or urgent sections override the schedule.
*Fix:* roadmap **P0-1** — surface `is_due` in the recommendation. **P0**.

---

## GxP

**Q37. Is this GxP compliant?** No — and say so first. Several 21 CFR Part 11-*style* technical
controls are implemented (password-verified approval bound to a content hash, attributed audit,
RBAC, immutable approved content). Missing: tamper-evident audit storage, separation of duties,
training completion records, any validation exercise. **Answer well and this is a strength.** **P3**.

**Q38. How is traceability maintained?** `Question.source_chunk` → `SOPChunk` → `SOPDocument`,
plus `chunking_strategy` and `generation_source`. *Gap:* `source_chunk` is `SET_NULL`, so
provenance is lost if a chunk is deleted. **P1**.

**Q39. How is training history preserved?** Attempts and answers are never overwritten;
resubmission returns `409`. *Gap:* no record of which questions were *offered* (D1), and no
question revision history — so a past attempt cannot be reconstructed as sat. **P1**.

**Q40. How do electronic signatures work?** Password re-verified at approval; hash binds the
signature to exact content; `signature_is_intact()` detects divergence. Strong. **P3**.

**Q41. What happens if the SOP changes?**
*Evidence:* reprocessing is **blocked** (`409`) when approved questions exist, because it would
delete chunks, cascade away `ChunkMastery` and orphan approved questions.
*Risk:* that is a guard, not a workflow — there is currently **no way to revise a procedure**.
*Expect:* SOP versioning. *Fix:* roadmap P1-1. **P1** (and the honest answer is "this is our
number-one gap").

**Q42. How do you prove which SOP version was used?**
*Evidence:* `SOPDocument` has `version`, unique with `sop_code`. An attempt links to the SOP.
*Risk:* if a question is regenerated or the SOP is replaced, a historical attempt points to
mutated state. No immutable snapshot. **P1**.

---

## Security

**Q43. Can a learner access the answer key?** No — `LearnerQuestionSerializer` omits
`is_correct` and `explanation`; `/api/quiz/options/` is reviewer-only; 11 tests including one
inspecting raw response bytes. Verified this session. **Strong.** **P3**.

**Q44. Can a learner submit twice?** No — atomic `UPDATE ... WHERE completed_at IS NULL`; `409`
and audited. 10 tests. **Strong.** **P3**.

**Q45. Can a learner see another learner's data?** No — attempts/answers scoped, dashboard
scoped, learning path scoped. **P3**.
*Caveat:* any authenticated user can read **all SOP chunk text** and download **any SOP file** —
no department or role scoping. Defensible ("SOPs are internal reference material") but say it
before they find it. **P2**.

**Q46. Can unauthorised users access SOP documents?** No — the unauthenticated `/media/` route
was removed; downloads go through an authenticated endpoint. **P3**.

---

## Limitations

**Q47. Biggest current limitations?** Lifetime-only accuracy ignoring improvement (Q27);
no sample-size confidence (Q26); adaptive selection advisory not binding (D1); priority/FSRS
unreconciled (Q36); no SOP versioning (Q41); no training assignment model.

**Q48. What would you implement next?** SOP versioning — it is the only gap that can silently
destroy learner data, and it is a prerequisite for the assignment model.

**Q49. Weakest part of the system?** The junction between the adaptive engine and the quiz the
learner actually receives. The engine is sound and well tested; its output is honoured by the
browser rather than enforced by the server, and it can contradict its own scheduler.

**Q50. With another month?** Recency-weighted, confidence-aware mastery; SOP versioning;
server-side quiz sessions so selection is binding and results reproducible.

---

## The five questions most likely to hurt

1. **Q27** — "Your screen says 100% recent accuracy and HIGH priority. Which is it?"
2. **Q36** — "You recommended CAPA; I clicked through and nothing was offered."
3. **Q26** — "One correct answer and fifty correct answers mean the same to you?"
4. **Q19/D1** — "If the browser ignores your question ids, what enforces the adaptation?"
5. **Q9** — "These two generated questions are the same question, reworded."

All five are fixable. Four of them are small. See `ADAPTIVE_NEXT_ROADMAP.md`.
