# Implementation Master Checklist

Machine-checkable status for the architecture upgrade programme.

**Rule:** a box becomes `[x]` only when implementation exists, tests exist, tests pass, manual
verification is done where needed, documentation is updated, and acceptance criteria are met.
Every completed item carries Evidence / Test / Commit / Date.

**Roadmap order (approved 19 Aug 2026):**
INTEGRITY → INSTRUMENTATION → MEASUREMENT → ARCHITECTURE → KT MODEL

---

## PHASE 0 — Baseline freeze

- [x] **P0-000** Freeze verified baseline
  - Evidence: `docs/UPGRADE_BASELINE.md` — commit `06b6e95`, 221 tests, 0 failures, migrations
    clean, deploy clean, lint 0/0, demo verified
  - Test: full suite + migration + deploy + lint + build + `demo_adaptive`
  - Commit: *(uncommitted)* · Date: 2026-08-15

---

## BATCH A — Quiz integrity

- [x] **P0-001** Audit quiz session integrity against current code
  - Evidence: verified `QuizAttempt`/`AttemptAnswer` models, both attempt-creation paths, submit
    flow, and the frontend's client-side set assembly. Confirmed the browser decided the offered set
  - Date: 2026-08-19
- [x] **P0-002** Implement server-side offered-question binding
  - Evidence: `QuizAttemptQuestion` (attempt, question, position, created_at, unique_together);
    persisted on both creation paths; non-destructive on reuse
  - Test: `OfferedQuestionSetTests::test_a1_*`, `test_a18_*`, `test_a18b_*`
  - Date: 2026-08-19
- [x] **P0-003** Exact-set submission contract
  - Evidence: `SET(submitted) == SET(offered)`, equal length, no duplicates; rejects partial,
    missing, extra, substituted, duplicate, empty
  - Test: `test_a2` … `test_a8b`, `test_adversarial_mixed_payload_rejected_with_zero_writes`
  - Date: 2026-08-19
- [x] **P0-004** Score against the full offered set
  - Evidence: denominator is `offered_count`; 2 correct + 4 unanswered of 6 → 33.33%
  - Test: `test_a10_score_denominator_is_the_full_offered_set`
  - Date: 2026-08-19
- [x] **P0-005** Zero-write guarantee on invalid submission
  - Evidence: all validation precedes the atomic claim; no answers, no `TopicMastery`, no
    `ChunkMastery`, no Elo movement, attempt not consumed. Delta-verified adversarially
  - Test: `_assert_nothing_written()` in every rejection test
  - Date: 2026-08-19
- [x] **P0-006** Preserve resubmission and learner-isolation guarantees
  - Evidence: atomic compare-and-set retained; 409 on resubmit, 404 on foreign attempt
  - Test: `test_a16_*`, `test_a17_*`
  - Date: 2026-08-19
- [x] **P0-007** Data migration for in-flight attempts
  - Evidence: `0007_backfill_offered_questions` — 31 rows across 5 of 6 incomplete attempts;
    completed attempts untouched; reversible
  - Date: 2026-08-19
- [x] **P0-008** Frontend conforms to the server contract
  - Evidence: both quiz-start paths render `offered_question_ids`; no fall-back to a client-built
    set. Browser-verified: 9-question set rendered from the server response
  - Test: lint 0/0, build OK, manual browser check
  - Date: 2026-08-19

---

## BATCH B — KT-ready instrumentation

- [x] **P0-010** Add `answered_at` (server-authoritative)
  - Evidence: nullable, set in code (not `auto_now_add`), timezone-aware, one reading per submission;
    client-supplied values ignored
  - Test: `InteractionTelemetryTests::test_b1_b2_*`, `test_b3_*`
  - Date: 2026-08-19
- [x] **P0-011** Add `response_latency_ms` with validation
  - Evidence: nullable; rejects negative, >1 h, non-numeric; documented as untrusted telemetry
  - Test: `test_b4_b7_*`, `test_b5_*`, `test_b6_*`, `test_b6b_*`
  - Date: 2026-08-19
- [x] **P0-012** Do not fabricate historical temporal data
  - Evidence: 0 of 51 pre-existing rows backfilled; `demo_adaptive` output left NULL as a synthetic
    marker
  - Test: `test_b8_*`
  - Date: 2026-08-19
- [x] **P0-013** Event-model design decision recorded
  - Evidence: `BATCH_A_B_REPORT.md` §2 — extend `AttemptAnswer`; separate event table rejected with
    reasons
  - Date: 2026-08-19
