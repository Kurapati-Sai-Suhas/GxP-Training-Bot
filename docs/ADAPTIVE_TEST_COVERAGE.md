# Adaptive Learning — Test Coverage Map

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


176 tests pass. That number is not the question. The question is **which adaptive behaviours
are actually pinned down**, and which would break silently.

---

## Lifecycle coverage

| Scenario | Tested? | Test | Gap |
|---|---|---|---|
| Correct answer raises mastery | ✅ | `test_correct_section_strengthens_while_wrong_sections_stay_weak` | — |
| Incorrect answer keeps section weak | ✅ | same | — |
| Never assessed | ✅ | `test_a_section_never_yet_assessed_is_still_offered_for_training`, `test_untouched_sections_are_reported_as_never_assessed` | — |
| Mixed performance in one attempt | ✅ | `test_every_answered_section_gets_its_own_mastery_row` | — |
| Mastered section excluded | ✅ | `test_mastering_a_section_removes_it_from_retraining` | — |
| Weak section selected | ✅ | `test_retraining_targets_only_the_weak_sections` | — |
| Reassessment improves state | ✅ | `test_reassessment_improves_the_weak_section_state`, `test_improvement_is_visible_after_retraining` | — |
| Cross-section selection | ✅ | `test_auto_assigned_targets_unmastered_section_questions_first` | — |
| One section's miss doesn't reset another | ✅ | `test_a_miss_in_one_section_does_not_reset_an_already_strong_section` | — |
| FSRS scheduling | ✅ | `FSRSAlgorithmTests` (7), `AdaptiveFSRSSchedulingTests` (3) | — |
| FSRS brings weak material back sooner | ✅ | `test_weak_sections_are_scheduled_sooner_than_the_strong_one` | — |
| Elo moves once per answer | ✅ | `test_question_elo_rating_moves_exactly_once_per_answer_not_twice` | — |
| Questions with no source chunk | ✅ | `test_questions_without_a_source_chunk_create_no_chunk_mastery` | — |
| Explainability matches engine | ✅ | `LearningPathExplainabilityTests` (9) | — |
| Learner scoping of path | ✅ | `test_path_is_scoped_to_the_requesting_learner` | — |
| **`source_chunk` set by generation** | ❌ | — | **D3 — highest-value missing test** |
| **Small sample size** | ❌ | — | 1/1 treated as 50/50 |
| **Recency / improvement trend** | ❌ | — | improved learner still HIGH |
| **Difficulty affects priority** | ❌ | — | not implemented; untested either way |
| **Priority ↔ FSRS disagreement** | ❌ | — | D2 — verified broken, no test |
| **Learning-path vs auto-assigned consistency** | ❌ | — | D2 — no test compares them |
| **Targeted ids enforced server-side** | ❌ | — | D1 — client-enforced only |
| **Submitted question ids validated** | ❌ | — | arbitrary ids accepted |
| **SOP reprocessing vs mastery** | ⚠️ | `test_reprocessing_is_blocked_once_approved_questions_exist` | only the *blocked* path; draft-only reprocess still wipes `ChunkMastery`, untested |
| **PDF / DOCX extraction (positive path)** | ❌ | — | only `.txt` and a corrupt-PDF failure case |
| Two learners diverge | ⚠️ | `test_auto_assigned_scoped_to_requesting_learner` | proves isolation, not *different content* |
| Within-quiz adaptation | n/a | — | not a feature (by design) |

---

## Where the 176 actually sit

| App | Tests | Adaptive-relevant |
|---|---:|---:|
| `attempts` | 63 | ~45 |
| `quiz` | 36 | ~4 |
| `ai_engine` | 27 | ~2 |
| `sops` | 19 | ~5 |
| `accounts` | 16 | 0 |
| `analytics` | 10 | ~3 |
| `audit` | 5 | 0 |

Roughly **59 of 176** tests touch the adaptive lifecycle. The rest are security, RBAC, and
infrastructure — valuable, but they do not defend the project's central claim.

---

## The four tests I would write first

Not written (this is an audit), listed in value order:

1. **`test_generation_links_questions_to_their_source_chunk`** — assert that
   `generate_quiz_task` produces questions with `source_chunk` populated. Without it, the FK the
   entire adaptive claim depends on could break and every adaptive test would still pass,
   because they all set `source_chunk` by hand in fixtures. *(Gap D3)*

2. **`test_learning_path_and_auto_assigned_agree`** — assert that a section marked
   `selected_for_retraining` is actually offered by `auto-assigned`, or that the learner is told
   why not. Currently they can contradict each other. *(Gap D2)*

3. **`test_submit_rejects_questions_outside_the_attempt_sop`** — assert that a crafted payload
   cannot feed foreign question ids into `ChunkMastery`. *(Gap D1)*

4. **`test_reprocessing_a_draft_only_sop_preserves_or_warns_about_mastery`** — pin the behaviour
   of the one reprocessing path that is still destructive.

---

## Honest assessment of test quality

**Genuinely strong:** the tests assert behaviour rather than implementation, carry docstrings
explaining the scenario, cover permission boundaries from both directions, and several are named
for the specific bug they prevent. The three adaptive bugs fixed in the previous sprint were
found by writing the controlled scenario *first* and watching it fail — that is the right order.

**The systematic weakness:** adaptive tests build their fixtures by hand
(`Question.objects.create(..., source_chunk=chunk)`) rather than by running generation. This
tests the *consumer* of the grounding link thoroughly while never testing its *producer*. It is
exactly the shape of coverage that stays green while the feature breaks.
