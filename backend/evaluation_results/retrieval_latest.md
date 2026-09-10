# Retrieval Baseline — lexical

Gold set `v1.0` · 14 scored queries · 0 skipped

## The headline finding

Largest candidate pool is **5 chunks**; the chatbot requests **6**. Retrieval therefore **never filters — it returns the whole document, reordered**.

## Metrics

| Metric | Value |
|---|---|
| Hit@1 | 0.7857 |
| Recall@1 | 0.75 |
| Recall@3 | 1.0 |
| Recall@5 | 1.0 (degenerate) |
| Precision@1 | 0.7857 |
| MRR | 0.8929 |

## By query category

| Category | n | Hit@1 | MRR |
|---|---|---|---|
| ambiguous_short | 1 | 1.0 | 1.0 |
| exact_terminology | 7 | 0.8571 | 0.9286 |
| multi_section | 2 | 0.5 | 0.75 |
| paraphrase | 3 | 0.6667 | 0.8333 |
| uncommon_terminology | 1 | 1.0 | 1.0 |

## Per-query

| ID | Category | Rank of first relevant | Expected | Top-1 retrieved |
|---|---|---|---|---|
| Q01 | exact_terminology | 2 | Section 2: Gowning Sequence | Section 1: Purpose |
| Q02 | paraphrase | 1 | Section 3: Time Limit | Section 3: Time Limit |
| Q03 | exact_terminology | 1 | Section 4: Glove Breach | Section 4: Glove Breach |
| Q04 | exact_terminology | 1 | Section 1: Purpose | Section 1: Purpose |
| Q05 | ambiguous_short | 1 | Section 2: Gowning Sequence | Section 2: Gowning Sequence |
| Q06 | exact_terminology | 1 | Section 2: Calibration Frequency | Section 2: Calibration Frequency |
| Q07 | uncommon_terminology | 1 | Section 3: System Suitability | Section 3: System Suitability |
| Q08 | exact_terminology | 1 | Section 4: Documentation | Section 4: Documentation |
| Q09 | paraphrase | 1 | Section 3: System Suitability | Section 3: System Suitability |
| Q10 | paraphrase | 2 | Section 2: Receipt Inspection | Section 1: Purpose |
| Q11 | exact_terminology | 1 | Section 3: Quarantine Labeling | Section 3: Quarantine Labeling |
| Q12 | multi_section | 2 | Section 1: Purpose, Section 2: Receipt Inspection | Auto chunk 1 |
| Q13 | multi_section | 1 | Section 2: Gowning Sequence, Section 3: Time Limit | Section 2: Gowning Sequence |
| Q14 | irrelevant | n/a (no relevant chunk) | (none) | Section 2: Receipt Inspection |
| Q15 | exact_terminology | 1 | Section 4: Documentation | Section 4: Documentation |

## Caveats

- **recall_at_5** — DEGENERATE. Every SOP here has at most 5 chunks and the chatbot requests 6, so retrieval returns the whole document. A value of 1.0 reflects the corpus size, not retrieval quality.
- **precision_at_k** — Reported at K=1 only. Most queries have a single relevant chunk, so Precision@3 is capped at 0.33 regardless of ranking quality.
- **sample_size** — 14 scored queries. Auditable, not statistically representative. No confidence intervals are computed and no result here is significance-tested.