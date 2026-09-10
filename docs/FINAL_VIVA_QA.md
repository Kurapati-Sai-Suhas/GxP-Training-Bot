# FINAL VIVA Q&A — 30 hardest questions

Format: **BEST ANSWER** → *likely follow-up* → **follow-up answer** → ❌ **red flag to avoid**.

---

### 1. Is your adaptive learning rule-based or machine learning?
**Both, and I'll be precise. The priority decision is rule-based — thresholds at 60% and 80%, a
minimum-evidence gate of three, a mastery streak of three. Those I chose; they weren't fitted. But
they're applied to a statistic estimated from data: an exponentially recency-weighted accuracy,
half-life five answers. And two components estimate parameters online — Elo for ability and item
difficulty, FSRS for memory state. So it's a hybrid. It's adaptive; it is not machine learning,
because nothing is trained offline: no dataset, no loss function, no gradient.**
→ *"Then why call it adaptive at all?"*
→ **Because measured performance changes future content selection. The learner scored 33% and got six questions back instead of nine — the section they'd mastered was excluded.**
❌ Never say "yes, it uses ML" and then be unable to name the training data.

### 2. Is this Knowledge Tracing?
**No, and I won't claim it. Knowledge tracing maintains a latent knowledge state and predicts the
probability of answering the next item correctly. Mine classifies an observed statistic against
thresholds. There's no latent variable and no prediction. To become true KT it would need a
knowledge-state model emitting P(correct) per concept, a concept layer above chunks, difficulty
snapshotted at answer time, and far more data.**
→ *"So your project has no KT at all?"*
→ **Correct — and I documented exactly what would be required, which is a more useful contribution than a model I couldn't validate.**
❌ Never call section mastery "knowledge tracing".

### 3. Why not DKT?
**Data scale. DKT was fitted on roughly half a million interactions. My entire database has 42, and
most are scripted demo output. It would fit noise and I'd have no way to detect it. There's also a
structural blocker: my event log only gained per-answer timestamps this week, so before that there
was nothing temporal to model.**
→ *"How many would you need?"*
→ **For DKT, 10⁵–10⁶. For BKT, hundreds per skill. I'd reach BKT territory long before DKT.**
❌ Don't say "DKT is too complex" — it's a data argument, not a difficulty argument.

### 4. Why not BKT?
**Same reason, smaller gap. BKT needs hundreds of observations per skill; I have roughly 1.6 per
chunk. BKT would also need a concept layer — my unit is the physical chunk, so two chunks covering
one concept are tracked independently. It's the first model I'd implement once data accumulates.**
❌ Don't claim BKT is implemented anywhere.

### 5. Why Elo?
**Because difficulty shouldn't be a label someone typed once. Every answer is a match between the
learner's ability and the item's difficulty, both starting at 1500. Two update rates on purpose:
K=32 for the learner, because ability must adapt from few answers; K=16 for the question, because
its rating is shared across everyone. Pelánek 2016 is the reference. It's the cheap online
approximation of IRT.**
→ *"Why not IRT then?"*
→ **IRT needs far more responses per item to calibrate. Elo gets a usable estimate from a handful.**

### 6. Why FSRS?
**It replaced a fixed Leitner ladder — 1/2/4/7/14/30 days — which treated all material and all
learners identically. FSRS models memory with difficulty, stability and retrievability, and
schedules the next review where predicted recall drops to 90%. I use the published 17 weights, not
per-user fitted ones, because fitting them needs a volume of reviews this deployment will never
produce.**
→ *"Only two of four grades?"*
→ **Yes — my signal is binary pass/fail. I'd rather use the model honestly on the signal I have than invent a four-point scale.**

### 7. Why separate adaptive selection from FSRS?
**Because retrievability is 1.0 for every stability value at zero elapsed time. Immediately after
an assessment — exactly when the retraining decision is made — a section you just failed and one
you just passed both score 1.0. FSRS mathematically cannot distinguish them at that moment. So
accuracy selects the content and FSRS schedules the timing.**
→ *"Show me that in the code."*
→ **`fsrs.py`, `retrievability()`: `(1 + (19/81)·t/S)^(−0.5)`. At t=0 the bracket is 1, so the result is 1 for any S.**
*(This is your single strongest technical moment — deliver it slowly.)*

