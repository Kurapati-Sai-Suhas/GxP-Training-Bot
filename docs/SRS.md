# Software Requirements Specification
# GxP Training Bot

| | |
|---|---|
| **Document type** | Reverse-engineered SRS — documents the system **as implemented**, not as intended |
| **Baseline** | commit `d783815`, branch `main` |
| **Method** | Source, models, migrations, routes, tests, config, Docker, CI read directly. Two findings verified empirically against a running instance. |
| **Verified test status** | 89 backend tests, all passing (`manage.py test`); 0 frontend tests — **this is the audit baseline**; the suite is now 176 after the hardening and adaptive sprints (see [`CHANGELOG.md`](CHANGELOG.md)) |
| **Status** | Analysis only — no source code was modified in producing this document |

**Reading conventions.** Where the repository does not support a statement, it is marked
`NOT FOUND IN CURRENT IMPLEMENTATION`. Where behaviour is unclear, `IMPLEMENTATION AMBIGUOUS —
REQUIRES VERIFICATION`. Where project documentation claims something the code does not do,
`DOCUMENTED BUT NOT IMPLEMENTED`; the reverse is `IMPLEMENTED BUT NOT DOCUMENTED`.
Contradictions are reported, not silently reconciled.

**Companion documents:** [`ERD.md`](ERD.md) · [`API.md`](API.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md) ·
[`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md) · [`GAP_ANALYSIS.md`](GAP_ANALYSIS.md)

---

# 1. Introduction

## 1.1 Purpose

This SRS records the actual behaviour of the GxP Training Bot codebase so that a subsequent
implementation phase can be planned against verified facts rather than assumptions. It is
descriptive, not aspirational.

## 1.2 Scope

**In scope:** the Django REST backend (7 apps), the React SPA, the AI/RAG pipeline, the Celery
async layer, the data model, Docker Compose and GitHub Actions configuration, and the automated
test suite.

**Out of scope:** anything not present in the repository. No runtime production environment,
monitoring stack, or user documentation exists to examine.

## 1.3 Product overview

The system converts pharmaceutical Standard Operating Procedures into role-specific
multiple-choice quizzes. An LLM drafts questions from the SOP's own text; a qualified human
reviewer must approve each one under a password-confirmed electronic signature before any
learner sees it; learner attempts are graded server-side and drive a spaced-repetition
retraining schedule; and defined events are written to an append-only audit trail.

The design intent, stated in the repository's README and consistent with the code, is
*"AI proposes, a qualified human disposes."*

Built for the NVIDIA GenAI Bootcamp, problem statement PS053.

## 1.4 Intended users

Three tiers, resolved server-side (`accounts/permissions.py`, `accounts/views.py:30`):

| Tier | Determined by | Population |
|---|---|---|
| Training/QA Admin | `User.is_staff` **or** `"Admin"` group | small |
| SME Reviewer | `"SME"` group (Admins also qualify) | small |
| Learner | any authenticated user without the above | the majority |

## 1.5 Definitions

| Term | Meaning in this system |
|---|---|
| **SOP** | Standard Operating Procedure — the source document (`SOPDocument`) |
| **Chunk** | A semantically coherent slice of an SOP (`SOPChunk`); the unit of AI grounding |
| **Draft / Approved / Rejected** | The three `Question.status` values; only `approved` should reach learners |
| **E-signature** | Re-entry of the reviewer's own password, verified with `check_password()` |
| **Elo** | Paired rating: learner ability and question difficulty, updated per answer |
| **FSRS** | Free Spaced Repetition Scheduler — memory model producing the next review date |
| **TopicMastery** | Per-(learner, SOP) scheduling state |
| **ChunkMastery** | Per-(learner, SOPChunk) scheduling state — the finer-grained sibling |
| **Pass signal** | Confidence-filtered, Elo-weighted pass/fail used for scheduling — distinct from the learner-facing percentage score |
| **GxP** | Collective term for Good {Manufacturing, Laboratory, Clinical} Practice regulations |

## 1.6 References

**In-repository:** `README.md`, `ROADMAP.md`, `DEMO_SCRIPT.md`, `docs/*.docx|pptx`.

**Cited in source comments** (these genuinely drove design decisions and are traceable to
specific code):

- Pelánek, *Applications of the Elo rating system in adaptive educational systems*, Computers & Education 98 (2016) → `attempts/services.py`
- FSRS-4.5 default weights, open-spaced-repetition → `attempts/fsrs.py`
- Wilson, Karklin, Han & Ekanadham, EDM 2016 → basis for **rejecting** neural knowledge tracing
- Sapountzi et al., EDM 2021 → streak-based mastery stopping rule
- Ye, Su & Cao, KDD 2022; Settles & Meeder, ACL 2016 → difficulty-weighted scoring
- Geng et al., NAACL 2024 → LLM confidence miscalibration → `CONFIDENCE_TRUST_THRESHOLD`
- Kiss, Nagy & Szilágyi, Discover Computing 2025 → Max-Min semantic chunking
- Moreno-Cediel et al., Knowledge-Based Systems 2025 → against fixed-length splitting

> **Note:** `README.md` §"What's next" references `docs/SRS_GxP_Training_Bot.docx`, which was
> deleted at commit `b5be386`. That link is currently broken.

---

# 2. System Overview

## 2.1 Product perspective

A self-contained two-tier web application with one external dependency (NVIDIA NIM) and three
stateful ones (PostgreSQL/SQLite, Redis, local filesystem). It integrates with no LMS, HR
system, or document management system.

## 2.2 System architecture

Summarised here; full detail in [`ARCHITECTURE.md`](ARCHITECTURE.md).

```text
React SPA ──token──► Django REST (7 apps) ──► PostgreSQL / SQLite
                            │                └► local filesystem (MEDIA_ROOT)
                            ├──► Redis ──► Celery worker ──► NVIDIA NIM
                            └──► NVIDIA NIM (embeddings, via worker)
```

## 2.3 Operating environment

| | |
|---|---|
| Python | `>=3.11` (`pyproject.toml`); CI uses 3.12 |
| Node | 20 (CI and Dockerfile) |
| Database | SQLite (default) or PostgreSQL 16, selected by `DATABASE_URL` scheme |
| Broker | Redis 7 (only when `CELERY_TASK_ALWAYS_EAGER=False`) |
| Timezone | `Asia/Kolkata`, `USE_TZ=True` (`config/settings.py:94`) |

## 2.4 Dependencies

See [`ARCHITECTURE.md`](ARCHITECTURE.md) §8. All backend pins are compatible-ranges;
**neither** side has a committed lockfile (both `uv.lock` and `package-lock.json` are
gitignored), so no build in this repository is reproducible.

## 2.5 Constraints

1. **Single LLM provider**, hardcoded in two modules.
2. **Synchronous task waits** — every Celery call is `.delay().get(timeout=…)`.
3. **Local-filesystem media** — the web and worker containers must share a volume; horizontal
   scaling would require object storage.
4. **No pagination** anywhere — response sizes grow without bound.
5. **Eager Celery by default** — the default execution path differs from the deployed one.

---

# 3. Actors and Roles

| Actor | Purpose | Permissions (as enforced in code) | Main actions |
|---|---|---|---|
| **Training/QA Admin** | Owns SOPs and content generation | `IsAdminUser`: all SOP writes, generation, question CRUD, job-role and learner-profile writes, audit read/export; unscoped view of all attempts | Upload/process/delete SOPs, generate quizzes, approve/reject, manage roles, export audit log |
| **SME Reviewer** | Independent qualified approver | `IsReviewerUser`: approve/reject with e-signature, retraining and section-mastery views | Review AI drafts, e-sign decisions |
| **Learner** | Trains and is assessed | `IsAuthenticated`: own attempts only; may read all SOPs, chunks, questions, job roles, learner profiles, and the analytics dashboard | Take quizzes, view own results, ask the SOP chatbot |
| **Celery worker** | Executes the 3 long-running tasks | Full ORM access; writes audit entries with the initiating user's identity | SOP processing, quiz generation, chat answering |
| **NVIDIA NIM** | External LLM + embeddings | None (outbound only) | Generates questions, answers, embeddings |
| **Django admin site** | Break-glass data administration | `is_staff` | Direct model editing; `AuditLog` is read-only |

> **Role separation caveat.** An Admin can both generate questions and approve them
> (`IsReviewerUser` accepts `is_staff`). Nothing enforces that the approver differs from the
> initiator. The README's role table implies separation; the code permits self-approval.
> See §11 GXP-4.

> **`IMPLEMENTED BUT NOT DOCUMENTED`:** the read surface available to a plain learner is far
> wider than the README's role table suggests — all SOP chunk text, all questions (including
> unapproved drafts and correct answers), all learner profiles with emails, and the full
> analytics dashboard including other learners' scores.

---

# 4. Functional Requirements

Requirement IDs are traced to code and tests in
[`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md).

## 4.1 Authentication

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-001 | Username/password login returns a DRF token plus a user object with role flags | Implemented | `accounts/views.py:58` |
| FR-002 | Logout deletes **all** the user's tokens | Implemented | `accounts/views.py:76` |
| FR-003 | `me/` returns identity, role tiers, and learner profile | Implemented | `accounts/views.py:83` |
| FR-004 | SPA restores a session from `localStorage` on boot | Implemented | `App.jsx:2107` |
| — | Signup, password change/reset, account deactivation, MFA, token expiry, idle timeout | **NOT FOUND IN CURRENT IMPLEMENTATION** | — |

## 4.2 Authorization

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-010 | Admin = `is_staff` or `"Admin"` group | Implemented | `accounts/permissions.py:7` |
| FR-011 | Reviewer = Admin or `"SME"` group | Implemented | `accounts/permissions.py:19` |
| FR-012 | Learners see only their own attempts and answers | Implemented | `attempts/views.py:93,203` |
| FR-013 | Navigation hidden by role | Implemented (client-side, defence-in-depth only) | `App.jsx:86,2253` |
| FR-014 | `Admin`/`SME` groups created by data migration | Implemented | `accounts/migrations/0002` |
| — | Object-level permissions beyond attempt ownership; department scoping; separation of duties | **NOT FOUND IN CURRENT IMPLEMENTATION** | — |

## 4.3 Document management

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-030 | Admin uploads SOP with metadata; server validates extension (`.pdf/.docx/.txt/.md`) and ≤20 MB | Implemented | `sops/serializers.py:39` |
| FR-031 | All authenticated users list/retrieve SOPs with chunk counts | Implemented | `sops/views.py:11` |
| FR-032 | Admin deletes an SOP (cascades to questions, attempts, answers, mastery) | Implemented, **unaudited** | `sops/views.py:16`; no `perform_destroy` |
| FR-033 | `unique_together(sop_code, version)` prevents duplicate version rows | Implemented | `sops/models.py:23` |
| — | Version supersession, effective dates, current-version flag, requalification on new version, soft delete, content-hash duplicate detection | **NOT FOUND IN CURRENT IMPLEMENTATION** | — |

## 4.4 Document processing

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-040 | Extract text: PyMuPDF (PDF), python-docx (DOCX), direct read (TXT/MD) | Implemented | `sops/services.py:21` |
| FR-041 | Three-tier chunking cascade: heading-aware → semantic (embeddings) → fixed-length | Implemented | `sops/services.py:121` |
| FR-042 | Record which tier produced each chunk | Implemented | `SOPChunk.chunking_strategy` |
| FR-043 | Chunking runs as a Celery task; failure marks the SOP `failed` and audits the reason | Implemented | `sops/tasks.py:37-43` |
| FR-044 | Re-processing rebuilds chunks | Implemented — **destructively** (see §16) | `sops/tasks.py:20` |
| FR-045 | Capture page numbers | **DOCUMENTED BUT NOT IMPLEMENTED** — field exists, real pipeline never sets it | `sops/tasks.py:22` |

## 4.5 Content generation

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-050 | Admin generates 1–20 role-specific MCQs from a processed SOP | Implemented | `ai_engine/views.py:13` |
| FR-051 | Questions distributed across chunks (`divmod`) | Implemented | `ai_engine/tasks.py:38` |
| FR-052 | Prompt constrains the model to the supplied SOP text and a strict JSON contract | Implemented | `ai_engine/services.py:25` |
| FR-053 | Explanation must state why the correct answer is compliant and the others risky | Implemented (prompt-level instruction) | `ai_engine/services.py:38` |
| FR-054 | Per-question self-reported confidence, clamped 0–1 | Implemented | `ai_engine/services.py:53` |
| FR-055 | Up to 3 attempts with linear backoff before falling back | Implemented, **untested** | `ai_engine/services.py:98-115` |
| FR-056 | Deterministic offline fallback on any failure | Implemented, **partly** deterministic (unseeded `random`) | `ai_engine/services.py:131` |
| FR-057 | Record generation path per question (`nvidia_nim`/`mock`/`manual`) | Implemented | `Question.generation_source` |
| FR-058 | Skip near-duplicates by normalised (question, correct answer) signature | Implemented | `ai_engine/tasks.py:33-51` |
| FR-059 | Questions persist as `draft`; a single `questions_generated` audit entry is written | Implemented | `ai_engine/tasks.py:60,81` |
| FR-060 | Caller-specified difficulty | **DOCUMENTED BUT NOT IMPLEMENTED** — UI sends it, view ignores it | `ai_engine/views.py:16-47` |

## 4.6 AI validation & grounding

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-070 | Strip markdown fences before JSON parsing | Implemented | `ai_engine/services.py:45` |
| FR-071 | Reject malformed drafts (4 required keys, ≥2 options); raise if none usable | Implemented | `ai_engine/services.py:61` |
| FR-072 | Link each question to its source chunk | Implemented | `Question.source_chunk` |
| FR-073 | Surface generation source and confidence to reviewers | Implemented | `App.jsx:2007-2044` |
| — | Semantic validation of answer correctness against the SOP; hallucination detection; prompt-injection defence | **NOT FOUND IN CURRENT IMPLEMENTATION** — the human gate is the only content control | — |

## 4.7 Review workflow

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-080 | Approve/reject requires `IsReviewerUser` | Implemented | `quiz/views.py:29` |
| FR-081 | Approve/reject requires the reviewer's own password, verified server-side | Implemented | `quiz/views.py:35-48` |
| FR-082 | A missing or wrong password returns 400 and changes nothing | Implemented | `quiz/views.py:41-47` |
| FR-083 | Successful signature audited with `details.e_signature = true` | Implemented | `quiz/views.py:58,73` |
| FR-084 | Admin may edit question content | Implemented — **no approval lock, no audit** | `quiz/views.py:31` |
| FR-085 | Approved questions locked from editing | **Client-side only**; the API permits the write | `App.jsx:1135` |
| — | Signature meaning/intent capture, content hash binding, question version history, reviewer comments, bulk review | **NOT FOUND IN CURRENT IMPLEMENTATION** | — |

## 4.8 Assessment

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-090 | Learner starts an attempt; `learner` forced to `request.user` | Implemented | `attempts/views.py:99` |
| FR-091 | Grading is entirely server-side from `Option.is_correct` | Implemented | `attempts/views.py:122` |
| FR-092 | Only the attempt's owner may submit (403 otherwise) | Implemented | `attempts/views.py:105` |
| FR-093 | Score = correct ÷ submitted × 100, 2 dp | Implemented | `attempts/views.py:135` |
| FR-094 | Unanswered questions recorded with `selected_option = NULL`, marked incorrect | Implemented | `attempts/views.py:119` |
| FR-095 | Result screen shows learner's answer, correct answer, explanation | Implemented | `App.jsx:1454-1474` |
| FR-096 | Prevent resubmission of a completed attempt | **NOT FOUND IN CURRENT IMPLEMENTATION** | no `completed_at` guard |
| FR-097 | Validate that submitted questions belong to the attempt and are approved | **NOT FOUND IN CURRENT IMPLEMENTATION** | `attempts/views.py:115` |
| — | Time limits, question shuffling, attempt limits, partial save/resume, proctoring | **NOT FOUND IN CURRENT IMPLEMENTATION** | — |

## 4.9 Adaptive retraining & progress

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-100 | `TopicMastery` updated once per completed attempt (not per answer) | Implemented | `attempts/views.py:147` |
| FR-101 | Pass signal excludes low-confidence questions (<0.5) unless >half would be excluded | Implemented | `attempts/views.py:56-61` |
| FR-102 | Pass signal weights each question by live Elo, mapped 1.0–2.0 | Implemented | `attempts/views.py:43` |
| FR-103 | 3 consecutive passes ⇒ `mastered`; any fail resets streak and box | Implemented | `attempts/models.py:106` |
| FR-104 | Elo: correct raises learner and lowers question, and vice versa (K=32/16) | Implemented | `attempts/services.py:37` |
| FR-105 | Question difficulty moves exactly once per answer even with two mastery tracks | Implemented | `apply_elo_update_ability_only` |
| FR-106 | New questions seed Elo from their difficulty label (1300/1500/1700) | Implemented | `quiz/models.py:58` |
| FR-107 | Next review date computed by FSRS-4.5 from stability | Implemented | `attempts/fsrs.py:103` |
| FR-108 | `ChunkMastery` tracked per section from the same attempt | Implemented | `attempts/views.py:182` |
| FR-109 | Questions without a source chunk contribute only to `TopicMastery` | Implemented | `attempts/views.py:159` |
| FR-110 | Auto-assignment pre-creates a `QuizAttempt` for due, unmastered SOPs; idempotent | Implemented | `attempts/views.py:245-253` |
| FR-111 | Retest targets unmastered sections, else previously-missed questions, else all | Implemented | `attempts/views.py:286-311` |
| FR-112 | Suggested difficulty derived from Elo, not streak | Implemented | `attempts/views.py:232` |
| FR-113 | Escalate to the audit trail after ≥3 failed attempts on one SOP | Implemented | `attempts/views.py:272` |
| FR-114 | Reviewer views of retraining and section mastery | Implemented | `attempts/views.py:354,389` |
| FR-115 | Personal refresher recommendation from the learner's own wrong answers | Implemented | `analytics/views.py:127` |

## 4.10 RAG SOP chatbot

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-120 | Any authenticated user asks a free-text question about a processed SOP (≤500 chars) | Implemented | `ai_engine/views.py:51` |
| FR-121 | Retrieval is lexical word-overlap over that SOP's chunks, top 6 | Implemented | `ai_engine/services.py:204` |
| FR-122 | No-overlap ties fall back to document order, never an empty set | Implemented | `ai_engine/services.py:213` |
| FR-123 | Prompt instructs answering only from supplied text, admitting non-coverage | Implemented | `ai_engine/services.py:222` |
| FR-124 | Offline fallback quotes the best-matching chunk (≤400 chars) verbatim | Implemented, fully deterministic | `ai_engine/services.py:267` |
| FR-125 | Every query audited with question text, source, and sections used | Implemented | `ai_engine/tasks.py:113` |
| — | Multi-turn memory, cross-SOP search, role scoping of which SOPs are askable, human review of answers | **NOT FOUND IN CURRENT IMPLEMENTATION** | — |

## 4.11 Analytics

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-130 | Aggregate dashboard (14 keys) | Implemented — **no role gate** | `analytics/views.py:14` |
| FR-131 | Weak-topic ranking by lowest correct rate | Implemented | `analytics/views.py:53` |
| FR-132 | Retraining-improvement: first vs latest score per learner/SOP pair | Implemented | `analytics/views.py:86` |
| FR-133 | Count of live (SOP, role) quizzes and of due retraining | Implemented | `analytics/views.py:76-81` |

## 4.12 Audit logging

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-140 | `log_action()` writes attributed, timestamped entries with a JSON detail blob | Implemented | `audit/models.py:39` |
| FR-141 | 10 action types logged | Implemented | `audit/models.py:9` |
| FR-142 | Admin-only read API | Implemented | `audit/views.py:17` |
| FR-143 | CSV export for inspectors | Implemented | `audit/views.py:20` |
| FR-144 | Append-only in the Django admin | Implemented (that UI only) | `audit/admin.py:13-21` |
| FR-145 | Audit deletions and content edits | **NOT FOUND IN CURRENT IMPLEMENTATION** | — |
| FR-146 | Tamper-evidence (hash chain / signature / WORM) | **NOT FOUND IN CURRENT IMPLEMENTATION** | — |

## 4.13 Administration

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-150 | Django admin for SOPs, chunks, questions, options, attempts, answers, audit | Implemented | `*/admin.py` |
| FR-151 | `seed_demo` creates roles, users, SOPs, questions, attempts | Implemented | `seed_demo.py` |
| FR-152 | Admin registration for `TopicMastery`/`ChunkMastery` | **NOT FOUND IN CURRENT IMPLEMENTATION** | — |

---

# 5. Non-Functional Requirements

## 5.1 Performance

**Implemented:** `select_related`/`prefetch_related` on the main viewsets; DB-level aggregation
for most dashboard metrics; Celery process isolation for the three slow operations; `count`
via `.count()` rather than `len()` in hot paths.

**Not implemented:** pagination (none anywhere), caching (no cache backend configured), any
index beyond Django's automatic ones, and asynchronous request handling — every Celery call
blocks its web worker for up to 120 s. `dashboard_summary` loads every completed attempt into
memory; `retraining_status` and `section_mastery_status` are N+1.

**No performance budget, benchmark, or load test exists.**

## 5.2 Reliability

**Implemented and genuinely good:** every AI-dependent feature degrades rather than fails —
3 retries with linear backoff, then a deterministic offline path, at three independent points
(quiz generation, chat, semantic chunking). SOP processing failure is caught, marks the record
`failed`, and is audited. Two regression tests exist for real bugs (stale `prefetch_related`
caches) and are named for the bugs they prevent.

**Not implemented:** Celery task retries (`@shared_task` with no `autoretry_for`/`max_retries`),
dead-letter handling, database transaction wrapping of multi-step operations (`submit()` performs
~8 writes with no `atomic()` block — a mid-sequence failure leaves partial state), idempotency
keys, and circuit breaking.

## 5.3 Security

Detailed in §10. Summary: authentication and RBAC are implemented and well tested; transport
security, secrets management, rate limiting, data scoping on two endpoints, and audit coverage
of destructive operations are absent.

## 5.4 Scalability

**Supports:** a stateless API (token auth, no server-side session state required), an external
broker, a `DATABASE_URL`-driven backend that runs identically on SQLite and PostgreSQL, and CI
that proves the Postgres path.

**Blocks horizontal scaling:** media on a local shared volume; synchronous `.get()` coupling web
worker count to concurrency; unpaginated responses; no read replicas or connection pooling.

## 5.5 Maintainability

**Strengths:** clean app boundaries; algorithm modules free of framework coupling; unusually
high-quality explanatory comments that record *why* a decision was made and what was rejected;
89 passing tests concentrated on the highest-value logic.

**Weaknesses:** a 2,305-line single-file frontend with no tests; a 94-line transaction script in
`submit()`; a duplicated provider constant; no linting or type checking in CI; no lockfiles.

## 5.6 Usability

**Implemented:** role-filtered navigation; explicit loading and error states on every async
action; a confirmation modal for the e-signature; provenance badges (confidence, generation
source, Elo) that let a reviewer judge AI output; a global search across SOPs, questions, and
learners; empty-state copy that explains the next action.

**Concern:** when the API is unreachable the SPA renders **invented demo data** — fabricated
compliance percentages, SOP rows, and review questions (`App.jsx:316-369,492-499,982-1006`) —
distinguished only by a small "Using demo fallback" badge. In a compliance product, showing
plausible fabricated numbers is a hazard, not a graceful degradation. See GAP-D6.

## 5.7 Observability

**`NOT FOUND IN CURRENT IMPLEMENTATION`.** A grep for `LOGGING`, `logger`, and `logging` across
the backend returns zero matches. There is no logging configuration, no metrics, no tracing, no
request IDs, no health-check endpoint, and no error tracking. Five bare `except Exception`
blocks discard the exception object entirely, so LLM failures leave no diagnostic trace beyond
the `generation_source` column.

This is the single largest operational-readiness gap.

## 5.8 Availability

Docker Compose defines a `pg_isready` healthcheck for the database and dependency ordering. No
healthcheck exists for the backend, worker, or frontend; no restart policy; no readiness or
liveness endpoint; no graceful shutdown handling.

---

# 6. AI/ML Requirements

## 6.1 Model and provider

| Aspect | Value | Location |
|---|---|---|
| Provider | NVIDIA NIM (OpenAI-compatible) | `ai_engine/services.py:9` |
| Generation model | `meta/llama-3.1-8b-instruct` | `ai_engine/services.py:10` |
| Embedding model | `nvidia/nv-embedqa-e5-v5` | `sops/services.py:15` |
| Client | `openai` SDK, base-URL redirected | `requirements.txt:10` |
| Temperature | 0.2 (both prompts) | `services.py:106,256` |
| Credential | `NVIDIA_API_KEY` env var | `.env` (gitignored) |

**Provider-agnosticism assessment.** The system swaps between **one** live provider and a
non-AI deterministic fallback — not between multiple AI providers. There is no registry,
strategy interface, or configuration key selecting a backend; grep for `anthropic|gemini|claude|
provider` returns zero matches. The accurate term is *provider-portable* (two constants in two
files), with three independent live→offline degradation points. The base URL is duplicated
rather than shared.

## 6.2 Prompt architecture

Two prompts, each with a distinct responsibility:

**Quiz generation** (`build_quiz_prompt`, `services.py:25`) — establishes the GxP tutor role and
target job role; constrains the model to the supplied chunk text; specifies a strict JSON-array
output contract with six required fields; requires a self-assessed confidence value with explicit
guidance on when to lower it; and requires each explanation to justify the correct answer *and*
the risk of each wrong one. System message: *"Return strict JSON for a GxP quiz generation task."*

**SOP chat** (`build_sop_chat_prompt`, `services.py:218`) — restricts answers to the retrieved
sections; supplies a verbatim refusal string for uncovered questions; caps length at 2–4
sentences; requires naming the source section. System message: *"Answer strictly from the
provided SOP text."*

Both are direct hallucination-mitigation measures at the prompt layer. Neither has any defence
against instructions embedded in the SOP text itself (§10, GAP-E10).

## 6.3 Pipeline

```text
Document → extract → chunk (3-tier cascade) → [retrieve, chat only]
   → prompt → NVIDIA NIM (3 retries) → strip fences → parse JSON
   → normalise/validate → deduplicate → persist as draft → HUMAN E-SIGNATURE GATE → learner
```

Full diagram in [`ARCHITECTURE.md`](ARCHITECTURE.md) §3.

## 6.4 Validation

`_normalize_drafts` (`services.py:61`) accepts a bare array or `{"questions": [...]}`, drops any
item missing the four required keys or with fewer than 2 options, coerces
`correct_option_index` to `int`, clamps confidence to 0.0–1.0, and raises if nothing usable
survives — which triggers the retry/fallback path.

**No semantic validation exists.** Whether the correct answer is actually correct per the SOP is
determined solely by the human reviewer.

## 6.5 Fallback and retry

Retry: 3 attempts, `sleep(0.5 × attempt)` between. Fallback triggers on missing API key or
exhausted retries.

| Path | Deterministic? |
|---|---|
| Quiz mock generator | **Partly** — correct answer is verbatim SOP text, confidence fixed at 1.0; but `random.sample`/`random.shuffle` are unseeded, so distractors and ordering vary |
| Chat offline | **Yes** |
| Fixed-length chunking | **Yes** |

**Test caveat:** all fallback tests force `NVIDIA_API_KEY=""`, which returns *before* the retry
loop. The retry logic and all JSON-handling code are untested.

## 6.6 Deduplication

Signature = `(whitespace-normalised lowercased question_text, same for the correct answer)`,
compared against existing questions for that `(sop, job_role)` pair, accumulated within the run
so a batch cannot self-duplicate. Count returned as `skipped_duplicates`. Exact-match only — no
fuzzy or semantic similarity.

---

# 7. Data Requirements

Full specification in [`ERD.md`](ERD.md). Ten concrete models across five apps
(`JobRole`, `LearnerProfile`, `SOPDocument`, `SOPChunk`, `Question`, `Option`, `QuizAttempt`,
`AttemptAnswer`, `TopicMastery`, `ChunkMastery`) plus `AuditLog` and one abstract base
(`MasteryState`).

**Integrity mechanisms present:** three `unique_together` constraints; FK constraints throughout;
deliberate `on_delete` choices (`SET_NULL` to preserve records, `CASCADE` where the child is
meaningless alone).

**Integrity mechanisms absent:** no check constraint that a question has exactly one correct
option; no uniqueness on `(attempt, question)`; no uniqueness on `employee_code`; no indexes on
any filtered column; no soft delete anywhere; no row-level history or versioning.

**Retention:** no policy, archival, or purge mechanism. The audit log grows without bound and is
returned unpaginated.

---

# 8. API Requirements

Full specification in [`API.md`](API.md). 32 URL patterns, ~50 distinct method+path operations
across 7 apps. Token-based auth; `IsAuthenticated` global default; one public endpoint (`login`).

Cross-cutting gaps: no pagination, no versioning (no `/v1/`), no OpenAPI schema, no rate
limiting, and an inconsistent error contract that prevents serializer validation messages from
reaching users.

---

# 9. UI Requirements

Single React 18 SPA, no router — page state is a `useState` string (`App.jsx:2097`).

**Seven screens:** Dashboard, SOP Library, Generate Quiz (Admin), Question Review (Reviewer),
Learner Quiz, Analytics, Users & Roles.

**Implemented workflows:**

```text
Login → Dashboard → SOP Library → upload+process → Generate Quiz → Question Review
      → e-signature modal → approved → Learner Quiz → question-by-question → Submit
      → Result review → Analytics / Retraining status
```

```text
Learner: Login → Learner Quiz → [Adaptive Retraining Due panel | Recommended Refresher
      | manual SOP select] → attempt → submit → results with explanations
```

```text
Any user: SOP Library → "Ask About an SOP" → grounded answer + live/offline badge + sections
```

**Component inventory:** `Sidebar`, `Topbar` (with global search and dropdowns), `LoginScreen`,
`Dashboard`, `SopLibrary`, `GenerateQuiz`, `QuestionReview`, `LearnerQuiz`, `Analytics`,
`UsersRoles`, plus presentational helpers (`PageHeader`, `DataStatus`, `StatCard`, `SimpleTable`,
`OptionList`, `ConfidenceBadge`, `SourceBadge`, `EloBadge`).

**States handled:** loading (per-action button text and page-level), error (inline `text-error`),
empty (explanatory copy on every list), success (`text-success`), and disabled-by-role with an
explanatory note rather than a silently dead control.

**Gaps:** no routing or deep links (state lost on refresh); no accessibility work beyond a few
`aria-label`s; no responsive/mobile consideration evident; no tests; and the fabricated-fallback
issue in §5.6.

---

# 10. Security Requirements

| Control | Status | Evidence / gap |
|---|---|---|
| Authentication | **IMPLEMENTED** | DRF token; `check_password` for e-signature |
| Token lifecycle | **NOT IMPLEMENTED** | never expires; `get_or_create` reuses one key; `localStorage` (XSS-readable) |
| RBAC | **IMPLEMENTED** | 2 permission classes, per-action `get_permissions`, tested both sides |
| Object-level access | **PARTIALLY IMPLEMENTED** | attempts scoped correctly; questions, chunks, profiles, dashboard are not |
| Cross-learner data isolation | **NOT IMPLEMENTED** | `dashboard-summary` exposes all learners' scores; `learner-profiles` exposes all emails |
| Answer-key confidentiality | **NOT IMPLEMENTED** | `is_correct` shipped to the client before answering (verified empirically) |
| Password storage | **IMPLEMENTED** | Django default hashing; 4 validators enabled |
| Rate limiting / lockout | **NOT IMPLEMENTED** | no throttling on login **or** on the e-signature check |
| CSRF | **IMPLEMENTED** | middleware enabled; DRF enforces for session auth |
| CORS | **IMPLEMENTED** | explicit allow-list from env, not `ALLOW_ALL` |
| Transport security | **NOT IMPLEMENTED** | no TLS, no `SECURE_*` settings, no HSTS, no secure-cookie flags |
| Secrets management | **NOT IMPLEMENTED** | `.env` on disk; `SECRET_KEY` default `dev-only-secret-key`; DB password hardcoded in compose |
| File upload validation | **PARTIALLY IMPLEMENTED** | extension + size only; no content sniffing, no AV, original filename preserved |
| Uploaded-file access control | **NOT IMPLEMENTED** | media served by Django only when `DEBUG` — which the compose stack sets `True`, making every SOP world-readable |
| SQL injection | **IMPLEMENTED** | ORM throughout; no raw SQL |
| XSS | **PARTIALLY IMPLEMENTED** | React escapes by default; no `dangerouslySetInnerHTML`; but no CSP header |
| Prompt injection | **NOT IMPLEMENTED** | SOP text interpolated directly; chatbot output reaches learners unreviewed |
| CSV injection | **NOT IMPLEMENTED** | audit export writes user-influenced values unescaped |
| Audit of destructive ops | **NOT IMPLEMENTED** | SOP/question delete and edit are unlogged |
| Audit tamper-evidence | **NOT IMPLEMENTED** | admin-UI guard only; no hash chain, signature, or WORM |
| Dependency scanning | **NOT IMPLEMENTED** | no Dependabot, no `pip-audit`/`npm audit` in CI |
| Security logging | **NOT IMPLEMENTED** | no logging at all |

**The presence of a security mechanism is not evidence of security.** Authentication and RBAC
are genuinely well built and well tested. The deployment configuration, data-scoping on two
endpoints, and audit coverage of destructive operations are not.

---

# 11. Auditability Requirements — GxP-oriented assessment

> This section assesses GxP-relevant *mechanisms*. It makes **no claim of regulatory
> compliance**, and the evidence does not support one.

| # | Capability | Current implementation | Evidence | Gap | Risk |
|---|---|---|---|---|---|
| GXP-1 | **Traceability** (question → source text) | `Question.source_chunk` FK; `source_section` surfaced in the UI | `quiz/models.py:35` | `SET_NULL` on chunk deletion; re-processing orphans every question | **High** — provenance silently lost |
| GXP-2 | **Electronic signature** | Password re-verified server-side; `e_signature: true` audited | `quiz/views.py:35-48` | Stores a boolean only — no signed content hash, no meaning/intent, no signature manifestation per §11.50; unthrottled | **High** — signature does not bind to the content signed |
| GXP-3 | **Approval workflow** | Draft → approved/rejected; only reviewers may transition | `quiz/views.py:29` | No second-person review, no re-approval on edit | **Medium** |
| GXP-4 | **Role separation** | Three tiers enforced server-side | `accounts/permissions.py` | An Admin can generate *and* approve the same question — no separation of duties | **High** |
| GXP-5 | **Attributable audit trail** | `user`, `action`, object ref, summary, JSON details, timestamp | `audit/models.py` | `user` is `SET_NULL` — deleting a user anonymises their entire history | **High** |
| GXP-6 | **Record integrity** | Append-only in Django admin | `audit/admin.py:13-21` | No DB-level immutability; approved questions editable without audit; deletions unlogged; attempts resubmittable | **Critical** |
| GXP-7 | **Reproducibility** | `generation_source`, `chunking_strategy`, `confidence_score` recorded | `quiz/models.py`, `sops/models.py` | Prompt, model version, and raw response not stored; unseeded randomness in fallback; no lockfiles | **High** — an AI-drafted question cannot be reproduced or re-derived |
| GXP-8 | **Access control** | Token auth + RBAC, tested | `accounts/permissions.py` | No token expiry; no periodic access review; unauthenticated media | **High** |
| GXP-9 | **Training records** | `QuizAttempt` with score and completion timestamp | `attempts/models.py:14` | No completion certificate, no qualification state, no assignment model — cannot answer "is this person qualified?" | **Critical** |
| GXP-10 | **Assessment records** | `AttemptAnswer` per question | `attempts/models.py:29` | Resubmission overwrites them; the question may be edited afterwards; answer key was visible during the attempt | **Critical** — records may not reflect what happened |
| GXP-11 | **Content versioning** | SOP `unique_together(code, version)` | `sops/models.py:23` | Identity only — no supersession, no effective date, no requalification trigger; questions have no version history | **High** |
| GXP-12 | **Timestamps** | `auto_now_add`/`auto_now`, `USE_TZ=True` | throughout | Application-clock based, no trusted time source; `TIME_ZONE` is `Asia/Kolkata`, not UTC | **Low–Medium** |
| GXP-13 | **Accountability** | Audit attributes each logged action | `audit/models.py:39` | Only 10 action types; deletion, edit, and role changes are entirely absent | **Critical** |

**Conclusion.** The system implements a *credible sketch* of several 21 CFR Part 11-style
controls — a genuine password-verified approval signature, an attributed action log, an
admin-level append-only guard, and role separation. It does **not** implement the controls that
make such a trail dependable: signature-to-content binding, tamper-evident storage,
audit coverage of deletions and edits, protection of assessment integrity, or any concept of
training completion. The architecture is a reasonable foundation; the current state should not
be described as compliant, validated, or inspection-ready.

---

# 12. Testing Requirements

**Verified:** 89 backend tests, all passing — the state at audit time. Subsequent sprints took
this to 176; see [`TESTING.md`](TESTING.md) for the current inventory.

| App | Tests | Focus |
|---|---:|---|
| `attempts` | 30 | Elo (5), FSRS pure (7) + integration (3), section mastery (7), retraining, submission |
| `ai_engine` | 13 | Generation, offline fallback, chunk ranking, chat validation, audit |
| `quiz` | 11 | E-signature (6), RBAC, filtering, Elo seeding |
| `sops` | 10 | Upload validation, processing, 3-tier chunking cascade |
| `accounts` | 9 | Login, identity, role tiers, write permissions |
| `audit` | 5 | Attribution, admin-only access, CSV export, append-only |
| `analytics` | 4 | Weak topics, refresher recommendation, isolation |
| **Frontend** | **0** | — |

**Test-type matrix**

| Type | Found | Status |
|---|---|---|
| Unit (pure functions) | FSRS, Elo, chunk ranking, chunking | ✅ good |
| API/integration | most endpoints | ✅ good |
| Authentication | 6 | ✅ good |
| RBAC | ~10, from both sides | ✅ strong |
| E-signature | 6 | ✅ strong |
| AI fallback | 4 | ⚠️ only via missing key |
| AI live path | 0 | ❌ |
| Retry/backoff | 0 | ❌ |
| Celery (non-eager) | 0 | ❌ eager mode only |
| Destructive ops | 0 | ❌ |
| Frontend | 0 | ❌ |
| Security/pen | 0 | ❌ |
| Performance/load | 0 | ❌ |
| Migration/rollback | 0 | ❌ |

**Not configured:** coverage measurement, coverage thresholds, linting in CI, mutation testing,
contract tests, fixtures/factories (tests build objects inline).

**Quality observation.** The tests that exist are unusually well written — they assert
*behaviour* rather than implementation, carry docstrings explaining the scenario and its
rationale, cover permission boundaries from both directions, and include two regression tests
named for the real bugs they prevent. The problem is distribution, not craft: the best-covered
subsystem (adaptive retraining) is arguably the least risky, while the untested areas
(live AI path, deletions, the entire frontend) carry the highest consequences.

---

# 13. Deployment Requirements

**Local development:** `uv sync` → `.env` → `migrate` → `seed_demo` → `runserver`; `npm install`
→ `npm run dev`. SQLite and eager Celery mean no external services are needed.

**Docker Compose:** 5 services (db, redis, backend, celery-worker, frontend/nginx), Postgres
healthcheck gating startup, shared `media_data` volume, frontend built with a
`VITE_API_BASE_URL` build arg and served by nginx with SPA history fallback.

**CI:** backend tests against a real Postgres service container; frontend production build.
No lint, no frontend tests, no coverage, no security scan, no image publish, no deploy stage.

**The compose stack is not production-ready** — `DEBUG=True`, Django's `runserver`,
unauthenticated media, hardcoded DB password, placeholder `SECRET_KEY`, no TLS, no backend
healthcheck, no restart policy, no resource limits. See [`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2.

**`NOT FOUND IN CURRENT IMPLEMENTATION`:** production WSGI/ASGI server, reverse proxy or TLS
termination, secrets manager, backup/restore, migration rollback plan, blue-green or rolling
deployment, environment promotion, and infrastructure-as-code.

---

# 14. Error Handling

| Layer | Behaviour | Assessment |
|---|---|---|
| Upload validation | Serializer raises → 400 with field errors | Correct server-side, but the SPA cannot display it (§D7) |
| SOP processing | Broad catch → status `failed` + audit entry → 400 | **Good** — failure is recorded and visible |
| LLM call | 3 retries → fallback, exception discarded | Resilient but **silent**; no diagnostics |
| Embedding call | Returns `None` → next cascade tier | Correct |
| E-signature | Explicit 400, state unchanged | **Correct and tested** |
| Attempt ownership | Explicit 403 | **Correct and tested** |
| Invalid question ID on submit | Unhandled `IntegrityError` → 500 | **Gap** |
| Celery timeout | `TimeoutError` propagates → 500 | **Gap** — no friendly message or retry |
| Frontend | Per-action try/catch with inline messages | Good UX; silently falls back to fabricated data on total failure (§5.6) |

**Systemic issue:** no error is ever logged. Every failure path either surfaces to the user or
vanishes. There is no server-side record that an LLM call failed, that a token was rejected, or
that a 500 occurred.

---

# 15. Future / Planned Capabilities

Explicitly named as future work in `ROADMAP.md` and `README.md` — **not implemented, and
correctly labelled as such by the project**:

- Frontend test suite
- Multi-turn conversation memory for the SOP chatbot
- Concept clustering via LLM semantic similarity (LECTOR-style)
- Hard auto-assignment semantics for unstarted system-created attempts
- Committing `package-lock.json` so CI can use `npm ci`
- n8n workflow-automation integration (designed, not built)
- Production hardening: secrets management, TLS, rate limiting, observability, horizontal scaling

---

# 16. Known Limitations

Ordered by consequence. Full detail and IDs in [`GAP_ANALYSIS.md`](GAP_ANALYSIS.md).

1. **The answer key is sent to the client before the learner answers** (verified empirically).
   Every assessment is open-book to anyone who opens devtools. — GAP-A1
2. **Completed attempts can be resubmitted indefinitely**, overwriting score and mastery. Combined
   with (1), a learner can read the answers and then post a perfect score. — GAP-A2
3. **Deletions and content edits are not audited.** An Admin can delete an SOP — cascading away
   its questions, attempts, answers, and mastery — with no trace, or edit an approved question
   after it was e-signed. — GAP-E4
4. **Re-processing an SOP destroys all section-level mastery and question provenance.** — GAP-A3
5. **The deployment path runs Django's dev server with `DEBUG=True` and serves every uploaded SOP
   without authentication.** — GAP-E1
6. **Cross-learner performance data is readable by any authenticated user.** — GAP-E3
7. **No rate limiting** on login or on the e-signature password check. — GAP-E2
8. **No observability of any kind**; LLM failures are silently swallowed. — GAP-C6/E9
9. **No training-assignment or completion model** — the system cannot answer whether a person is
   qualified. — GAP-C2/C3
10. **No SOP version lifecycle** — learners stay "mastered" on superseded procedures. — GAP-C4
11. **The Difficulty control on Generate Quiz is a no-op.** — GAP-A4
12. **The live LLM path is entirely untested**; all fallback tests bypass it. — GAP-B5
13. **No pagination anywhere**; the audit log is returned in full and grows without bound. — GAP-F3
14. **The frontend fabricates data when the API is unreachable.** — GAP-D6
15. **No lockfiles on either side** — no build is reproducible. — GAP-D4

---

# 17. Requirements Traceability

See [`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md).

Summary: of 60 traced requirements, **34** have direct automated coverage, **10** partial, and
**16** none. Coverage is strongest in adaptive retraining (11/11), the e-signature workflow, and
RBAC boundaries; weakest in the live AI path, destructive operations, `dashboard-summary`
authorization, and the entire frontend.