- [x] **P0-014** KT sequence reconstructable
  - Evidence: (time, question, concept, correctness) ordered by `answered_at`
  - Test: `test_b9_b10_*`, `test_kt_sequence_can_be_reconstructed_*`
  - Date: 2026-08-19
- [x] **P0-015** KT data readiness reported honestly
  - Evidence: `docs/KT_DATA_READINESS.md` — 33 interactions, 0 timestamped, three data classes,
    demo corpus unstable
  - Date: 2026-08-19
- [x] **P0-016** Snapshot question difficulty and learner ability at answer time
  - Problem: `Question.elo_rating` and `TopicMastery.elo_rating` are live and move *because of*
    the answer being recorded. Reconstructing a past interaction from current ratings feeds the
    outcome back into its own feature — look-ahead bias — and would inflate the apparent accuracy
    of any model trained on it. Identified during the roadmap-to-code reconciliation as the one
    remaining irreversible-if-delayed instrumentation item, i.e. Batch B was incomplete.
  - Evidence: `AttemptAnswer.question_difficulty_at_answer` / `.learner_ability_at_answer`
    (FloatField, nullable, matching the existing `elo_rating` representation), captured in
    `_grade_and_record` **before** `apply_elo_update()` runs. Verified live: snapshot 1500/1500
    while the ratings moved to 1492/1516, and still 1500 after four further answers drove the
    live rating to 1465.09
  - Test: `attempts.tests.EloSnapshotTests` (8 tests, covering pre-update capture, carried-forward
    ability, immutability under drift, NULL history, forged client values, KT-tuple
    reconstruction, and zero-write on rejected submissions)
  - Migration: `0008_attemptanswer_learner_ability_at_answer_and_more` — additive, nullable,
    **no backfill**
  - Regression: 244 → 252 tests, 0 failures; `adaptive.py`, `fsrs.py` and `services.py` have zero
    uncommitted changes; demo output byte-identical
  - Commit: *(uncommitted)* · Date: 2026-08-19

### Batch A+B regression

- [x] **P0-020** Full verification after change
  - Evidence: 244 tests (221 → 244), 0 failures, 0 skipped; migrations clean; deploy clean;
    lint 0/0; build OK; **demo byte-identical** (6 of 9, +50.0 pp, 87.9% vs 75.0%)
  - Date: 2026-08-19
- [x] **P0-021** Documented every modified existing test
  - Evidence: `BATCH_A_B_REPORT.md` §4 — 11 call sites narrowed via `_offer_exactly`, intent
    preserved; `SubmissionValidationTests` deliberately not narrowed
  - Date: 2026-08-19

---

## BATCH C+ — Data pipeline separation

- [x] **P1-010** Separate synthetic demo data from real learner interactions
  - Problem: Batch C showed `demo_adaptive` output was indistinguishable from real interactions
    in evaluation. Its outcomes are decided by a script, so counting them as evidence about
    learning would be the easiest possible way to produce a meaningless result
  - Evidence: `QuizAttempt.is_synthetic` (default False, indexed); only `demo_adaptive` sets it
    True; `evaluation.load_interactions` excludes it by default and counts the exclusion; an
    `include_synthetic` flag remains for mechanism checks. Verified live: harness reports
    `27 synthetic demo, 24 no timestamp`, previously conflated as one bucket
  - Scope note: `demo_adaptive` deletes only `SOP-DEMO` and its demo users, so real learner data
    on other SOPs was never at risk. The problem was identification, not destruction — so the
    smallest correct fix is a marker, not a redesign of the demo
  - Test: `SyntheticDataSeparationTests` (5)
  - Migration: `0009_quizattempt_is_synthetic` — additive, defaulted, no backfill
  - Commit: *(uncommitted)* · Date: 2026-08-19

---

## BATCH C — Measurement

- [x] **P1-001** Build offline evaluation harness (temporal splits, no leakage)
  - Evidence: `attempts/evaluation.py` + `manage.py evaluate_adaptive`. Per-learner temporal
    split on `answered_at`; expanding-window features (row i sees 0..i-1 only); rows without a
    timestamp or Elo snapshot excluded, never imputed. Read-only — writes no application tables
  - Test: `EvaluationHarnessTests` — ordering, history-excludes-future, exclusion counts,
    per-learner split, duplicate handling
  - Date: 2026-08-19
