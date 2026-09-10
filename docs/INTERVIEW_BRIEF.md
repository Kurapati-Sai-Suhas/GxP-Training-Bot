# Interview Brief — Ankercloud, Trainee AI/ML Engineer

**Project:** GxP Training Bot · **Prepared:** 10 September 2026

> **Rule for tomorrow:** everything in this document is measured or verifiable in the repo.
> If you are asked something not covered here, say "I haven't measured that" — it is a
> stronger answer than a guess, and it is the exact habit this project is built around.

---

# 1. What was wrong

Found by running the system, not by reading the docs.

| # | Problem | Severity | Evidence |
|---|---|---|---|
| **1** | **Both NVIDIA NIM models retired by the provider.** `meta/llama-3.1-8b-instruct` EOL 2026-08-26, `nvidia/nv-embedqa-e5-v5` EOL 2026-08-25. Both return HTTP 410 Gone. | **Critical** | Live API probe |
| **2** | **The outage was invisible.** The fallback contract swallows every provider error, so there were no exceptions, no 5xx, no alert. Every generation had been silently served by the offline generator. | **Critical** | Test-run log |
| **3** | **410 misclassified as `unknown`.** A permanent, operator-actionable failure was indistinguishable from a transient network blip. | High | `classify_llm_error` |
| **4** | **404 only detected via exception type name.** Every other category keys off the status code in the message; 404 alone relied on the SDK exception surviving intact, so a wrapped 404 degraded to `unknown`. | Medium | Same function |
| **5** | **Model ids hardcoded.** Rotating a retired model required a code edit, review, rebuild and redeploy — for a one-line config change. | High | Two modules |
| **6** | **No embedding model invocable at all.** Every candidate 404s — catalogue listing ≠ entitlement. Tier 2 of the three-tier chunking cascade is dead, not misconfigured. | Medium | Live probe of 4 models |
| **7** | **No health or readiness endpoints.** Nothing for a load balancer to probe. | High | Route audit |
| **8** | **No inference telemetry.** No way to answer "is the AI actually working?" | High | — |

**The headline finding is #2, not #1.** Models get retired; that is normal. The defect was
that a system designed to degrade gracefully had no way to report that it *had* degraded.
A fallback is a safety net, not a monitor.

---

# 2. What was fixed

| Change | File | Why |
|---|---|---|
| `model_retired` error category | `ai_engine/services.py` | 410 is permanent; retrying cannot help. Distinct from a blip. |
| 404 detected from message too | `ai_engine/services.py` | Consistency with every other category |
| `nim_model()` / `embed_model()` accessors | `ai_engine/services.py`, `sops/services.py` | Model id is configuration. Resolved at **call** time, so a running worker picks up a rotation without a rebuild. |
| Working default model | `ai_engine/services.py` | `openai/gpt-oss-20b`, verified invocable |
| `manage.py check_ai_provider` | new | Preflight. Exit `0`/`1`/`2`. Gates a deploy, runs on a schedule. |
| `ai_engine/metrics.py` | new | Fallback-rate telemetry — the only in-band signal of a silent outage |
| 4 operational endpoints | `config/health.py` | `live/`, `ready/`, `ai/`, `metrics/` |
| **25 new tests** | | Every fix has one |

**Test count: 330 → 355.** No existing test was modified or deleted.

---

# 3. Future scope implemented — and one deliberately rejected

## 3.1 BM25 retrieval — implemented, measured, **REJECTED**

This is the strongest thing you have to talk about. Lead with it.

**Hypothesis.** Prior measurement (P1-007) found Q01 failing due to a three-way tie at
lexical-overlap score 3. IDF should break that tie.

**Result — it did not. BM25 lost on every configuration:**

| Ranker | Hit@1 | R@3 | MRR |
|---|---|---|---|
| **overlap (kept)** | 0.8571 | 1.0 | **0.9286** |
| bm25 b=0.75 | 0.8571 | 1.0 | 0.9048 |
| bm25 b=0.50 | 0.8571 | 1.0 | 0.9048 |
| bm25 b=0.25 | 0.8571 | 1.0 | 0.9048 |
| bm25 b=0 | 0.8571 | 1.0 | 0.9167 |

