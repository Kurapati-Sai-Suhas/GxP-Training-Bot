# FINAL GAP MATRIX

Measured 19 August 2026. Severity is relative to **a review tomorrow at 22:00**, not to an
ideal roadmap.

| ID | Gap | Evidence | Sev | Review impact | User impact | Effort | Migration | Risk | Recommendation |
|---|---|---|---|---|---|---|---|---|---|
| **P0-1** | **Batch A+B uncommitted; 2 migrations applied to the dev DB but untracked** | `git status`; `showmigrations` shows 0006/0007 `[X]` | **P0** | **High** — a stash/checkout would leave the schema ahead of the code and break the demo | None if untouched | 5 min | Already applied | **Low if committed, high if disturbed** | **COMMIT before the review** (see §1) |
| P1-1 | Frontend has no automated tests | No test runner in `package.json` | P1 | Medium — likely asked | None | Days | No | — | **Future scope.** Say: verified by lint, build and manual checks |
| P1-2 | `demo_adaptive` wipes and rebuilds the corpus | Interaction count 60→51→33→42 across runs | P1 | Medium — undermines "dataset" claims | None | 2 h | No | Medium | **Do not touch.** Disclose: demo data is a fixture, not a dataset |
| P2-1 | Classification uses the rounded accuracy | 47 reachable sequences; shortest len 8 | P2 | Low | Negligible (0.05 pp band) | 30 min | No | Medium | **Do not fix.** Deliberate — raw value would make the UI self-contradict |
| P2-2 | Difficulty does not enter adaptive priority | grep: no Elo term in `_classify` | P2 | Medium — a sharp examiner may spot it | Low | 2 h | No | **Medium** — changes the core metric | **Do not touch before review.** It's a better answer than a patch |
| P2-3 | No SOP version lifecycle | Reprocess returns 409 | P2 | Medium | **High** — no revision path | Sprint | **Yes** | **High** | **Future scope.** Only gap that can destroy learner data |
| P2-4 | Grounding is provenance, not entailment | No NLI/judge step | P2 | Medium | Medium | High | No | High | **Future scope** |
| P2-5 | No BKT/IRT/neural KT | No model in repo | P2 | **High** — "where's the ML?" | None | Sprint+ | No | High | **Future scope.** Justify by data: 42 interactions |
| P2-6 | Deduplication lexical only | `token_similarity` | P2 | Low | Low | Medium | No | Medium | Future scope |
| P2-7 | Audit not tamper-evident | No hash chain / WORM | P2 | Low | Medium | Medium | Maybe | Medium | Future scope |
| P2-8 | No separation of duties | Admin can generate and approve | P2 | Low | Medium | Low | No | Low | Future scope — mention proactively |
| P2-9 | No concept layer | `ChunkMastery` keyed on chunk | P2 | Low | Medium | High | Yes | High | Future scope |
| P2-10 | Difficulty not snapshotted at answer time | `Question.elo_rating` is live | P2 | Low | None today | Low | Yes | Medium | **Future scope** — but required before any KT dataset (look-ahead bias) |
| P2-11 | Celery awaited synchronously | `.delay().get()` | P2 | Low | Low | Medium | No | Medium | Future scope — say "process isolation, not async" |
| P2-12 | Frontend latency telemetry not wired | 0 rows with latency | P2 | Very low | None | 1 h | No | Low | Future scope — backend accepts and validates it |
| P3-1 | Self-start offers the whole SOP | `perform_create` | P3 | Low | By design | — | — | — | **Do not touch.** Legitimate; the set is recorded either way |
| P3-2 | Questions approved mid-attempt are excluded | Offered set fixed at creation | P3 | Very low | By design | — | — | — | **Do not touch.** Correct behaviour |
| P3-3 | Not deployed | No running stack | P3 | Low | — | High | — | — | Say "Docker and CI written, not executed as stacks" |
| P3-4 | 401s on frontend mount | Console | P3 | Very low | Cosmetic | 1 h | No | Low | **Do not touch** the night before |

---

## §1 — The only P0, and what to do about it

**Problem.** Batch A+B (offered-set binding + interaction timestamps) is fully implemented,
tested at 244 passing, adversarially verified and browser-verified — but it is **uncommitted**,
and its two migrations are **applied to the development database while untracked**.

**Why this matters tomorrow.** The demo depends on that schema. If anything caused the tree to
revert to `HEAD` — an accidental `git checkout`, `git stash`, cloning fresh onto another machine
— the database would contain `QuizAttemptQuestion` and `answered_at`, but the code would not know
they exist, and `submit()` would fail. Working from an uncommitted tree the night before a
demonstration is the single largest avoidable risk in this repository.

**Recommendation: commit it.** It is verified to a higher standard than anything else in the
project:

- 244 tests passing, 0 failures, +23 added, none deleted or weakened
- Migration drift clean; deploy check clean; lint 0/0; build OK
- Demo output byte-identical to the pre-change baseline
- 13-probe adversarial pass with delta-verified zero-writes
- Browser-verified: attempt created, 9 questions rendered, submitted 200, scored "0 of 9"

There is no *technical* argument for leaving it uncommitted; the argument would only be "don't
change anything before a review", and committing already-verified work does not change behaviour.

**Do not push** unless you want it on GitHub tonight — committing locally is enough to remove the
risk.

---

## Answering the brief's five originally-stated limitations

| Original limitation | Status now |
|---|---|
| 1. Offered-question set not persisted | ✅ **CLOSED** — `QuizAttemptQuestion`, exact-set contract, 13 tests |
| 2. SOP version lifecycle | ❌ Open — P2-3, future scope |
| 3. Difficulty does not influence priority | ❌ Open — P2-2, deliberately deferred |
| 4. Grounding not entailment-verified | ❌ Open — P2-4, future scope |
| 5. No BKT / IRT / advanced KT | ❌ Open — P2-5, **blocked by data**, not by effort |

**New gaps introduced by Batch A+B:** none functional. Two behavioural consequences are by
design and documented (P3-1, P3-2).