### 8. How is mastery calculated?
**Two signals in parallel. Per attempt, a confidence-filtered, Elo-weighted score against an 80%
pass mark decides pass or fail. Three consecutive passes mark a section mastered and retire it
from retraining; one failure resets the streak to zero. Separately, the adaptive engine computes a
recency-weighted accuracy per section for priority.**

### 9. Why recency weighting?
**Because a flat average can't represent change. A learner who went 0-of-5 then 5-of-5 has clearly
improved, but their lifetime average is still 50% — so the interface displayed "Recent: 100%" next
to a HIGH priority badge and contradicted itself. Weighted, they sit at 66.7% and move a band.
Reversed, they drop to 33.3%, so decline is caught sooner too.**
→ *"Why half-life five?"*
→ **It matches the "recent accuracy" window already shown in the UI, so "recent" means one half-life everywhere. It's a chosen default, not a fitted value.**

### 10. What is MIN_EVIDENCE?
**Three answers minimum before a section can be *excluded*. It's deliberately asymmetric: strong
performance on one or two answers is capped at MEDIUM and can't exclude anything, but weak
performance on one or two still reads HIGH. Over-training costs a few questions; under-training
could leave someone unqualified.**

### 11. What happens with insufficient data?
**The evidence gate handles it explicitly, and never-assessed sections are checked first and
return HIGH — absence of evidence must never read as competence. That was a real bug I fixed:
untested sections were invisible, so a learner could be retrained forever on the one section
they'd been tested on.**

### 12. Can the browser bypass your adaptive selection?
**Not any more, and this is the fix I'm most pleased with. The server now records exactly which
questions were offered, in order, when the attempt is created. Submission must match that set
exactly — same members, same count, no duplicates. Partial, substituted, extra, duplicated and
empty submissions all return 400 with zero database writes.**
→ *"What could it do before?"*
→ **Answer only the questions it knew. Two of six correct scored 100%, because the score was computed over submitted answers. Now the denominator is the offered set: two correct and four unanswered of six is 33.33%.**
❌ Don't claim it was always enforced.

### 13. How do you prevent cheating generally?
**Layered. Grading is entirely server-side. The answer key never reaches the learner's browser —
different serialisers by role, and I verified it on the raw response body, not just the screen. A
completed attempt can't be resubmitted: an atomic compare-and-set means the second submission gets
409. Foreign SOPs, unapproved drafts and other learners' attempts are all rejected.**

### 14. How do you prevent LLM hallucinations?
**I don't prevent them — I mitigate at three layers and prevent at one. Constrained generation,
one section per prompt, no outside knowledge. Provenance: the reviewer sees the exact source
passage. A deterministic fallback whose answer is lifted verbatim from the SOP. The prevention
layer is the SME. A systematic review of 71 studies reports error rates from 0.3% up to 45% in
AI-generated MCQs and concludes they shouldn't be used unsupervised — which is why the gate is
mandatory.**
→ *"So a wrong question could still be approved?"*
→ **Yes. Human review is the control, and it's fallible. Automated entailment checking is the honest gap.**

### 15. How is SME approval enforced?
**Non-reviewer querysets force `status="approved"`, so drafts are invisible to learners regardless
of what the client asks for. Approval requires the reviewer to re-enter their own password,
checked server-side with `check_password`. On success a SHA-256 hash of the exact approved content
is stored, and the question becomes immutable — PATCH, PUT and DELETE all return 403.**

### 16. How is question provenance maintained?
**Every generated question stores a foreign key to the `SOPChunk` it was drafted from, and the
reviewer UI displays that passage. It's `SET_NULL`, so if a chunk is removed the question survives
in an explicit unlinked bucket rather than disappearing from analysis.**
→ *"Does provenance prove correctness?"*
→ **No. It's provenance plus a prompt constraint, not verified entailment.**

### 17. Why NVIDIA NIM rather than OpenAI or Gemini?
**Free tier suitable for a student project, an OpenAI-compatible endpoint so the standard SDK
works, and access to open models — Llama 3.1 8B for generation and an embedding model for
chunking. It's provider-**portable**, not provider-agnostic: switching means changing two
constants in two files, but they're currently hard-coded.**
❌ Don't say "provider-agnostic".

### 18. Why no vector database?
**Because I measured the problem before reaching for the tool. Retrieval operates over a single
SOP's chunks — three in the demo, twenty across the corpus. An index over twenty items is
overhead. The chatbot uses lexical word-overlap retrieval today, and the defensible next step is
to build a retrieval gold set and measure that baseline before adding pgvector.**
❌ Never call it "vector RAG".