- [x] **P1-002** Baseline: current rule-based engine
  - Evidence: `CurrentAdaptiveEngineBaseline` — an *adapter* that calls
    `adaptive.weighted_accuracy` and `adaptive._classify` directly. `adaptive.py` is imported,
    never modified or duplicated. Abstains to the training base rate when a chunk has no history
  - Test: `test_12_*`, `test_12b_*`
  - Date: 2026-08-19
- [x] **P1-003** Baseline: Elo-only predictor
  - Evidence: `EloBaseline` uses `services._expected_score` on the stored pre-answer snapshots
  - Test: `test_13_*`
  - Date: 2026-08-19
- [x] **P1-004** Baseline: logistic / PFA-style
  - Evidence: `LogisticBaseline` — 4 features, L2, gradient descent, pure Python; standardisation
    fitted on the training split only; zero-variance columns guarded against NaN
  - Test: `test_14_*`, `test_14b_*`
  - Date: 2026-08-19
- [x] **P1-005** Metrics: ROC-AUC, PR-AUC, log loss, Brier, calibration
  - Evidence: pure Python (no numeric stack in the project). Undefined cases return
    `insufficient_data` rather than a default; calibration bins below `min_per_bin` report their
    count and abstain
  - Test: `test_10_*`, `test_10b_*` (verified against hand-computable cases), `test_11_*`
  - Date: 2026-08-19
- [x] **P1-009** Data-sufficiency gate
  - Evidence: thresholds derived from the standard error of the statistics involved, not from
    what the current dataset contains — 100 evaluation interactions, 20 per class, 5 learners.
    Current run reports `INSUFFICIENT_DATA` with named blockers
  - Test: `test_9_*`, `test_9b_*`
  - Date: 2026-08-19
- [x] **P1-006** Build a retrieval gold set for the SOP chatbot
  - Evidence: `ai_engine/retrieval_gold_set.json` — 15 queries over 14 chunks in 3 SOPs,
    manually labelled with a stated rationale per query. Keyed on `(sop_code, section_title)`
    rather than chunk ids, so it survives a reseed. Categories: exact_terminology, paraphrase,
    uncommon_terminology, ambiguous_short, multi_section, irrelevant
  - Exclusions recorded in the file: SOP-DEMO (wiped by `demo_adaptive` every run),
    three single-chunk SOPs (retrieval trivial with one candidate), SOP-198 (never processed)
  - Honesty: the file states it is an evaluation fixture over **seed** content, not production
    data, and is auditable rather than statistically representative. No confidence intervals
  - Test: `RetrievalGoldSetTests` (8) — schema, determinism, unique ids, synthetic exclusion,
    honesty markers present, category coverage, multi-relevant support, empty relevant set
  - Date: 2026-08-19
- [x] **P1-007** Evaluate current lexical retrieval as the retrieval baseline
  - Evidence: `ai_engine/retrieval_evaluation.py` + `manage.py evaluate_retrieval`. Calls the
    production `select_relevant_chunks` unmodified. Results in
    `evaluation_results/retrieval_latest.{json,md}`
  - **Measured (14 scored queries): Hit@1 0.786 · Recall@1 0.75 · Recall@3 1.0 ·
    Precision@1 0.786 · MRR 0.893.** By category — exact_terminology 0.857 Hit@1,
    paraphrase 0.667, multi_section 0.5
  - **Headline finding: retrieval never filters.** Largest SOP has 5 chunks; the chatbot
    requests 6. `select_relevant_chunks` returns the whole document, reordered. Recall@5 is
    therefore 1.0 *by construction* and is reported as degenerate, not as success. Only
    rank-sensitive metrics are meaningful today
  - Failure modes diagnosed by measurement, not inspection: (1) no term weighting — "must"
    and "what" score the same as "tailing"; (2) ties broken by document order, so a generic
    earlier chunk beats a specific later one; (3) title-only chunks outrank content because
    queries echo the document title
  - Test: `RetrievalMetricTests` (6) + `RetrievalBaselineTests` (6) — metric arithmetic against
    hand-computable cases, provenance preservation, title-keying, unresolvable labels skipped
    rather than scored zero, degeneracy flagged, irrelevant query excluded from scoring
  - Limitation: 14 scored queries over seed content. Establishes a baseline to compare against;
    it does not establish that retrieval is adequate in general
  - Date: 2026-08-19

