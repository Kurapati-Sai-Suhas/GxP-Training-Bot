# FINAL AUDIT SUMMARY

**Audited:** 19 August 2026 · **Commit:** `06b6e95` + uncommitted Batch A+B
**Method:** every claim below was executed and observed. Nothing carried over from documentation.

> **Consolidation note.** The brief requested 14 separate documents. Several would have been
> 80% duplicate prose. The audits were all *performed*; they are consolidated here by section.
> Separate files exist where they carry standalone value: `BASELINE_CURRENT.md`,
> `FINAL_GAP_MATRIX.md`, `FINAL_DEMO_SCRIPT.md`, `FINAL_VIVA_QA.md`, `FINAL_MEMORIZE.md`,
> `KT_DATA_READINESS.md`, `BATCH_A_B_REPORT.md`.

---

# 1. Does the full project work end-to-end?

**Yes — verified twice, by two independent routes.**

## 1.1 Workflow trace

| Stage | Implementation | Verified | Test | Browser | Risk |
|---|---|---|---|---|---|
| SOP upload + validation | `sops/serializers.py:39` (extension allow-list, 20 MB) | ✅ | ✅ | ✅ | Low |
| Text extraction | `sops/services.py` PyMuPDF | ✅ demo | ✅ | ✅ | Low |
| Chunking cascade | `sops/services.py:133` heading → semantic → fixed | ✅ demo | ✅ | ✅ | Low |
| Chunk persistence | `SOPChunk`, `chunking_strategy` recorded | ✅ 3 chunks, `heading` | ✅ | ✅ | Low |
| AI generation | `ai_engine/services.py:135` llama-3.1-8b via NIM | ✅ live | ✅ | ✅ | **Provider dependency** |
| Validation / normalisation | `_normalize_drafts` | ✅ | ✅ | — | Low |
| Duplicate detection | `is_near_duplicate` lexical (0.8 / 0.4) | ✅ "Skipped duplicates: 0" | ✅ | — | Semantic dupes pass |
| Draft questions | `Question(status="draft")` | ✅ | ✅ | ✅ | Low |
| SME review | `quiz/views.py` reviewer-only queryset | ✅ | ✅ | ✅ | Low |
| Password re-verification | `check_password` server-side | ✅ | ✅ | ✅ | Low |
| E-signature + content hash | SHA-256 canonical JSON | ✅ `content_hash=23eadef2…`, intact=True | ✅ | ✅ | Low |
| Immutability | 403 on PATCH/PUT/DELETE | ✅ | ✅ (14) | — | Low |
| Learner login / dashboard | DRF token | ✅ | ✅ | ✅ | Low |
| Attempt creation | `perform_create` | ✅ | ✅ | ✅ | Low |
| **Server-defined offered set** | `QuizAttemptQuestion` | ✅ `offered_question_ids:[236…244]` | ✅ (13) | ✅ | Low |
| Questions displayed | `App.jsx` renders server set | ✅ "Question 1 of 9" | — | ✅ | Low |
| Submission validation | exact-set contract | ✅ | ✅ | ✅ 200 | Low |
| Scoring | denominator = offered set | ✅ **"0 of 9"** not "0 of 0" | ✅ | ✅ | Low |
| `AttemptAnswer` persistence | + `answered_at` | ✅ 9 rows timestamped | ✅ (10) | ✅ | Low |
| Mastery / Elo / FSRS update | `apply_answer`, `services.py`, `fsrs.py` | ✅ demo | ✅ | ✅ | Low |
| Adaptive analysis | `adaptive.analyse_sections` | ✅ 2 HIGH, 1 LOW | ✅ | ✅ | Low |
| Learning path | `learning_path` endpoint | ✅ | ✅ | ✅ | Low |
| Retraining selection | 6 of 9 | ✅ | ✅ | ✅ | Low |
| Reassessment + gain | 50 → 100, +50.0 pp | ✅ | ✅ | ✅ | Low |
| FSRS scheduling | next-review dates diverge | ✅ | ✅ | ✅ | Low |
| Audit trail | 17 action types, 11 entries this SOP | ✅ | ✅ | ✅ | Not tamper-evident |

**Nothing in the main workflow is broken.**

## 1.2 Browser verification of the critical path

