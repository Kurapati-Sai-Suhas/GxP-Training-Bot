# Adaptive Feature Gap Matrix

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


Every feature relevant to the core claim, classified honestly. "Verified" means the end-to-end
behaviour was executed or is pinned by a test that would fail without it — not that a model,
endpoint or UI element exists.

**Legend:** ✅ implemented + verified · 🟢 implemented + partially verified · 🟡 implemented but
weak · 🟠 partially implemented · 🔴 missing · 🔵 future scope

---

## Quiz generation

| Feature | Status | Evidence | Risk | Priority |
|---|---|---|---|---|
| TXT/MD ingestion | ✅ | `SopProcessTests` | — | — |
| PDF/DOCX ingestion | 🟢 | code present; only a corrupt-PDF failure test | Formats a real customer uploads have no positive-path test | P2 |
| Heading-aware chunking | ✅ | 5 chunking tests; heading tier fired in live demo | — | — |
| Semantic chunking fallback | 🟢 | tested with a mocked embedder | Never exercised against the real embedding API | P3 |
| Fixed-length fallback | ✅ | tested | Chunks may straddle topics, making `ChunkMastery` meaningless; not surfaced | P2 |
| LLM generation (live) | ✅ | mocked-provider suite + live demo run | — | — |
| Retry + backoff | ✅ | `test_provider_error_retries_three_times_then_falls_back` | — | — |
| Offline fallback | 🟡 | tested | Distractors drawn from **5 fixed templates**; unseeded `random` — not reproducible | P2 |
| JSON/schema validation | ✅ | 4 tests | — | — |
| Error classification | ✅ | 6 tests | — | — |
| **Source provenance recorded** | 🟢 | FK exists; reviewer UI shows passage | **No test asserts generation populates `source_chunk`** — the whole adaptive claim rests on it | **P0** |
| Exact-duplicate detection | ✅ | `test_generate_skips_duplicates_on_a_repeat_run` | — | — |
| **Semantic/near-duplicate detection** | 🔴 | — | Reworded duplicates pass; visible in demo output | P1 |
| Question quality scoring | 🔴 | — | No relevance/clarity/distractor check beyond the SME's eye | P2 |
| Difficulty control at generation | 🔴 | UI control was removed as dead; LLM self-assigns | Reviewer may ask how difficulty is set | P2 |
| Entailment verification | 🔵 | — | Grounding is prompt + provenance only | P3 |

## SME review

| Feature | Status | Evidence | Risk | Priority |
|---|---|---|---|---|
| Draft queue | ✅ | tested | — | — |
| Source passage visible | ✅ | verified via API: `source_text == SOPChunk.chunk_text` | — | — |
| Generation metadata visible | ✅ | confidence/source/Elo badges | — | — |
| Approve / reject + e-signature | ✅ | 14 tests | — | — |
| Signature bound to content hash | ✅ | 8 tests incl. tamper detection | — | — |
| Approved content immutable | ✅ | 6 tests, PUT and PATCH separately | — | — |
| Edit before approval | ✅ | tested | — | — |
| **Question revision history** | 🔴 | — | Reject→edit→re-approve destroys the superseded wording; past attempts unreconstructable | P1 |
| **Regenerate single question** | 🔴 | — | Reviewer expects it; only whole-batch regeneration exists | P2 |
| Reviewer comment / rationale | 🔴 | — | Audit records *that* they approved, not *what they checked* | P2 |
| Bulk review | 🔴 | — | 14 pending questions reviewed one at a time | P3 |

## Assessment integrity

| Feature | Status | Evidence | Risk | Priority |
|---|---|---|---|---|
| Answer key withheld | ✅ | 11 tests + raw-body check; re-verified this audit | — | — |
| Approved-only delivery | ✅ | server-side queryset | — | — |
| Server-side grading | ✅ | 4 tests | — | — |
| Single submission per attempt | ✅ | 10 tests, atomic compare-and-set | — | — |
| **Server-side quiz session** | 🔴 | — | No record of which questions were *offered*; results not reproducible | **P0** |
| **Submitted ids validated** | 🔴 | — | Arbitrary question ids accepted into `ChunkMastery` | **P0** |
| **Targeted selection enforced** | 🟠 | server computes ids; **client decides** whether to honour them | "Start Quiz" bypasses adaptation entirely, silently | **P0** |

## Adaptive learning