### 19. So you have no embeddings at all?
**One place only: semantic chunking, and only when heading detection fails. They're computed in
memory and discarded — nothing is persisted, so there's nothing to index. The demo SOP has
numbered headings, so that path didn't even run for it.**

### 20. What happens if NVIDIA NIM fails?
**Three retries with backoff, then a deterministic offline generator that lifts the correct answer
verbatim from the SOP text — lower quality, but it cannot hallucinate. Failures are classified
into nine categories in the logs, and generated questions are tagged with their source so a
reviewer can see which path produced them. The workflow never blocks.**

### 21. Where is the novelty?
**Three things. Section-level adaptation grounded in a persisted provenance link, so an answer is
evidence about a passage rather than a document. The deliberate separation of *what to train* from
*when to review*, with a mathematical justification. And an explainability contract — every
recommendation carries the measured accuracy, the response count and the threshold that fired.
The individual algorithms are established; the composition and the compliance framing are mine.**
❌ Don't claim Elo or FSRS as your contribution.

### 22. What makes this better than a normal LMS?
**An LMS records that someone scored 60%. This localises the gap to a section, excludes what
they've demonstrated, and can state the evidence for every recommendation. And it does that inside
a controlled-content workflow where a qualified human signs off every question.**

### 23. How do you know the learner actually improved?
**I compare the oldest half of that section's stored answers with the newest half — at least four
answers. In the demo, 50% to 100% on both weak sections. But that's a within-learner pre/post
measurement, not a controlled experiment. No control group, so I can show they improved on the
material selected — not that they wouldn't have improved by re-reading the whole SOP.**
❌ Never call it statistically significant.

### 24. Is your demo data real?
**No. It's a scripted scenario generated by a management command, and I'd rather say so. The
outcomes are decided by the script to demonstrate the loop. It's a verified demonstration of the
mechanism, not evidence of educational effectiveness.**

### 25. Is this GxP compliant?
**No, and I wouldn't claim it. GxP-oriented, with Part 11-style technical controls:
password-verified approval bound to a content hash, attributed audit trail, RBAC, immutable
approved content. Missing: tamper-evident audit storage, separation of duties, a qualification
record, and any formal validation exercise.**

### 26. What happens when an SOP changes?
**Today it's blocked — reprocessing a document with approved questions returns 409. That's a
guard, not a workflow: reprocessing would delete the chunks, cascade away every learner's section
mastery, and orphan approved questions. So revising a procedure has no supported path, which in a
regulated setting is a genuine operational limit. A `SOPVersion` entity is the fix and it's high
on my roadmap.**

### 27. Isn't this just a lot of if-else?
**The final classification is if-else — four branches — and I'd defend that as correct for a
compliance decision, because a threshold and a count can be audited. The interesting part is what's
being branched on: a recency-weighted accuracy over an ordered history, per source section, gated
on evidence sufficiency, reconciled against a spaced-repetition schedule, with two online-estimated
rating systems feeding the mastery signal. The last step is if-else; everything that makes it
correct happens before it.**

### 28. What's your biggest limitation?
**Until this week, the offered question set wasn't persisted, so the adaptive decision was
computed and validated but not *enforced*. I closed that. What remains is SOP versioning — no
supported revision path — and the absence of a real knowledge-tracing model, which is blocked by
data rather than by effort.**

### 29. What would you implement next?
**In order: an offline evaluation harness with temporal splits and calibration metrics, so any
future model can be compared against the current engine on identical data — without that, "better"
is an opinion. Then SOP versioning, because it's the only gap that can silently destroy learner
data. Then difficulty-aware priority, then entailment verification. Integrity first, then
measurement quality, then intelligence.**

### 30. How much of this is actually AI?
**The generation half is genuinely AI — an LLM drafting assessment items from controlled source
text, plus an embedding model for semantic chunking. Two adaptive components are
parameter-estimating models; Elo is a latent-trait model, the same family as IRT. The decision
layer is deliberately not AI, because in a regulated setting a recommendation has to survive being
questioned, and "below the 60% threshold on three answers" survives where a softmax output
doesn't. The harder engineering problem here isn't which model — it's how you put a probabilistic
component inside a controlled workflow without displacing human accountability.**