*(gold set v1.0, 14 scored queries, corrected chunking, max_chunks=6, same corpus throughout)*

**Two reasons, both properties of this corpus rather than defects in BM25:**

1. **IDF needs a corpus to be a statistic over.** Each SOP holds 4–5 chunks, so document
   frequency takes ~3 distinct values and IDF weights span a range too narrow to separate
   anything. Q01 ranked *worse* under every variant.
2. **Length normalisation actively harms it.** On the live corpus a 6-token title-only chunk
   scored **2.885** against the correct 31-token chunk's **1.472** — purely for being short.
   Chunk length here reflects how much a section says, not verbosity, which is what `b` assumes.

**`b` was swept to attribute the regression, never to pick a value** — fitting a free
parameter on a 15-query gold set overfits the measuring instrument.

**Decision:** keep the overlap ranker. Keep the BM25 code and the measurement script so the
comparison is re-runnable when the corpus grows. A test pins `DEFAULT_RANKER == "overlap"`
so nobody "improves" it back later.

## 3.2 Provider resilience and observability — implemented

Covered in §2. Frames as: detection for a failure mode that produces no errors.

## 3.3 Deliberately not attempted

| Candidate | Why not |
|---|---|
| Neural knowledge tracing (DKT/SAKT) | Interaction volume is 3–4 orders of magnitude short. The harness exists and correctly reports `INSUFFICIENT_DATA`. |
| pgvector / dense retrieval | No embedding model invocable — a hard blocker, not a choice |
| Entailment verification | Needs a second model; the one available model has 30–100s latency |
| S3 storage backend | Untested code the night before a demo. Flagged as top follow-up. |

---

# 4. Research justification

| Paper | Finding | Decision it drove |
|---|---|---|
| **Robertson & Zaragoza (2009)**, *The Probabilistic Relevance Framework: BM25 and Beyond*, FnTIR | Canonical BM25 derivation | Implemented exactly as specified — then rejected on measurement |
| **Thakur et al. (2021)**, *BEIR*, NeurIPS D&B | BM25 is a robust baseline; dense retrievers **often fail to beat it** zero-shot out-of-domain | Justified strengthening lexical retrieval before reaching for embeddings. Also explains the negative result: BEIR's finding holds at realistic corpus scale; this deployment is orders of magnitude below it. |
| **Pelánek (2016)**, *Elo in adaptive educational systems*, C&E 98 | Elo ≈ online Rasch/1PL estimator, no batch refitting | Elo for ability + difficulty (K=32 / K=16) |
| **Wilson et al. (2016)**, EDM | Bayesian IRT extensions **outperform** neural nets for proficiency estimation | Basis for **rejecting** DKT |
| **FSRS-4.5**, open-spaced-repetition | DSR memory model, 17 published weights | Retraining schedule, retention 0.9 |
| **Ye, Su & Cao (2022)** KDD; **Settles & Meeder (2016)** ACL | Treating items as equally hard understates what a learner knows | Difficulty-weighted mastery |
| **Geng et al. (2024)**, NAACL | LLM self-reported confidence is miscalibrated | Confidence used **only** defensively — never as a quality gate |
| **Kiss et al. (2025)** Discover Computing; **Moreno-Cediel et al. (2025)** KBS | Max-Min semantic chunking; fixed-size splits create weak semantic boundaries | Three-tier chunking cascade |

---

# 5. Before vs after

| Metric | Before | After |
|---|---|---|
| Live AI path | **Dead** (both models 410) | Working (`openai/gpt-oss-20b`) |
| Time to detect a dead model | Never | Immediate — preflight + `fallback_rate` |
| Time to rotate a model | Code change + redeploy | Env var, no rebuild |
| 410 classification | `unknown` | `model_retired` |
| Health endpoints | 0 | 4 |
| Inference telemetry | None | Latency, success, fallback rate, error categories |
| Tests | 330 | **355** |
| Retrieval Hit@1 / MRR | 0.857 / 0.929 | **unchanged — BM25 measured and rejected** |

