# Project Review — 40 Likely Questions

Answers written to be **spoken**, not read aloud from a page. Each has the follow-up a sharp
reviewer will actually ask next.

🟢 Strong · 🟡 Defensible, explain carefully · 🔴 Real weakness — be first to name it

---

## A. Project motivation

**A1. What problem does this solve?** 🟢
> "Pharma companies retrain staff on SOPs constantly, and today a QA trainer hand-writes the quiz
> questions from a 20-page document for every job role, every time it changes. And everyone gets
> the same quiz regardless of what they got wrong — a score tells you who's struggling, not what
> with."

*Follow-up: "Couldn't you just buy an LMS?"* → "An LMS delivers and records training. It doesn't
author questions from your procedures, and it doesn't know which *section* of a procedure a
learner failed. That attribution is the whole point here."

**A2. Who are the users?** 🟢
> "Three roles. A Training/QA Admin uploads SOPs and triggers generation. An SME reviewer approves
> questions under an electronic signature. Learners take quizzes. All enforced server-side, not
> just hidden in the UI."

*Follow-up: "Show me a learner can't approve."* → `test_approve_rejected_for_plain_learner`, 403.

**A3. Why is this a hard problem?** 🟢
> "The hard part isn't generating questions — an LLM does that in one call. It's that you can't
> trust AI-generated compliance content, so you need a human gate; and once you have per-question
> results you have to decide what to do with them. Most systems stop at the score."

**A4. What's the single most important design decision?** 🟢
> "Keeping a foreign key from every question back to the SOP chunk it was generated from. That one
> link is what turns an answer into evidence about a *passage* instead of about a document. Remove
> it and this is a conventional quiz app."

---

## B. Architecture

**B5. Why Django?** 🟢
> "The two hardest requirements were an attributable audit trail and role-based access control.
> Django gives me groups, a permission framework, and an admin whose add/change/delete can be
> denied outright — so the append-only audit log is about twenty lines instead of custom
> infrastructure."

*Follow-up: "Why not FastAPI?"* → "I'd have built auth, admin, ORM and migrations myself. That's
engineering judgement, not a recorded design decision."

**B6. Why Celery and Redis?** 🟡
> "Two operations are genuinely slow — PDF parsing and LLM calls, up to two minutes. Running them
> in the web process risks one bad document taking down a worker. Redis is just the broker."

*Follow-up: "So it's asynchronous?"* → **Be honest.** "Not really. Every task is dispatched and
then immediately awaited with `.get(timeout=...)`, so the request still blocks. I get process
isolation, not request-thread liberation. Proper job-state polling is on my list — and gunicorn's
timeout is set to 180 seconds specifically to accommodate the current design."

**B7. Why NVIDIA NIM?** 🟢
> "It serves Llama 3.1 8B behind an OpenAI-compatible API, so the standard client works unchanged
> — the base URL and the model name are the only two constants that differ. It was also the
> platform for the bootcamp track this was built for."

*Follow-up: "So it's provider-agnostic?"* → **Trap.** "No — I wouldn't claim that. One provider,
hardcoded in two modules. The honest word is *provider-portable*: swapping means editing two
constants. There's no registry or strategy interface."

**B8. Why not call the LLM from the frontend?** 🟢
> "Four reasons: the API key would be exposed to every client; generated content has to persist as
> a draft and pass the SME gate before anyone sees it; de-duplication needs the existing question
> set; and the audit trail has to attribute generation server-side."

**B9. How would this scale?** 🟡
> "The database and Celery layers are fine — Postgres, one queue. The real limits are that there's
> no pagination on list endpoints, and the synchronous `.get()` means concurrency is bounded by web
> workers rather than queue depth."

*Follow-up: "What breaks first?"* → "The audit log endpoint — it returns the entire trail
unpaginated and only ever grows."

**B10. Why React rather than Django templates?** 🟢
> "The learner quiz is stateful — question by question, progress bar, an answer snapshot held
> until submit. That's awkward in templates. Engineering judgement rather than a recorded
> decision."

---

## C. AI / LLM

**C11. What exactly does the LLM do?** 🟢
> "It reads one chunk and returns a JSON array of multiple-choice questions with a difficulty
> label, an explanation, and a self-reported confidence. That's the whole job."

**C12. What does it not decide?** 🟢
> "It doesn't determine regulatory truth, doesn't approve anything, and can't publish. Every
> question it drafts is created with status 'draft' and is inert until a human signs it. There is
> no code path from generation to a learner that skips that."

