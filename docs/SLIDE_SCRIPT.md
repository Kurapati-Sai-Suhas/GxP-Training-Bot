# Slide-by-Slide Speaking Script

What to actually say, slide by slide. Written to be spoken, not read — say it in your own words
once you know the shape of it. The same text is in the PPTX speaker notes.

**Total: about 12 minutes at a comfortable pace.** Times are guidance, not a stopwatch.

---

## SLIDE 1 — Title · 40s

> "Good morning. My project is **GxP Training Bot** — an AI-assisted adaptive training and
> assessment platform for pharmaceutical standard operating procedures.
>
> In one sentence: the system turns controlled SOP content into SME-approved assessments, then
> uses each learner's question-level performance to identify weak **source sections** and aim
> future training at those weaknesses.
>
> The strip at the bottom is the pipeline — SOP, AI, SME, learner, adaptive training. The
> important thing about it is that it's also a trust boundary: **the AI drafts, the human
> decides**, and nothing reaches a learner without passing the SME gate."

*If asked what GxP means:* good-practice regulations for pharmaceutical manufacturing. My system
is GxP-**oriented** in its controls — it is not a compliant, validated system, and I'll be
precise about that difference throughout.

---

## SLIDE 2 — Problem Statement · 60s

> "SOP retraining today is mostly a slide deck and a generic quiz — same content for everyone.
>
> Here's the concrete gap. If an operator scores 60%, you know **they** struggled. You don't know
> **what** they struggled with. So the next round of training repeats everything, including the
> material they already knew.
>
> On top of that, if you bring an LLM in to generate the questions, you've introduced a quality
> risk into controlled training content — and in a regulated environment the recommendation has
> to be traceable and explainable to an auditor.
>
> So the question the whole project answers is this one:" *(read the blue box)* **"How can
> SOP-based training be automated while remaining human-validated, explainable, and adaptive to
> individual learner performance?"**

---

## SLIDE 3 — Aim and Objectives · 50s

> "The aim:" *(read the banner once, slowly)*
>
> "Six objectives. The first four build the content supply chain — ingestion, chunking,
> generation, and SME validation. The last two are the adaptive contribution — per-learner
> section-level modelling, and explainable scheduling.
>
> These map directly onto the seven Django apps in the codebase, so each objective is a real
> module, not an aspiration."

*If asked which part is your contribution rather than integration:* objectives 5 and 6 — the
section-level adaptive model and its reconciliation with spaced repetition are the parts I
designed rather than assembled.

---

## SLIDE 4 — Literature Review · 70s

**Do not read the table.**

> "The literature gave me three things: a way to model ability, a way to model forgetting, and a
> warning.
>
> **Pelánek 2016** is the one I implemented most directly — Elo applied to education, where a
> student answering an item is treated as a match, so learner skill and item difficulty are
> estimated jointly and online.
>
> **The DSR memory model** from Su and Ye's TKDE paper is the basis of my review scheduler.
>
> And **Kıyak 2026** is the warning, and it's the reason the SME gate is mandatory rather than
> optional. It's a systematic review of 71 studies across 24 countries. Reported error rates for
> AI-generated multiple-choice questions range from 0.3% up to 45% — and the authors conclude
> that current evidence does **not** support unsupervised use in summative assessment.
>
> I also want to point at Deep Knowledge Tracing — row two. I read it and **deliberately did not
> adopt it**, and I'll come back to why."

---

## SLIDE 5 — Existing vs Proposed · 50s

> "The left column is current practice, the right is what I built. Rather than walk both, let me
> land on the bar at the bottom, because that's the actual architectural idea:
>
> **The unit of adaptation is the SOP section, not the document.**
>
> Every generated question keeps a foreign key to the SOP chunk it came from. So when a learner
> answers, that answer is evidence about a **specific passage of a specific procedure** — not
> about the document as a whole. Without that one field, all you can compute is a percentage per
> document, which is exactly what existing systems already do."

---

## SLIDE 6 — System Architecture · 80s