**Say the last row out loud.** "I tried an improvement, measured it, it was worse, so I
didn't ship it" is a stronger signal than any number you could quote.

---

# 6–9. Deployment

Full guide with exact commands: **[`AWS_DEPLOYMENT.md`](AWS_DEPLOYMENT.md)**

Architecture, env vars, and cloud resources are all in that document. Two things to
memorise:

- **App Runner closed to new customers on 30 April 2026.** AWS now directs new deployments
  to **ECS Express Mode**. Most tutorials are stale. Mentioning this unprompted signals you
  read current documentation rather than a blog post.
- **Health check path is `/api/health/ready/`, not `/live/`.** Readiness checks dependencies
  and returns 503 to drain the instance; liveness checks only the process, because a
  liveness failure *restarts* containers and an RDS failover would then restart the whole
  service.

---

# 10. "How did you deploy your AI/ML project?" — 2 minutes

> It's a Django REST backend, a React SPA, PostgreSQL, Redis and Celery, with an LLM
> inference layer. On AWS the container image goes to ECR and runs on ECS Express Mode —
> Fargate underneath, and it provisions the load balancer, TLS and autoscaling for you. I'd
> have reached for App Runner, but AWS closed it to new customers in April, so Express Mode
> is the current path.
>
> Data services are managed: RDS PostgreSQL in a private subnet, ElastiCache Valkey as the
> Celery broker, S3 for uploaded SOPs — private bucket, served only through an authenticated
> endpoint, because these are controlled documents. Secrets are in Secrets Manager and
> injected at container start; nothing is in the image or in git.
>
> The Celery worker is a second service off the same image with no load balancer. That
> separation matters here — LLM calls take 30 to 100 seconds, so they can't sit on a request
> thread.
>
> The part I'd actually highlight is what I found when I ran it. Both NVIDIA models had been
> retired by the provider two weeks earlier. The application never broke, because there's a
> deterministic offline fallback — but that's exactly why nobody noticed. So I added a
> preflight check that tests real invocability, and a fallback-rate metric, because when a
> system is designed to degrade silently, the absence of errors tells you nothing. I also
> made the model id an environment variable, so rotating a dead model is a config change
> instead of a redeploy.

---

# 11. SageMaker

**"Why did you use SageMaker?"** — Do not pretend you did.

> I didn't, and that was a deliberate call. SageMaker hosts model artefacts you train or
> bring. My inference is a hosted third-party LLM behind an OpenAI-compatible HTTP API —
> there's no artefact to host, so a SageMaker endpoint would add cost and latency for
> nothing.
>
> Where it *would* belong: my roadmap has a reranker over retrieved chunks. That's a real
> trained model with weights, and I'd put it on a SageMaker real-time endpoint — versioned
> models, built-in A/B via production variants, autoscaling, and Model Monitor for drift.
> The blocker isn't SageMaker, it's that I don't have the labelled data to train one yet.
> My gold set is 15 queries. I'd rather say that than deploy a model I can't evaluate.

**"Why not put the whole application inside SageMaker?"**

> Because it's a model serving platform, not a web host. Endpoints are EC2-backed, so
> scaling is minutes where Fargate is seconds. There's no managed path for Django's static
> files, migrations or admin. And it'd couple the web tier's lifecycle to the model's — I
> want to redeploy the API without touching inference, and swap a model without redeploying
> the API. Which is exactly what the env-var model config buys me.

---

# 12. MLOps

**"How are you monitoring your model?"**