**C13. Can it hallucinate?** 🟢 *(because you answer honestly)*
> "Yes. Published studies find up to 45% of LLM-generated MCQs contain factually implausible
> content. I'd be worried about anyone who claimed otherwise."

**C14. How do you prevent hallucinations?** 🟡
> "I don't prevent them — I mitigate at three layers and prevent at one. The prompt supplies only
> the chunk text and forbids outside knowledge. Output is schema-validated, so malformed drafts
> never persist. The model self-reports confidence and low-confidence questions are excluded from
> mastery scoring. But the only actual *control* is the fourth layer: a qualified human signs
> every question."

*Follow-up: "So the prompt doesn't guarantee grounding?"* → "Correct. A prompt is a request, not a
guarantee. That's exactly why the gate is mandatory rather than advisory."

**C15. How are questions grounded?** 🟡
> "Each question stores the chunk it came from, the prompt supplies only that passage, and the
> reviewer sees the passage inline beside the question."

*Follow-up: "Does that prove the question is correct?"* → **🔴 Say no clearly.** "No. Provenance
proves *where it came from*, not that it's *right*. There's no entailment verification — nothing
mechanically checks the correct answer follows from that passage. That's the biggest gap in the AI
layer and it's item four in my future scope."

**C16. What if the LLM returns invalid JSON?** 🟢
> "It never persists. Markdown fences are stripped first — instruct models emit them despite being
> told not to — then it's parsed, then each item must have four required keys and at least two
> options. Anything unusable raises and triggers a retry, and after three retries we fall back."

**C17. What if NVIDIA NIM is down?** 🟢
> "Three retries with linear backoff, then a deterministic offline generator whose correct answer
> is taken verbatim from the SOP text — so it can't hallucinate. Lower quality, not lower
> integrity. Those questions are tagged so a reviewer can see which path produced them."

*Follow-up: "Is that tested?"* → "Yes — with a mocked provider, so I test the real retry loop
rather than just the missing-key shortcut. That was a gap I found and closed."

**C18. Why not fine-tune your own model?** 🟢
> "No training data, and it would move correctness into the weights where nobody can review it.
> The SME gate is a better use of that effort."

**C19. Why not use RAG for generation?** 🟢
> "There *is* a retrieval-augmented chatbot for free-text questions about an SOP. But for
> generation there's nothing to retrieve — I already know which chunk I'm generating from."

**C20. How do you evaluate question quality?** 🔴
> "Structurally, and by human review. Schema validation, provenance, the model's confidence, and
> then the SME. There is **no automated quality score** — no relevance or distractor-plausibility
> check. That's a real gap."

---

## D. Quiz generation & chunking

**D21. Why chunk at all?** 🟢
> "Two reasons. A 20-page SOP in one prompt produces vague questions. But architecturally, the
> chunk is the unit that makes adaptation possible — without it a wrong answer only tells you the
> learner failed 'the SOP'."

**D22. Why heading-aware chunking first?** 🟢
> "SOPs are structurally regular by regulatory convention — numbered, titled sections. Splitting on
> that gives chunks that map to a coherent part of the procedure, the heading becomes the section
> title the learner sees, and it costs no embedding call."

**D23. What if there are no headings?** 🟢
> "It falls back to embeddings-based semantic chunking — Max-Min cosine similarity — and to
> fixed-length only if that also fails. Which tier fired is recorded per chunk."

*Follow-up: "What's wrong with fixed-length?"* → "It creates weak semantic boundaries. Worse here:
an arbitrary cut produces a chunk spanning two topics, which makes section mastery for it
meaningless. It's the last resort, and I'd flag it to a reviewer — that's not surfaced yet."

**D24. How do you detect duplicates?** 🟡
> "Exact signature on the normalised question and correct answer, plus lexical near-duplicate
> detection. The thresholds are asymmetric: correct-answer similarity above 0.8 *and* stem
> similarity above 0.4."

*Follow-up: "Why asymmetric?"* → "The answer identifies which *fact* is being tested; the stem just
confirms it's the same subject. Rewording mangles the stem — 'What must be done before batch
release?' and 'Prior to batch release, what is required?' share only 0.40 overlap despite being the
same question. A symmetric high bar would have missed exactly the case exact matching already
missed."

*Follow-up: "What about semantic duplicates with no shared words?"* → 🔴 "Still get through.
Embedding similarity would catch them; I didn't add it because it puts an embedding call in every
generation run and makes de-duplication depend on the provider being reachable."

**D25. How is difficulty set?** 🟡
> "The model self-assigns easy/medium/hard, and that seeds an Elo rating which then drifts based on
> how real learners actually perform. So the label is a starting prior, not a fixed truth."