> "Top lane, left to right, is the content pipeline: the SOP comes in, gets extracted and chunked,
> the AI engine drafts questions from it, and then it hits the SME review box — and that is a
> **hard gate**. Nothing passes it without a human approving under electronic signature. Only then
> does content enter the approved question store.
>
> From there it reaches the learner. And this is the split I want you to notice — the assessment
> feeds **two separate engines**.
>
> The left one, `adaptive.py`, answers **WHAT** to train — which sections is this learner weak on.
> The right one, `fsrs.py`, answers **WHEN** — when is the right moment to review. They're
> deliberately separate modules because they consume different signals, and I'll show you the
> mathematical reason they can't be merged.
>
> Both feed targeted retraining, then reassessment — and that bottom arrow is what makes this a
> **loop** rather than a report."

---

## SLIDE 7 — Methodology · 55s

> "Six stages. Stages one to four are a content supply chain; stages five and six are the adaptive
> loop.
>
> Two things worth emphasising. **Stage four is a gate, not a step** — content cannot skip it.
> And **stage five happens entirely server-side** — grading never touches the client, because if
> it did, every number downstream would be untrustworthy.
>
> On stage three: generation has three retries with backoff, then falls back to a deterministic
> offline generator whose correct answer is lifted verbatim from the SOP text. Lower quality — but
> it cannot hallucinate. So the workflow never blocks on the provider being reachable."

---

## SLIDE 8 — AI Question Generation · 70s

> "That's the chain — extraction, three-tier chunking, prompt construction, the model call,
> structured JSON back, validation, deduplication, and crucially the source-chunk linkage, before
> it becomes a draft awaiting SME approval.
>
> The two panels are the part I'd most like you to hold onto.
>
> **On the left, what chunking buys me:** the question is generated from specific SOP content
> rather than unrestricted model knowledge. One chunk per prompt. The model gets that passage and
> is instructed not to use outside knowledge.
>
> **On the right, what that does not buy me:** provenance does **not** guarantee factual
> correctness. Nothing mechanically verifies that the stated correct answer is actually entailed
> by the passage. This is provenance plus prompt constraint — **it is not verified entailment**.
> That's an honest gap, and it's on my roadmap as entailment verification."

*Also worth saying:* deduplication is lexical, not semantic — answer similarity at or above 0.8
**and** stem similarity at or above 0.4. It catches rewording; it does not catch two questions
that mean the same thing with no shared vocabulary.

---

## SLIDE 9 — SME Review and GxP-Oriented Control · 70s

> "Why is a human gate here at all, when the whole point was automation? Two reasons.
>
> **Regulatory:** in a GxP context, training content is controlled content. A qualified person has
> to be accountable for what an operator is assessed on. 'The model generated it' is not an answer
> an auditor accepts.
>
> **Technical:** the model has no ground truth. It produces plausible text conditioned on a
> passage; nothing in the pipeline can verify the answer is right. So the SME is the **only
> correctness control** in the system.
>
> Mechanically: approval re-verifies the reviewer's own password server-side — not just an active
> session. On success it stores a SHA-256 hash over the exact content approved, including which
> option is correct. After that the question is immutable; write methods return 403. And if
> something changes it by another route, a hash recomputation detects it.
>
> And to be clear about the red bar — **this is GxP-oriented design, not a compliance claim.**
> These are Part 11-*style* technical controls. Not implemented: tamper-evident audit storage,
> separation of duties, qualification records, and any formal validation exercise."

---

## SLIDE 10 — Adaptive Learning, Main Contribution · 100s ⭐ SLOW DOWN