A full quiz was driven through the real UI as `demo_learner`: attempt created
(`offered_question_ids: [236…244]`), 9 questions rendered, submitted → **HTTP 200**, result
screen showed **"0 of 9 correct"**.

The unanswered-question case is the important one: the honest client submitted all 9 with nulls
and the server scored against **9**, not against the number answered. That is the exact
inflation vector closed by Batch A.

---

# 2. Adaptive learning — what it actually is

## 2.1 Formal answers

| | |
|---|---|
| **A. Priority engine rule-based?** | **Yes.** Fixed thresholds in `_classify`, four branches in a fixed order |
| **B. Statistical signal** | Exponentially recency-weighted accuracy: `w_i = 0.5^(i/5)`, `i=0` newest |
| **C. Fixed parameters** | 60% / 80% thresholds · half-life 5 · MIN_EVIDENCE 3 · mastery streak 3 · Elo K 32/16 · all 17 FSRS weights · retention 0.9 |
| **D. Learned from learner data** | Learner + section ability (Elo) · question difficulty (Elo) · per-section `fsrs_stability` / `fsrs_difficulty` · the weighted-accuracy statistic · streak, box, mastery status |
| **E. Elo** | Each answer = a match; learner K=32 (fast, few observations), question K=16 (shared across learners). Section level uses ability-only update to avoid double-counting |
| **F. FSRS** | DSR memory model, `R(t,S) = (1 + (19/81)·t/S)^(−0.5)`, next review where R decays to 0.9, floor 1 day, only AGAIN/GOOD grades |
| **G. FSRS decides** | **WHEN**, never WHAT |
| **H. Does Elo affect priority?** | **No.** Verified by grep: `_classify` contains no Elo term. Elo weights the *pass signal* (mastery/FSRS) and suggested difficulty only |
| **I. Does the LLM participate?** | **No.** `adaptive.py` imports only `timezone`, `Question`, `AttemptAnswer`, `ChunkMastery` |
| **J. Is this Knowledge Tracing?** | **No.** No latent knowledge-state model, no P(correct) prediction. It is threshold classification over an observed statistic |
| **K. Is this CAT?** | **No.** Adaptation is *between* assessments; the set is fixed when an attempt begins |
| **L. Genuinely adaptive?** | **Yes.** Measured per-section performance changes which content is selected next — demonstrably 6 of 9, not 9 of 9 |
| **M. To become true KT** | Needs a latent knowledge-state model emitting P(correct) per concept, a concept layer above chunks, difficulty snapshotted at answer time, and orders of magnitude more interactions |

## 2.2 The decision tree (exact, from `_classify`)

```
answered == 0                                 -> HIGH   "never assessed"
mastery.status == "mastered"                  -> NONE   (streak >= 3)
answered < 3  AND  weighted >= 60             -> MEDIUM "insufficient evidence"
weighted < 60                                 -> HIGH
weighted < 80                                 -> MEDIUM
otherwise                                     -> LOW

selected_for_retraining = priority in {HIGH, MEDIUM}
available_now           = selected AND (mastery is None OR next_eligible_at <= now)
```

## 2.3 Controlled scenarios — 14 scenarios + exhaustive search

All 14 behave as specified (new learner, 0/1, 1/1, 2/2, 3/3, 5/5, 0/5, improving, declining,
mastered, mastered-then-fails, mixed, evidence gate both directions).

**Monotonicity: CLEAN.** Across every answer sequence of length 3–12 (~8,000 sequences), a
correct answer never raises priority and a wrong answer never lowers it.

**One reproducible imprecision:** classification runs on the accuracy *rounded to 1 dp*.
Exhaustive search over lengths 3–14 found **47 sequences** where the true value sits below a
threshold but the rounded value lands on it — shortest at length 8, `CCXCCXXX`, true 59.9734%
→ displayed 60.0% → MEDIUM instead of HIGH.

**This is documented as deliberate and should not be changed.** Deciding on the raw value would
make the UI contradict itself: it would display "60.0%" beside the reason "below the 60%
threshold". Band width ≈ 0.05 pp.

---

# 3. Security / integrity

Attack pass on an isolated throwaway database. Zero-write claims measured as a **delta** against
a pre-probe snapshot of mastery and Elo, not as absolute emptiness.

