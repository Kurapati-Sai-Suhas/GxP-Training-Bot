# Adaptive Evaluation Report

**Generated:** 2026-08-19T11:52:42.425985+05:30
**Status:** `INSUFFICIENT_DATA`

> ## ⚠️ NO VALID MODEL PERFORMANCE CLAIM CAN CURRENTLY BE MADE
>
> The harness ran correctly end to end. The dataset is below the documented
> sufficiency thresholds, so any metric below reflects the mechanism working, not
> the quality of a model. Blockers:
>
> - evaluation split has 0 interactions, below the 100 required
> - minority class has 0 observations, below the 20 required
> - 0 learners with usable sequences, below the 5 required

## Dataset

| Quantity | Value |
|---|---|
| Total interaction rows | 51 |
| Usable (temporally valid) | 0 |
| Excluded — synthetic demo data | 27 |
| Excluded — no `answered_at` | 24 |
| Excluded — no Elo snapshot | 0 |
| Excluded — sequence too short | 0 |
| Learners with usable sequences | 0 |
| Train / evaluation interactions | 0 / 0 |
| Evaluation positives / negatives | 0 / 0 |

## Split

- **Type:** per-learner temporal
- **Ordering field:** `answered_at`
- **Train fraction:** 0.7
- No random splitting. A learner's later answers are never used to predict their earlier ones.

## Leakage controls

- Features for interaction i are built from interactions 0..i-1 only.
- Difficulty and ability come from AttemptAnswer snapshots captured before the Elo update, never from the live Question/TopicMastery ratings.
- Rows without answered_at or without snapshots are excluded, never imputed.
- Synthetic demo interactions (QuizAttempt.is_synthetic) are excluded by default.
- The target (is_correct) is never exposed to any baseline as an input.

## Baselines

### `current_rule_engine`

Rule-based decision layer over recency-weighted accuracy (half-life 5.0, MIN_EVIDENCE 3). Not a trained model.

**Predicts:** P(correct) read from the engine's own recency-weighted accuracy for that chunk

| Metric | Value |
|---|---|
| roc_auc | insufficient_data |
| pr_auc | insufficient_data |
| log_loss | insufficient_data |
| brier | insufficient_data |

> Computed for mechanism verification only. NOT a performance claim -- the dataset is below the documented sufficiency thresholds.

### `elo`

Elo expected score from pre-answer ability and difficulty snapshots.

**Predicts:** P(correct) = 1 / (1 + 10^((difficulty - ability)/400))

| Metric | Value |
|---|---|
| roc_auc | insufficient_data |
| pr_auc | insufficient_data |
| log_loss | insufficient_data |
| brier | insufficient_data |

> Computed for mechanism verification only. NOT a performance claim -- the dataset is below the documented sufficiency thresholds.

### `logistic_pfa`

Logistic regression on prior counts and pre-answer snapshots. PFA-style, not BKT and not knowledge tracing.

**Predicts:** P(correct | prior correct, prior incorrect, difficulty, ability)

| Metric | Value |
|---|---|
| roc_auc | insufficient_data |
| pr_auc | insufficient_data |
| log_loss | insufficient_data |
| brier | insufficient_data |

> Computed for mechanism verification only. NOT a performance claim -- the dataset is below the documented sufficiency thresholds.

## What this report is and is not

- **Implemented:** temporal extraction, leakage-safe features, per-learner temporal split, three baselines, four metrics, calibration, sufficiency gating.
- **Measured:** only what the Dataset table states.
- **Not yet possible:** any claim that one approach outperforms another, until the sufficiency thresholds are met with real learner data.

No knowledge-tracing model is implemented. Elo and the logistic baseline are conventional predictors used for comparison, not knowledge tracing.