> "This is the core of the project.
>
> For each section I take that learner's answer history, newest first, and compute an
> **exponentially recency-weighted accuracy** — weight is 0.5 to the power of i over 5, so an
> answer five back counts half as much as the newest one.
>
> Why weight it at all? Because **a flat lifetime average cannot represent change**. A learner who
> answered 0 out of 5 and then 5 out of 5 has plainly improved — but their lifetime average is
> 50%. The interface was showing 'Recent: 100%' right next to a HIGH priority badge. It
> contradicted itself. Weighted, that learner sits at 66.7% and moves to MEDIUM. Reversed — 5 out
> of 5 then 0 out of 5 — they drop to 33.3%, so decline is caught sooner too.
>
> Then the decision. Check order matters: **never assessed comes first and returns HIGH** —
> absence of evidence must never read as competence. Then mastered, which retires a section after
> three consecutive passing assessments. Then the evidence gate — below three answers you cannot
> **exclude** a section.
>
> And that gate is deliberately **asymmetric**: strong performance on a tiny sample is capped at
> MEDIUM, but weak performance on a tiny sample still reads HIGH. Because over-training costs a
> few extra questions, and under-training costs you an unqualified operator."

*If asked where 60, 80, 5 and 3 came from:* documented chosen defaults, not fitted values. 80%
matches the existing pass mark used elsewhere in the system. MIN_EVIDENCE is the first constant
I'd tune with real deployment data.

---

## SLIDE 11 — What Kind of Adaptive System Is This? · 80s ⭐ THE VIVA SLIDE

Say this almost verbatim. Then stop talking and let the black bar sit.

> "I want to be precise about what this is, because it would be easy to overclaim.
>
> **Overall it's a hybrid adaptive system, in four layers.**
>
> **Layer one** — the priority engine — is **rule-based**. Fixed thresholds, an evidence gate, a
> mastery state. Those numbers are chosen, not fitted.
>
> **Layer two** is the **statistical** signal those rules are applied to — the recency-weighted
> performance I just described. That is estimated from data.
>
> **Layer three is Elo** — genuine online parameter estimation of learner ability and question
> difficulty. It learns from every answer.
>
> **Layer four is FSRS** — a memory-state model that drives review scheduling.
>
> So, plainly: **the LLM does not determine adaptive priority. The adaptive engine is not a
> trained neural network. And it is adaptive because learner performance changes future content
> selection.**"

*If asked "so are you using machine learning?":* Not in the trained-model sense — nothing is
trained offline, there's no dataset and no loss function. What I have is **online parameter
estimation**, which is a different and weaker claim, and I'd rather make the weaker accurate one.

---

## SLIDE 12 — The Complete Adaptive Loop · 80s

> "This is the full loop. Learner answers, performance measurement, section mastery, adaptive
> priority, select the weak content, retrain on only that, reassess — and back to the top.
>
> The two panels underneath run **alongside** that loop, not inside it. Elo updates per answer and
> gives me ability and difficulty. FSRS takes the response plus elapsed time and gives me the next
> review date.
>
> And here's why these are two engines and not one." *(point at the grey bar)*
>
> "**FSRS retrievability R of 0 and S equals 1.0 for every stability value.** Immediately after an
> assessment, a section you just failed and a section you just passed **both** score 1.0. FSRS
> literally cannot tell them apart at the exact moment you most need it to.
>
> So accuracy selects the content, and FSRS schedules the timing. Each does what it's actually
> good at. In the interface that produces two distinct counts — sections needing training, and
> sections available now. An earlier version reported only the first, so it recommended a section
> and then the quiz screen had nothing to offer. I found that during audit and there's now a
> regression test for it."

---

## SLIDE 13 — Results · 70s

**Lead with the caveat, not the number.**

> "I want to frame this correctly first: this is a **verified demonstration result** from a
> controlled scenario. It is not a user study and I'm not claiming statistical significance.
>
> With that said — in the verified demo, the learner takes a nine-question quiz and scores 33%.
> The engine identifies two weak sections and one strong one. Retraining is built from **six
> questions, not nine** — the strong section is excluded entirely, so the learner is never dragged
> back through material they'd already demonstrated.
>
> After retraining, both weak sections go from **50% to 100% — fifty percentage points.** And that
> isn't asserted; it's computed from their stored answers by comparing the oldest half of the
> section's history against the newest half.
>
> One number worth pointing at: adaptive score 87.9% against lifetime 75%. **That gap is the
> recency weighting recognising improvement** — a flat average would still be dragging the early
> failures along.
>
> On the system side: 221 backend tests passing, zero failures, migrations and deploy checks
> clean, zero lint warnings, and the complete workflow verified end-to-end against live NVIDIA
> NIM."