*Follow-up: "Can an admin choose the difficulty?"* → "No. There was a UI control that sent a
difficulty parameter the backend never read — I found it in the audit and removed it rather than
leave a dead control implying something the system doesn't do."

---

## E. SME review & e-signature

**E26. Why is SME review required?** 🟢
> "Up to 45% of generated MCQs are implausible. In compliance training a wrong question can certify
> someone as competent in a procedure they don't understand — and that record is what an inspector
> later relies on."

**E27. Can an AI question reach a learner without approval?** 🟢
> "No. Created as draft, and the learner-facing queryset forces approved status **server-side** —
> not by the client passing a filter. There's a test where a learner explicitly requests drafts and
> gets an empty list."

**E28. How does the electronic signature work?** 🟢
> "The reviewer re-enters their own password, verified with `check_password` — not just an
> authenticated session. On success we store a SHA-256 hash of the exact content approved, who
> approved it, and when."

*Follow-up: "What exactly is hashed?"* → "Canonical JSON of the question text, explanation,
difficulty, and the full option list including which is correct — options ordered by id, keys
sorted, so the digest is deterministic. There's a test asserting stability."

*Follow-up: "Why SHA-256 and not a digital signature?"* → 🟡 "Honest answer: this is a content
*binding*, not a cryptographic identity. A true per-user key-pair signature would be stronger.
SHA-256 over MD5 or SHA-1 because those are collision-broken."

**E29. What if approved content is edited?** 🟢
> "403 through the API — PATCH, PUT and DELETE are all blocked. And if something changes it by
> another route, the ORM or the admin site, `signature_is_intact()` recomputes the hash and detects
> it. Fourteen tests cover that."

*Follow-up: "Is that checked automatically?"* → 🟡 "No — it's a backstop you can call, not a
monitor. A scheduled integrity sweep would be the next step."

**E30. How do you correct a bad published question?** 🟡
> "Reject it, which returns it to draft and clears the signature binding, then edit and re-approve.
> The gap is that the superseded wording isn't retained — there's no revision history, so you can't
> reconstruct exactly what a learner saw. That's item three in future scope."

---

## F. Adaptive learning

**F31. What actually makes this adaptive?** 🟢
> "The content selected for the next assessment changes based on measured per-section performance.
> Concretely: a learner scores 33% on a nine-question SOP and gets six questions back, covering
> only the two weak sections. I can show you that."

*Follow-up: "Is that enforced?"* → 🔴 See F40.

**F32. How is mastery calculated?** 🟡
> "Two things, deliberately separate. Priority comes from recency-weighted accuracy per section.
> Mastery status is streak-based — three consecutive passing attempts retires a section."

*Follow-up: "So a section can be strong but not mastered?"* → "Yes, and that's intentional. Those
answer different questions: priority asks 'is this learner weak right now', mastery asks 'have they
passed repeatedly'."

**F33. Why recency weighting?** 🟢
> "Because a flat lifetime average can't represent *change*. A learner who went nought-out-of-five
> then five-out-of-five sits at 50% lifetime — so they'd stay flagged weak while the screen
> displayed 'Recent: 100%' right beside it. The screen contradicted itself. That was a real bug."

*Follow-up: "What's the formula?"* → "`w_i = 0.5^(i/5)` where i counts back from the newest answer.
So five answers ago counts half. That learner now scores 66.7% instead of 50% and moves from HIGH
to MEDIUM; five more correct answers takes them to 85.7% and LOW."

*Follow-up: "Why half-life five?"* → 🟡 "It matches the window already used for the displayed
'recent accuracy', so 'recent' means one half-life everywhere. It's a chosen default, not fitted —
with real data it's the first constant I'd tune."

**F34. Does one good answer erase a bad history?** 🟢
> "No — that was a design constraint. One correct answer after nine wrong still scores under 30%.
> And it works both ways: a learner going five-right-then-five-wrong scores 33.3%, so decline is
> caught *sooner* than a lifetime average would catch it."

**F35. How do you handle small samples — 1/1 versus 50/50?** 🟢
> "Below three answers a section can't be excluded on accuracy alone. One correct answer gets you
> MEDIUM with the reason 'insufficient evidence to rule this section out'. It's deliberately
> asymmetric — weak performance on a small sample stays HIGH, because under-training produces an
> unqualified operator while over-training costs a few questions."

*Follow-up: "Why not a Wilson confidence interval?"* → "I looked at it. Wilson's lower bound at
ten-out-of-ten is about 72%, which is below my 80% threshold — so a learner with a perfect ten
would be permanently flagged for retraining. Too harsh. A count-based gate is cruder but behaves
sensibly at this scale."