> **No model performance claim is made or possible.** The harness is verified; the dataset is
> below every sufficiency threshold. See `evaluation_results/latest.md`.
- [x] **P1-008** Snapshot question difficulty at answer time — *completed early as P0-016;
      it was instrumentation, and delaying it would have kept producing unusable interactions*

---

## BATCH D — Document / retrieval architecture *(blocked on C)*

- [ ] **P2-001** Evaluate chunking strategies against the gold set
- [ ] **P2-002** Vector database decision (**pgvector evaluated first — Postgres already in use**)
- [ ] **P2-003** Embedding model decision with benchmark evidence
- [ ] **P2-004** Embedding persistence + model/dimension/version metadata *(closes L13)*
- [ ] **P2-005** Hybrid retrieval + reranking, **only if it beats the lexical baseline**
- [ ] **P2-006** Semantic duplicate detection *(closes L6)*
- [ ] **P2-007** Entailment verification of generated answers *(closes L4)*

---

## BATCH E — Adaptive / KT *(blocked on C and data volume)*

- [ ] **P3-001** Knowledge-tracing literature review
- [ ] **P3-002** Concept / knowledge-component layer *(closes L15)*
- [ ] **P3-003** Candidate KT architectures proposed and ranked
- [ ] **P3-004** Candidate implemented and evaluated against baselines
- [ ] **P3-005** Adopt a KT model **only if it beats the current engine on the harness**
- [ ] **P3-006** Cold-start policy
- [ ] **P3-007** Adaptive policy separated from KT prediction
- [x] **P3-008** Difficulty-aware adaptive priority *(closes L3)*
  - Problem: Elo weighted the *pass signal* driving mastery and FSRS, but not the accuracy
    driving *priority*. An easy question missed and a hard question missed contributed
    identically to whether a section was called weak — the two halves of the system disagreed
    about what "hard" meant
  - Evidence: `adaptive.difficulty_weight()` maps a recorded difficulty onto the same 1.0–2.0
    range the pass signal already used; `weighted_accuracy(sequence, difficulties)` multiplies
    each recency weight by it. Uses `AttemptAnswer.question_difficulty_at_answer` — the value
    recorded with the answer — **not** the question's live rating, which moves whenever any
    learner answers and would make a past decision irreproducible. Answers with no recorded
    difficulty weight 1.0, so pre-instrumentation history is unchanged
  - Explainability: the reason string reads "recency- and difficulty-weighted" only when
    difficulty was genuinely used; `difficulty_weighted` and `mean_difficulty` are exposed on
    every section and shown in the learning-path card
  - Test: `DifficultyAwarePriorityTests` (14) + `DifficultyAwarePriorityIntegrationTests` (3) —
    hard vs easy miss and success, band change, weak stays HIGH, strong never promoted,
    MIN_EVIDENCE unchanged, recency unchanged, mastery unchanged, never-assessed unchanged,
    unlinked questions unchanged, reason naming, learning-path agreement
  - Regression: 272 → 294 tests, 0 failures. Three existing recency tests updated for a
    genuine 0.2 pp semantic shift (documented inline); demo output byte-identical
  - Commit: *(uncommitted)* · Date: 2026-08-19

---

## BATCH F — Integrity / lifecycle *(scheduled)*

- [ ] **P3-020** SOP version lifecycle *(closes L2)*
- [ ] **P3-021** Question revision history
- [ ] **P3-022** Tamper-evident audit storage *(closes L7)*
- [ ] **P3-023** Separation of duties *(closes L8)*
- [ ] **P3-024** Frontend automated test suite *(closes L9)*

---

## BATCH G — Agentic *(not justified yet)*

- [ ] **P4-001** Determine whether an agent measurably improves outcomes
- [ ] **P4-002** Agent architecture with tool constraints, audit trail, human approval

> **[NOT IMPLEMENTED — DEFERRED]** Agentic orchestration.
> Reason: no evidence it improves any measurable outcome, and no evaluation harness exists to
> demonstrate that it does. It would also sit above compliance controls that must stay
> deterministic. Revisit after Batch C, and only with evidence.

---

## Summary

| Batch | Items | Complete |
|---|---|---|
| Phase 0 | 1 | 1 |
| Batch A | 8 | 8 |
| Batch B | 9 | 9 |
| Batch C | 9 | 9 |
| Batch C+ | 1 | 1 |
| Batches D–G | 23 | 2 |
| **Total** | **51** | **30** |

Nothing in this checklist has been committed or pushed.