*If asked "is this significant?":* No — one controlled demonstration with one learner. Establishing
efficacy would need an A/B trial against non-adaptive retraining, and that's a study, not a feature.

---

## SLIDE 14 — Security, Validation and Testing · 60s

**Don't read all ten. Pick three and say them well.**

> "Ten controls; let me give you three that matter most.
>
> **Grading is entirely server-side** — the client never decides anything.
>
> **The answer key never reaches the learner's browser.** Learners get a different serializer that
> omits the correct-option flag and the explanation, and I verified that on the raw response body,
> not just in the UI.
>
> **A completed attempt cannot be rewritten.** Submission uses an atomic compare-and-set, so a
> double submit returns 409 rather than overwriting a training record.
>
> One more that's recent: submitting the **same question multiple times** used to create multiple
> answer rows — enough to satisfy the minimum-evidence gate with a single question and inflate
> that section's accuracy. It now returns 400 with zero writes. I found that in my own audit.
>
> 221 tests, zero failures. But — tests validate system **behaviour**; they do not establish
> regulatory compliance, and I want to be clear about that distinction."

---

## SLIDE 15 — Limitations and Future Scope · 80s ⭐ VOLUNTEER LIMITATION 1

> "I'll start with the biggest one rather than wait to be asked.
>
> **The exact offered question set is not yet persisted server-side.** Validation confirms every
> submitted question belongs to the learner's permitted SOP, role and approved pool — but a
> modified client could submit a different eligible question, or omit questions. And because the
> score is computed over submitted answers, omitting inflates it.
>
> It's bounded — same SOP, approved content only, own attempts, fully audited — and the real
> client always submits the full offered set. But I'm not going to pretend that's enforcement.
> **The adaptive decision is computed and validated server-side; it is not yet enforced.**
>
> The fix is one join table, `QuizAttemptQuestion`. It's first on my roadmap. I didn't ship a
> migration on the highest-consequence code path two days before this review, and I'd make that
> call again.
>
> The roadmap ordering is the argument: **integrity first, then measurement quality, then
> intelligence.** Enforcement, then SOP versioning — which is the only gap that can silently
> destroy learner data — then content lineage. Agentic orchestration is last, deliberately,
> because it's the most fashionable item and the least load-bearing. **And agentic AI never
> replaces SME approval or the deterministic compliance controls.**"

---

## SLIDE 16 — Conclusion · 50s

Take the seven points fast. Then pause, and deliver the four closing lines slowly.

> "So — the system converts SOPs into structured training content, uses AI for scalable question
> generation, keeps a human SME in control, personalises retraining from measured performance,
> separates what to learn from when to review, explains every recommendation, and demonstrated
> measurable improvement.
>
> If you remember four things:
>
> **AI generates the content.
> SMEs control the content.
> Learner data drives adaptation.
> FSRS controls review timing.**
>
> Thank you — I'm happy to take questions."

---

## SLIDE 17 — References

Only bring this up if asked.

> "References 3 and 6 are the two actually implemented in code — Pelánek's Elo formulation and
> the DSR memory model. Reference 7 is the evidence base for why SME review is mandatory."

---

# TRANSITIONS — the glue between slides

| From → To | Say |
|---|---|
| 2 → 3 | "So that's the problem. Here's what I set out to build." |
| 5 → 6 | "Let me show you how that's actually assembled." |
| 6 → 7 | "Now the same thing as a process, stage by stage." |
| 9 → 10 | "That's the content side handled. Now the part I'd call my actual contribution." |
| 10 → 11 | "Before I show the loop, let me be precise about what kind of system this is." |
| 11 → 12 | "Here's all of that in one picture." |
| 12 → 13 | "So does it work? Here's what I measured." |
| 14 → 15 | "And here's what it doesn't do yet." |

---

# IF YOU HAVE ONLY 5 MINUTES

Slides **1 → 2 → 6 → 10 → 12 → 13 → 15 → 16**. Skip literature, methodology, generation,
security. You lose depth but keep the argument intact.