> Three layers. Structured JSON logs on every inference with model id, latency, success and
> error category — CloudWatch Logs Insights aggregates those across workers. In-process
> counters at `/api/health/metrics/`, admin-only. And a scheduled preflight that actually
> calls the model.
>
> The metric that matters is **fallback rate**. My pipeline falls back to a deterministic
> generator on any provider failure, so an outage produces no exceptions and no 5xx — you
> cannot alert on an error that's never raised. Fallback rate is the only in-band signal.
> Zero means healthy; one means the AI is completely dead while the app looks fine. That's
> precisely the state I found the system in.

**"How would you retrain it?"**

> The LLM is third-party, so retraining isn't mine. What *is* mine is the retrieval
> configuration and, eventually, a reranker. The loop is: gold set → offline evaluation
> harness → change one variable → compare on fixed metrics → adopt only on improvement.
> I ran that loop for BM25 and it told me not to ship, which is the loop working.
>
> For a trained model I'd version the training data, register the model, evaluate the
> candidate against the incumbent on a held-out set, and promote only on a win.

**"How would you detect model degradation?"**

> Retrieval quality against the gold set on a schedule — same queries, same corpus, so a
> regression is attributable. Operationally, fallback rate, latency percentiles and error
> categories. For a trained model, Model Monitor for input drift plus offline metrics on
> fresh labels.
>
> One thing I'd flag: my strongest degradation signal is a *silent* one. A retired model
> looks identical to a healthy one at the HTTP layer. That shaped the whole monitoring design.

**"How would you roll back a bad model?"**

> Two levels. A model swap is an env var — change the secret, force a new deployment, no
> rebuild. That's minutes. An application rollback is redeploying the previous image; images
> are tagged with the commit SHA, never just `latest`, so the previous version is always
> addressable. For a SageMaker endpoint it'd be shifting the production variant weight back.

---

# 13. RAG

| Question | Answer |
|---|---|
| **Why RAG?** | The model must answer from *this* SOP, not from what it learned about pharma. Grounding in retrieved chunks makes every answer traceable — which is the whole compliance argument. |
| **Why chunking?** | An LLM call gets one coherent unit. Heading-aware splitting follows the document's own structure, so a chunk maps to a real section — and the heading becomes the provenance label. |
| **Why semantic retrieval?** | I don't use it, and I'd say so. Retrieval is lexical overlap. Semantic retrieval is on the roadmap but the embedding endpoint isn't entitled on my account — every candidate 404s. BEIR also shows dense retrievers often lose to lexical baselines out-of-domain, so it isn't a free win. |
| **Why embeddings?** | Used for *chunking*, not retrieval — Max-Min semantic chunking when a document has no headings. Currently unavailable, so the cascade falls to fixed-length. |
| **Why reranking?** | Not implemented. It's the next real step, and the one place a SageMaker endpoint would genuinely belong. |
| **How do you evaluate retrieval?** | 15-query hand-labelled gold set over 3 SOPs and 6 failure categories. Hit@1, Recall@k, MRR, Precision@1. Irrelevant queries are *excluded*, not scored zero — recall over an empty relevant set is undefined. I flag Recall@5 as degenerate because retrieval doesn't filter below 6 chunks. |
| **How do you reduce hallucination?** | Four ways: the prompt is constrained to one retrieved chunk; output must satisfy a strict JSON contract; a qualified human approves every question under e-signature before a learner sees it; and low-confidence items are treated defensively. Confidence is never an automatic gate — Geng et al. show self-reported confidence is miscalibrated. |
| **How do you preserve provenance?** | Every question carries a foreign key to its source chunk. Without that link the training record isn't defensible under audit. My chunking work is partly *about* provenance — a change was adopted because it eliminated title-only chunks that were winning retrieval. |

---

# 14. Adaptive learning