| Feature | Status | Evidence | Risk | Priority |
|---|---|---|---|---|
| Per-section mastery (`ChunkMastery`) | ✅ | 7 tests | — | — |
| Whole-SOP mastery (`TopicMastery`) | ✅ | 6 tests | — | — |
| Accuracy-driven priority | ✅ | 16 tests | — | — |
| Never-assessed handling | ✅ | 2 tests; was a real bug, fixed | — | — |
| Mastered exclusion | ✅ | tested | — | — |
| Unlinked-question bucket | ✅ | tested | — | — |
| Elo ability + difficulty | ✅ | 5 tests incl. double-move guard | — | — |
| Confidence-aware pass signal | ✅ | tested | — | — |
| Explainability endpoint | ✅ | 9 tests; matches engine exactly | — | — |
| Learning Path UI | ✅ | verified in browser with real data | — | — |
| **Recency weighting** | 🔴 | verified absent: 0/5→5/5 still HIGH while UI shows "Recent 100%" | **Self-contradicting screen; learner can't shed a weak label** | **P0** |
| **Sample-size confidence** | 🔴 | verified absent: 1/1 ≡ 50/50 | Standard EdTech objection with no answer | **P0** |
| **Priority ↔ FSRS reconciliation** | 🔴 | verified: HIGH + not-due ⇒ recommendation with no action | **Live-demo dead end** | **P0** |
| Difficulty-aware priority | 🔴 | verified absent: easy-miss ≡ hard-miss | Inconsistent — Elo weights mastery but not priority | P1 |
| Mastery decay over time | 🔴 | — | A year-old 90% still reads LOW forever | P1 |
| Cross-SOP prioritisation | 🔴 | — | Ranking is within-SOP; SOPs processed in due-date order | P2 |
| Within-quiz (CAT) adaptation | 🔵 | not a feature | Reviewer may expect it; defensible to decline | P2 |
| Prerequisite awareness | 🔵 | — | — | P3 |
| Learning-gain measurement | 🟠 | `retraining_improvement` (first vs latest, whole-SOP) | Not per-section; not surfaced to the learner | P2 |

## Spaced repetition

| Feature | Status | Evidence | Risk | Priority |
|---|---|---|---|---|
| FSRS-4.5 memory model | ✅ | 7 pure + 3 integration tests | — | — |
| Weak material sooner | ✅ | verified: 13 Aug vs 15 Aug from one attempt | — | — |
| Per-section scheduling | 🟡 | `ChunkMastery.next_eligible_at` is computed | **Never consumed** — only `TopicMastery` due-ness gates assignment | P1 |
| Grade granularity | 🟠 | AGAIN/GOOD only | No Hard/Easy signal exists to give it | P3 |
| Per-user FSRS optimisation | 🔵 | deliberately declined | Correct call at this data scale | — |

## GxP / traceability

| Feature | Status | Evidence | Risk | Priority |
|---|---|---|---|---|
| Question → chunk → SOP trace | ✅ | verified via API | `SET_NULL` loses it on chunk delete | P1 |
| Audit of all mutations | ✅ | 17 action types | — | — |
| Attempt history immutable | ✅ | resubmission blocked | No record of questions offered | P1 |
| **SOP version lifecycle** | 🔴 | reprocessing blocked (`409`) | Guard, not a workflow — **no way to revise a procedure** | P1 |
| **Training assignment/completion** | 🔴 | — | Cannot answer "is this person qualified?" | P1 |
| Tamper-evident audit | 🔴 | admin-UI guard only | No hash chain / WORM | P2 |
| Separation of duties | 🔴 | Admin can generate *and* approve | — | P2 |

---

## Summary

| Status | Count |
|---|---:|
| ✅ implemented + verified | 27 |
| 🟢 partially verified | 4 |
| 🟡 implemented but weak | 3 |
| 🟠 partially implemented | 4 |
| 🔴 missing | 17 |
| 🔵 future scope | 4 |

**Six P0 items**, all in two clusters:

1. **The adaptive engine's output is not binding** — no server-side quiz session, submitted ids
   unvalidated, targeting enforced by the browser.
2. **The adaptive metric is too crude to defend** — no recency, no sample-size confidence, and it
   can contradict its own scheduler in the UI.

Neither cluster means the engine is wrong. Both mean a reviewer can puncture the claim in one
question. Cluster 2 is cheap to fix; cluster 1 is a day's work.