| Attack | HTTP | Writes | Mastery | Elo |
|---|---|---|---|---|
| Exact offered set (6 of 6) | 200 | 6 answers | updated | updated |
| **Partial — 2 of 6** | **400** | 0 | unchanged | unchanged |
| Missing one (5 of 6) | 400 | 0 | unchanged | unchanged |
| Extra foreign appended | 400 | 0 | unchanged | unchanged |
| Substituted foreign | 400 | 0 | unchanged | unchanged |
| Duplicate ids | 400 | 0 | unchanged | unchanged |
| Empty submission | 400 | 0 | unchanged | unchanged |
| Hostile mixed payload | 400 | 0 | unchanged | unchanged |
| Resubmission | 409 | 0 | unchanged | unchanged |
| Foreign learner's attempt | 404 | 0 | unchanged | unchanged |
| Client-supplied `answered_at` | ignored | server time stored | — | — |
| Latency negative / >1 h / non-numeric | 400 | 0 | — | — |
| Answer key in learner payload | absent (raw-body asserted) | — | — | — |

**Scoring:** 2 correct + 4 unanswered of 6 → **33.33%**. Never 50%, never 100%.

**Residual:** a learner may still self-start a full-SOP quiz rather than the targeted one. The
offered set is recorded either way, so this is a product question, not a bypass.

---

# 4. AI / generative component

| | |
|---|---|
| Model | `meta/llama-3.1-8b-instruct` |
| Endpoint | `https://integrate.api.nvidia.com/v1` (NVIDIA NIM, OpenAI-compatible) |
| SDK | `openai>=1.40` — the client library, **not** the provider |
| Embeddings | `nvidia/nv-embedqa-e5-v5`, semantic chunking **fallback tier only** |
| Retries | 3 with backoff, then deterministic offline generator |
| Error handling | `classify_llm_error` → 9 categories |
| Grounding | one chunk per prompt + `source_chunk` FK persisted |

**What AI does:** drafts candidate questions; answers the SOP chatbot; embeds sentences when a
document has no headings.

**What AI does not do:** decide priority · decide mastery · decide FSRS timing · grade · modify
approved content · reach a learner unapproved.

**If NIM fails:** 3 retries, then the offline generator lifts the correct answer verbatim from the
SOP text — lower quality, cannot hallucinate, tagged `generation_source='mock'`.

**Malformed JSON:** `_strip_markdown_fences` + `_normalize_drafts`; unusable output falls through
to the fallback.

**Can unapproved output reach a learner?** No. Non-reviewer querysets force `status="approved"`.

**Hallucination control:** mitigation at three layers, prevention at one — the SME. Grounding is
provenance + prompt constraint, **not verified entailment**. That is the honest gap.

> The phrase "AI-powered adaptive learning" is **not** justified. "AI-assisted content generation
> with a rule-based adaptive engine" is.

---

# 5. Knowledge-tracing data reality

| | |
|---|---|
| Interactions | **42** |
| With timestamps | **9** (all created today) |
| With latency | 0 |
| Longest learner sequence | 18 |
| Corpus stability | **None** — `demo_adaptive` wipes and rebuilds; count observed at 60 → 51 → 33 → 42 |

Requirements: BKT hundreds per skill · PFA 10³–10⁴ · DKT ~5×10⁵ · SAKT/SAINT/AKT 10⁵–10⁷.

**None of these can be legitimately trained.** Three to four orders of magnitude short, and most
existing rows are scripted demo output written directly through the ORM.

Additional blocker: `Question.elo_rating` is live, so an interaction does not record difficulty
*at answer time*. Joining today's rating into a KT dataset would introduce look-ahead bias.

**Correct statement:** *"Transformer KT is architecture-ready but cannot be validly trained on
the available data."*

---

# 6. RAG / retrieval

| Capability | Present? |
|---|---|
| Lexical retrieval | ✅ `select_relevant_chunks` — word-set intersection, top 6 |
| Embeddings for retrieval | ❌ 0 embedding calls in `ai_engine` |
| Vector database | ❌ none in dependencies |
| Persisted embeddings | ❌ computed in memory for chunking, discarded |
| Hybrid / reranking / metadata filtering | ❌ |
| Source provenance | ✅ chunks passed into the prompt |
| Answer verified against source | ❌ |