| Question | Answer |
|---|---|
| **Why Elo?** | Pelánek (2016) shows it's an efficient online approximation to Rasch/1PL. It updates per response with no batch refit and gives a usable estimate from the first answer — which matters at my data scale. K=32 for learners, 16 for questions, because an item's difficulty shouldn't swing on one learner. |
| **Why FSRS?** | It replaces a calendar interval with a per-learner prediction of when forgetting occurs. Retention target 0.9. |
| **Key limitation I'd volunteer** | FSRS answers *when* to review, not *what*. At elapsed time zero R(0,S)=1.0 for every stability, so everything looks equally known. Content selection uses a separate recency- and difficulty-weighted accuracy engine. |
| **Why not DKT?** | Data. DKT wants ~10⁵–10⁶ interactions; I have tens. Wilson et al. (2016) found Bayesian IRT extensions beat neural nets for proficiency estimation anyway. A DKT here would look trained while encoding noise. |
| **Why not transformers?** | Same reason, worse — SAKT/SAINT want 10⁵–10⁷. It's a data problem, not an architecture problem. |
| **What is the data limitation?** | Three to four orders of magnitude. Worse, most existing rows are synthetic demo data, and the demo command rebuilds its corpus each run, so nothing accumulates. |
| **How would you transition?** | Instrumentation is already done: server-set timestamps, offered-set binding, and difficulty/ability snapshotted *before* the Elo update to prevent look-ahead bias. The harness does per-learner temporal splits with ROC-AUC, PR-AUC, Brier and calibration. It reports `INSUFFICIENT_DATA` today and refuses to emit metrics. When real volume arrives I'd start with BKT or PFA, and adopt a neural model only if it beats them on that harness. |

---

# 15. "Is this system GxP compliant?"

> No — and I'd be cautious of anyone who said yes about a system rather than a validated
> installation. Compliance is a validation outcome, not a property of code.
>
> What it is, is **GxP-oriented**: built around the controls those regulations require.
> Every question traces to a specific chunk of a specific SOP. Nothing reaches a learner
> without a qualified reviewer approving it under a password-based electronic signature.
> There's an append-only audit trail with no update or delete endpoint. Assessment integrity
> is enforced server-side — the client can't influence what's scored.
>
> What's missing before anyone could claim compliance: there's no CSV package — no IQ, OQ or
> PQ, no validation protocol, no traceability matrix to Part 11 clauses. No SOP version
> lifecycle, so a chunk's identity isn't stable across a revision. The audit log is
> append-only through the API but isn't cryptographically tamper-evident. And there's no
> separation of duties — an admin can currently also act as an SME, which a real GxP
> environment wouldn't allow.
>
> Those are documented as gaps, not hidden.

---

# Priorities

## MUST DO TODAY

1. **Re-read §1, §3.1, §10, §11.** The retired-model story and the BM25 negative result are
   your two strongest answers.
2. **Run `python manage.py check_ai_provider`** so you've seen the output live.
3. **Hit the four health endpoints locally** — know what they return.
4. **Rehearse §10 out loud.** Twice. Time it.
5. **Know your numbers cold:** 355 tests · Hit@1 0.857 · MRR 0.929 · 15 gold queries ·
   BM25 rejected at 0.9048–0.9167 · both models EOL 25–26 Aug 2026.

## NICE TO HAVE

6. Deploy to AWS following [`AWS_DEPLOYMENT.md`](AWS_DEPLOYMENT.md) — a live URL is strong,
   but the story works without one. **Budget 2–3 hours and stop if it fights you.**
7. Wire the S3 storage backend (top technical follow-up).
8. Re-run `demo_adaptive` so the demo data is fresh.

## DO NOT TOUCH BEFORE THE INTERVIEW

9. **The adaptive engine** — Elo, FSRS, mastery. It works, it's tested, it's defensible.
10. **The offered-set integrity contract.** 180 tests depend on it.
11. **Do not make BM25 the default** to have "an improvement" to show. The negative result
    is worth more.
12. **Do not add a neural KT model.** The refusal is the strongest engineering judgment in
    the project.
13. **Do not commit or push** unless you have decided to.

---

## One-line summary

> "I ran my project, found the provider had retired both my models two weeks earlier and my
> own fallback had hidden it, fixed the detection rather than just the models — then tried a
> textbook retrieval upgrade, measured it, and didn't ship it because it was worse."
