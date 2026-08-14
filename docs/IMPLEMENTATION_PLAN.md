# Implementation Plan
## GxP Training Bot — hardening sprint

**Baseline:** commit `d783815` · **89 backend tests, 0 failures, 0 errors, 0 skipped** ·
working tree clean apart from untracked audit documents.

**Source of truth:** [`GAP_ANALYSIS.md`](GAP_ANALYSIS.md) (45 findings) and [`SRS.md`](SRS.md).

**Guiding principle:** correctness > security > data integrity > auditability > reliability >
core GxP workflow > UX polish > optional features.

---

## Scope decision

The full 27-phase brief is a multi-day programme. This sprint executes the **P0 band in full**
— every finding that compromises assessment integrity, record integrity, or access control —
because those are the items that decide whether the system's training records mean anything.
Larger architectural phases are deliberately deferred rather than half-built; §"Deferred" below
records that decision explicitly so nothing is left looking done when it is not.

---

## In scope this sprint

| # | Item | Gap ID | Pri | Phase |
|---|---|---|---|---|
| 1 | Learner-facing API must never expose the answer key | A1 | **P0** | 1 |
| 2 | Completed attempts cannot be resubmitted | A2 | **P0** | 2 |
| 3 | Approved/e-signed questions immutable via ordinary edit | E4 (part) | **P0** | 4 |
| 4 | Bind the e-signature to a content hash | GXP-2 | **P0** | 5 |
| 5 | Audit every destructive/mutating operation | E4 | **P0** | 6 |
| 6 | Uploaded SOP media requires authentication | E1 (part) | **P0** | 7 |
| 7 | `dashboard-summary` cross-learner data leak | E3 | **P0** | 8 |
| 8 | Rate limiting on login and e-signature | E2 | **P0** | 10 |
| 9 | Production configuration split (`DEBUG=False`, gunicorn) | E1 | **P1** | 9 |
| 10 | Regression tests for all of the above | B5-adjacent | **P0** | 23 |
| 11 | Documentation: `SECURITY.md`, `TESTING.md`, `CHANGELOG.md`, README | — | **P1** | 27 |

---

## Deferred — and why

These are **not** started. Each is a genuine architectural change that cannot be done safely in
the remaining budget, and a half-migration would leave the data model in a worse state than the
current, coherent one.

| Item | Gap | Why deferred |
|---|---|---|
| SOP version lifecycle (Phase 3/16) | C4, A3 | Requires a new `SOPVersion` entity and a data migration over live `Question`/`ChunkMastery` rows. Partially mitigated this sprint: reprocessing is now blocked once approved content exists (see item 3 below). |
| Training assignment/completion model (Phase 17) | C2, C3 | New models, new endpoints, new UI. Genuinely the largest missing *product* capability, but additive — it does not fix an integrity defect. |
| Celery job-state refactor (Phase 11) | D5, F4 | Changes the contract of 3 endpoints and requires frontend polling. Current behaviour is slow, not incorrect. |
| Question revision chain (Phase 4, full) | C5 | This sprint makes approved content *immutable*; it does not add supersede-and-revise. Immutability is the safety property; revision is the convenience. |
| Semantic/near-duplicate detection (Phase 15) | — | Exact-signature dedup already works; upgrade is an enhancement, not a defect fix. |
| Observability stack (Phase 22) | C6 | Partially addressed: LLM failures are now logged rather than silently swallowed. Full structured logging with request IDs deferred. |

**A3 (reprocessing destroys mastery) is mitigated, not solved.** The full fix is SOP versioning.
This sprint prevents the destructive path from being reachable once approved content exists,
which stops the data loss without pretending the versioning model has been built.

---

## Execution order

P0 integrity first (items 1–2), because they are the defects that make existing training records
untrustworthy. Then record integrity (3–5), then access control (6–8), then configuration (9),
with tests written alongside each change and the full suite re-run after every phase.

## Definition of done, per item

Implementation exists · regression test exists and fails without the fix · full suite passes ·
existing functionality verified intact · documentation updated · no TODO standing in for
functionality.
