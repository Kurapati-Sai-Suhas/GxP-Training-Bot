# FINAL MEMORIZE — one page, tonight only

Everything here was measured on 19 Aug 2026. If it is not here, do not claim it.

---

## THE 90-SECOND STORY *(learn this properly)*

> "Pharmaceutical manufacturers must train and retrain staff on Standard Operating Procedures.
> Today that's the same deck and the same quiz for everyone — so if someone scores 60%, you know
> *who* struggled, not *what* with, and retraining repeats material they already knew.
>
> My system ingests a controlled SOP and splits it into sections using a three-tier cascade:
> heading detection first, embedding-based semantic chunking if there are no headings,
> fixed-length as a floor. An LLM — Llama 3.1 8B on NVIDIA NIM — then drafts questions from **one
> section at a time**, and every question stores a foreign key to the section it came from.
>
> That foreign key is the whole architecture. It means a learner's answer is evidence about a
> *specific passage*, not about the document.
>
> Every generated question requires a subject-matter expert to approve it under an electronic
> signature bound to a SHA-256 hash of the exact content; after that it's immutable. The LLM
> drafts, the human decides.
>
> The adaptive engine then scores each section on a recency-weighted accuracy — half-life five
> answers — applies an evidence gate and fixed thresholds, and selects only the weak sections.
> In my demo the learner scores 33%, gets **six questions back instead of nine** because the
> section they'd mastered is excluded, and improves from **50% to 100%** on both weak sections,
> measured from their stored answers.
>
> I'll be precise about the classification: the decision layer is **rule-based over a statistical
> signal**, with two model-based components — Elo estimating ability and difficulty online, and
> FSRS modelling memory for review timing. It's adaptive; it isn't machine learning, and the LLM
> takes no part in any adaptive decision.
>
> It implements **GxP-oriented** controls — I'm not claiming compliance."

---

## THE THREE SENTENCES THAT DECIDE THE VIVA

**Classification** — *"The priority engine is rule-based over a recency-weighted statistic. Elo
and FSRS estimate parameters online from responses. It's adaptive; it is not machine learning."*

**Biggest limitation** *(say it before they ask)* — *"Until this week the offered question set
wasn't persisted, so the adaptive decision was advisory. I fixed that: the server now records
exactly which questions were offered and rejects any submission that doesn't match. The remaining
big one is SOP versioning — revising a procedure has no supported path yet."*

**Compliance** — *"GxP-oriented, Part 11-style controls. Not validated, not compliant. Missing:
tamper-evident audit storage, separation of duties, a qualification record."*

---

## NUMBERS

| | |
|---|---|
| Recency weight | `w_i = 0.5^(i/5)`, half-life **5** |
| Thresholds | HIGH **<60%** · MEDIUM **60–<80%** · LOW **≥80%** |
| MIN_EVIDENCE | **3** · Mastery streak **3** |
| Elo | learner **K=32**, question **K=16**, both start **1500** |
| FSRS | **17** published weights, retention **0.9**, min **1 day**, AGAIN/GOOD only |
| **Why FSRS can't select** | **`R(0,S) = 1.0` for every S** |
| Tests | **244** passing, 0 failures, 0 skipped |
| Demo | 9 questions → **6 selected** → **50% → 100% (+50 pp)** |
| Adaptive vs lifetime after retraining | **87.9% vs 75.0%** |
| Interactions in DB | **42** (9 timestamped) — *not a dataset* |

**Know cold:** `0/5 then 5/5` → **66.7%** weighted (lifetime 50%). `5/5 then 0/5` → **33.3%**.

---

## STACK

Django 5.2 + DRF 3.17 · React 18 + Vite · PostgreSQL/SQLite · Redis + Celery · NVIDIA NIM
(`meta/llama-3.1-8b-instruct`, `nvidia/nv-embedqa-e5-v5`) · PyMuPDF · 7 Django apps.

**The `openai` SDK is the client library, not the provider** — NIM is OpenAI-compatible.

---

## FIVE TRAPS

| Trap | Correct answer |
|---|---|
| "So you use semantic chunking?" | "The cascade *has* it, but the demo SOP has numbered headings, so tier 1 ran. The strategy used is stored per chunk." |
| "Vector database / vector RAG?" | "No vector DB, no persisted embeddings. Chatbot retrieval is **lexical word overlap** over one SOP's chunks. At 3–20 chunks an index is overhead." |
| "Is this Knowledge Tracing?" | "No. No latent knowledge-state model, no P(correct) prediction. Threshold classification over an observed statistic." |
| "Why no Transformer?" | "42 interactions total. DKT needs ~500,000. Three to four orders of magnitude short — and my event log only gained timestamps this week." |
| "Does difficulty affect what you retrain?" | "Elo weights the *pass signal*, not the priority. That's a real inconsistency, it's documented, and it's a two-hour fix I chose not to make days before a review." |

---

## NEVER SAY / SAY INSTEAD

| ❌ | ✅ |
|---|---|
| "GxP compliant" | "GxP-**oriented**, Part 11-**style**" |
| "machine learning" / "trained model" | "rule-based over a statistic + two model-based components" |
| "AI-powered adaptive learning" | "AI-assisted generation with a rule-based adaptive engine" |
| "prevents hallucinations" | "mitigated at three layers, prevented at one — the human" |
| "vector RAG" | "lexical retrieval with grounded generation" |
| "proves learning improves" | "verified controlled demonstration, not a user study" |
| "production ready" | "not deployed; Docker and CI written, not executed" |

---

## LOGINS — all `demo12345`

`demo_learner` (learner) · `demo_sme` (SME reviewer) · `anjali` (admin).
Learner in an **incognito window** — the token lives in `localStorage`.

## BEFORE THE DEMO

```bash
cd backend && uv run python manage.py demo_adaptive --stop-after-analysis
```

Navigate by **menu label**, never icon position — the sidebar is role-gated.

## IF SOMETHING BREAKS

*"That's the fallback working as designed"* — then move on. Never troubleshoot live.
If you don't know: *"I don't know that off the top of my head — let me tell you what I do know
and where I'd look."* Never invent an implementation detail.