**F36. Why these thresholds — 60 and 80?** 🟡
> "80 is the pass mark used system-wide, so 'proficient' means the same thing everywhere. 60 is a
> chosen default marking 'clearly struggling'. I'd tune it with deployment data — it's a default,
> not a derived constant."

**F37. What happens to a topic never assessed?** 🟢
> "It's HIGH priority with the reason 'never assessed'. And that check comes *first*, before the
> mastered check, so an absence of data is never mistaken for competence. That was a real bug —
> selection used to read mastery rows, and a never-assessed section has no row, so it was
> invisible. A learner tested only on section A would be retrained only on section A forever."

**F38. Can two learners get different training?** 🟢
> "Yes, and there's a test that proves the *content* differs, not just that they're isolated from
> each other. Learner A weak on CAPA and learner B weak on GMP get disjoint question sets."

**F39. Does it adapt within a quiz?** 🟡
> "No — adaptation is between assessments, not question by question. Item-level adaptive testing
> needs calibrated item parameters, difficulty and discrimination per question, which needs far more
> response data than a deployment this size produces. It's a deliberate choice, not an oversight."

**F40. If I modify the browser, can I bypass your adaptive decision?** 🔴 **Know this cold.**
> "Partly, and I'll be precise about it. The server validates that every submitted question belongs
> to this attempt's SOP, matches the role, and is approved — I can show you the 400s. What it
> doesn't yet do is pin the exact set that was *offered*, so a determined client could answer an
> approved question the engine excluded, and that would move mastery for that section.
>
> The blast radius is bounded: same SOP, approved content only, own attempts only, and every
> attempt is a separate audited record. You can't reach another learner's data, unapproved content,
> or another SOP.
>
> The fix is one join table — persist the offered questions on the attempt and validate against it.
> I didn't ship a migration two days before review. It's the first item in my future scope."

---

## G. FSRS

**G41. What is FSRS?** 🟢
> "The Free Spaced Repetition Scheduler — the algorithm behind modern flashcard apps. It models
> memory as two numbers per learner per item: stability, how many days until recall drops to about
> 90%, and difficulty. After each review it updates both and schedules the next one."

**G42. Why not a fixed interval?** 🟢
> "Everyone forgets at different rates and different material decays differently. A fixed 30-day
> ladder over-tests what you know cold and under-tests what you barely passed. This project
> actually started with a fixed Leitner ladder and moved off it."

**G43. Why doesn't FSRS choose the topic?** 🟢 **This is your strongest technical answer.**
> "Because it mathematically can't. Retrievability is `R(elapsed, S) = (1 + (19/81)·elapsed/S)^-0.5`,
> and at elapsed zero that's 1.0 for *every* stability value. So immediately after an assessment, a
> section you just failed and a section you just passed both score 1.0. FSRS's own metric cannot
> distinguish them at the moment you most need it to.
>
> Retrievability answers 'is it time to review?'. Accuracy answers 'is this learner weak?'.
> Different questions, different algorithms."

**G44. What if priority and FSRS disagree?** 🟢
> "They're allowed to, and the system represents it rather than forcing a resolution. Each section
> carries `selected_for_retraining` — what — and `is_due` — when. `available_now` is the
> conjunction. The learning path shows weak-but-not-due sections under 'Scheduled for 14 August';
> the assignment engine only hands over what's available.
>
> This was a real defect: the path used to recommend a section the quiz screen wouldn't offer. A
> reviewer clicking through would have hit a dead end."

**G45. Did you tune FSRS?** 🟢
> "No, deliberately. Published default weights, fit on hundreds of millions of reviews. Per-user
> optimisation needs a volume of logged reviews this deployment will never generate — the same
> data-scale reasoning that ruled out neural knowledge tracing."

---

## H. Security

**H46. Can a learner see the answer key?** 🟢
> "No. Separate serializers — the learner one omits `is_correct` and the explanation, and it's
> chosen from the user's role, not a query parameter, so they can't opt back in. Eleven tests
> including one that inspects the raw response bytes."

*Follow-up: "What about the options endpoint?"* → "Reviewer-only. That was a side channel I closed
— it exposed the same field."

*Context worth volunteering:* "This was the most serious defect the audit found: the API was
shipping `is_correct: true` before the learner answered. Scoring was tamper-proof while the
assessment was effectively open-book."

**H47. Can a learner resubmit to improve their score?** 🟢
> "No. An atomic conditional update claims the attempt — a second submission can't match, gets a
> 409, and is itself audited. It's compare-and-set rather than read-then-check, so two concurrent
> submissions can't both pass."