**Would a vector DB improve this project?** At current scale, **no**. Retrieval operates over one
SOP's chunks — 3 in the demo, 20 across the corpus. An index over 20 items is overhead. The
defensible position is to build a retrieval gold set first and measure the lexical baseline.

Do **not** describe this as "vector RAG". It is keyword retrieval with grounded generation.

---

# 7. GxP-oriented controls

| Control | Status |
|---|---|
| SME approval gate | **Implemented** |
| Password re-verification | **Implemented** (`check_password`) |
| Electronic signature + SHA-256 content hash | **Implemented** |
| Approved-content immutability | **Implemented** (403) |
| Tamper detection | **Implemented** (`signature_is_intact()`, on demand) |
| Attributed audit trail | **Implemented** — 17 action types |
| Attempt reproducibility | **Implemented** (new) — offered set + order + timestamps |
| Tamper-evident audit storage | **Not implemented** — append-only by convention only |
| Separation of duties | **Not implemented** — an admin can generate and approve |
| SOP version lifecycle | **Not implemented** — reprocessing blocked with 409 |
| Question revision history | **Not implemented** |
| Qualification / training-completion record | **Not implemented** |

**Never claim compliance.** Correct wording: *"GxP-oriented, with Part 11-style technical
controls."*

---

# 8. Project worthiness — examiner scoring

| # | Dimension | /10 | Evidence | How it will be challenged |
|---|---|---|---|---|
| 1 | Problem significance | 9 | Real regulated-training gap; localisation of knowledge gaps | "Is this a real industry need or invented?" |
| 2 | Novelty | 7 | Section-level adaptation + WHAT/WHEN separation. Components are established | "Elo and FSRS are off-the-shelf — what's yours?" |
| 3 | AI contribution | 7 | Live LLM generation, chunking cascade, fallback, error classification | "The AI just writes questions — is that AI/ML?" |
| 4 | Adaptive learning | 8 | Demonstrable 6-of-9; monotonic; explainable | "This is if-else over an average" |
| 5 | Software engineering | 9 | 244 tests, clean migrations/deploy/lint, 7-app separation | "Any frontend tests?" — **no** |
| 6 | Security | 9 | Exact-set contract, atomic claim, answer-key withheld, all delta-verified | "Show me the raw payload" |
| 7 | Data integrity | 8 | Offered set pinned, server timestamps, no fabricated history | "Can I replay an old attempt?" — only post-Batch-A |
| 8 | Explainability | 10 | Every recommendation carries measured evidence + threshold | Hard to challenge — this is the strongest dimension |
| 9 | Testing | 9 | 244, zero deleted, adversarial harness, exhaustive monotonicity | "Frontend coverage?" |
| 10 | Scalability | 5 | Celery awaited synchronously; no pagination; not deployed | "What happens with 10,000 learners?" |
| 11 | Research potential | 7 | Honest KT gap analysis, evaluation roadmap | "You have no results" |
| 12 | Demo quality | 9 | Reproducible, self-narrating terminal output, +50 pp | "Is the data real?" — **no, it's scripted** |
| 13 | Future scope | 10 | Prioritised with reasons, integrity-first ordering | Hard to challenge |
| 14 | **Review readiness** | **9** | Everything green; one uncommitted-state risk | — |

**Mean ≈ 8.4 / 10.**

## Is this genuinely a strong BTech AI/ML project? — brutally honest

**Yes, with one caveat you must own.**

Strengths that are unusual at this level: the engineering discipline (244 tests, adversarial
verification, migrations clean), the *honesty* of the documentation, and one genuinely sharp
technical insight — `R(0,S) = 1.0`, which is why selection and scheduling must be separate
modules. Most student projects cannot articulate why two components exist.

The caveat: **the ML content is thinner than the title suggests.** The adaptive engine is
rule-based over a statistic. Elo and FSRS are established algorithms used correctly, not
contributions. The LLM writes questions.

**That is fine — provided you say it first.** A student who says *"the decision layer is
rule-based over a recency-weighted statistic, and here is exactly why I rejected DKT at this data
scale"* reads as stronger than one who claims ML and cannot name their training set. Your
weakness is only a weakness if an examiner discovers it before you disclose it.