**H48. Can a learner see another learner's data?** 🟢
> "No — attempts, answers, the dashboard and the learning path are all scoped server-side. The
> dashboard was leaking every learner's name and score to any authenticated user; that's fixed and
> tested for all three roles."

**H49. Are uploaded SOPs protected?** 🟢
> "Yes. Django's media route serves files with no authentication and activates whenever DEBUG is
> on — which the Docker stack set — so every uploaded procedure was publicly downloadable. The
> route is removed; files go through an authenticated endpoint."

**H50. Is there rate limiting?** 🟢
> "On login, ten a minute per IP, and on e-signature verification, twenty a minute per user. The
> login throttle counts all attempts, so guessing correctly on attempt eleven is still blocked."

---

## I. GxP

**I51. Is this GxP compliant?** 🟢 *(because the answer is no)*
> "No, and I wouldn't claim it. It implements several 21 CFR Part 11-*style* technical controls —
> a password-verified approval bound to a content hash, an attributed audit trail, role-based
> access, immutable approved content. What it doesn't have is tamper-evident audit storage,
> separation of duties, a training qualification record, or any validation exercise. That's the gap
> between credible controls and compliance."

**I52. How do you maintain traceability?** 🟡
> "Question to chunk to SOP, plus which chunking strategy produced the chunk and whether the
> question came from the live model or the fallback. The weakness is that the chunk FK is SET_NULL,
> so provenance is one delete away from being lost."

**I53. Is training history preserved?** 🟡
> "Attempts and answers are never overwritten and resubmission is blocked. But there's no record of
> which questions were *offered*, and no question revision history — so I can't reconstruct a past
> attempt exactly as the learner saw it."

**I54. What happens when an SOP changes?** 🔴 **Know this cold.**
> "Reprocessing is blocked with a 409 when approved questions exist — because it would delete the
> chunks, cascade away every learner's section mastery, and orphan approved questions from their
> source text. Rather than let that happen silently, it refuses.
>
> So today, revising a procedure has no supported workflow. That's my number-one limitation. The
> fix is a SOPVersion entity where a new version supersedes rather than replaces, with mastery bound
> to a version — that's a data migration over live learner records, which isn't something I'd ship
> two days before a review."

**I55. Who can approve — is there separation of duties?** 🔴
> "No. An Admin can both generate and approve the same question — the reviewer permission accepts
> staff users. In a real deployment you'd want the approver to be a different person from the
> initiator. Known gap."

---

## J. Testing & future scope

**J56. You have 219 tests — what do they prove?** 🟢
> "I'd rather answer what they *establish* than count them. The valuable ones are scenario-first:
> a controlled three-section learner scenario that asserts what the system claims about a learner.
> That's what found the bugs — unit tests would all have passed, because each function did what it
> said. The defect was in the composition."

**J57. Tell me about a bug you found.** 🟢 **Your best story — volunteer it.**
> "Before touching the adaptive engine I wrote a scenario — GMP right, CAPA and Documentation
> wrong — and asserted the behaviour the system claimed. Three of seven assertions failed
> immediately.
>
> The worst one: whole-SOP mastery was hiding weak sections. If you aced three quizzes that
> happened to cover one section, the whole SOP was marked mastered and disappeared from retraining
> — while another section was still failing. The test returned no assignments at all.
>
> All three traced to concrete design flaws, all three have regression tests named for the bug they
> prevent."

**J58. What's the weakest part of the system?** 🟢
> "The junction between the adaptive engine and the quiz the learner actually receives. The engine
> is sound and well tested; its output is honoured by the browser rather than enforced by the
> server."

**J59. What would you build next?** 🟢
> "Server-side quiz sessions first — it converts the adaptive decision from advisory to enforced
> and makes results reproducible. Then SOP versioning, because it's the only gap that can silently
> destroy learner data."

**J60. If you had another month?** 🟢
> "Those two, then difficulty-aware priority — Elo already weights the pass signal but not the
> selection metric, which is a real inconsistency. Then entailment verification, which is the only
> thing that would upgrade grounding from provenance to actual verification."

---

## The five that will hurt most — rehearse these out loud

1. **F40** — "Can I bypass your adaptive decision?"
2. **I54** — "What happens when an SOP changes?"
3. **C15** — "Does provenance prove the question is correct?"
4. **B6** — "So it's asynchronous?"
5. **I51** — "Is this GxP compliant?"

All five have honest answers that make you look *better*, not worse — provided you say them
before the reviewer digs them out.
