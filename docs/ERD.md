# Data Model / Entity-Relationship Specification
## GxP Training Bot

**Basis:** `backend/*/models.py` and `backend/*/migrations/`, read at commit `d783815`.
**Method:** every field below is transcribed from the model definition. Nothing is inferred.

---

## 1. Entity relationship overview

Actual relationships as defined in code (not a generic template):

```text
                    django.contrib.auth.User
                    (stock Django user — not customised)
                       │
       ┌───────────────┼────────────────┬──────────────────┬─────────────────┐
       │               │                │                  │                 │
  LearnerProfile   SOPDocument      QuizAttempt      TopicMastery      ChunkMastery
   (1:1 user)      (uploaded_by)      (learner)        (learner)         (learner)
       │               │                │                  │                 │
       │               ▼                │                  │                 │
       │           SOPChunk ────────────┼──────────────────┘                 │
       │           (sop FK)             │           (TopicMastery.sop)       │
       │               │                │                                    │
       │               │                │        ChunkMastery.sop_chunk ─────┘
       │               ▼                │
       │           Question             │
       │        (sop, job_role,         │
       │         source_chunk)          │
       │               │                │
       │               ▼                ▼
       │            Option ───────► AttemptAnswer
       │        (question FK)      (attempt, question, selected_option)
       │
       ▼
    JobRole ◄──── referenced by: LearnerProfile, Question, QuizAttempt,
                                 TopicMastery, ChunkMastery

    AuditLog  (user FK; object_type/object_id are loose, non-FK references)
```

**Note on `AuditLog`:** it does *not* use a `GenericForeignKey`. `object_type` is a plain
`CharField` holding a class name and `object_id` a plain `PositiveIntegerField`. There is no
database-level referential integrity between an audit row and the record it describes.

---

## 2. `accounts` app

### 2.1 `JobRole` — `backend/accounts/models.py:5`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `id` | BigAutoField | auto | auto | PK (`DEFAULT_AUTO_FIELD`, `config/settings.py:102`) |
| `name` | CharField(120) | yes | — | `unique=True` |
| `department` | CharField(120) | yes | — | free text, not a FK or choice list |
| `description` | TextField | no | `""` | `blank=True` |

No `Meta` ordering. `__str__` → `"{name} - {department}"`.

### 2.2 `LearnerProfile` — `backend/accounts/models.py:14`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `id` | BigAutoField | auto | auto | PK |
| `user` | OneToOneField(User) | yes | — | `on_delete=CASCADE` |
| `job_role` | FK(JobRole) | no | `NULL` | `on_delete=SET_NULL`, `null=True, blank=True` |
| `employee_code` | CharField(50) | no | `""` | `blank=True`, **not unique** |

> IMPLEMENTATION AMBIGUOUS — REQUIRES VERIFICATION: `employee_code` is the only
> human-facing employee identifier but carries no uniqueness constraint, so two learners can
> hold the same code.

### 2.3 Role groups — `backend/accounts/migrations/0002_create_role_groups.py`

A data migration creates two `django.contrib.auth.Group` rows: `"Admin"` and `"SME"`.
Roles are therefore **group membership plus the `User.is_staff` flag**, not a dedicated model.

---

## 3. `sops` app

### 3.1 `SOPDocument` — `backend/sops/models.py:5`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `id` | BigAutoField | auto | auto | PK |
| `title` | CharField(180) | yes | — | |
| `sop_code` | CharField(80) | yes | — | not unique alone |
| `version` | CharField(40) | yes | — | free text (`"v2.1"`), not numeric |
| `department` | CharField(120) | yes | — | free text |
| `file` | FileField | yes | — | `upload_to="sops/"` |
| `status` | CharField(20) | yes | `"uploaded"` | choices: `uploaded` / `processed` / `failed` |
| `uploaded_by` | FK(User) | no | `NULL` | `on_delete=SET_NULL` |
| `created_at` | DateTimeField | auto | now | `auto_now_add=True` |

**Constraints:** `unique_together = ("sop_code", "version")` — the same SOP code may exist at
multiple versions, but not twice at one version. **Ordering:** `["-created_at"]`.

> Versioning is *identity only*. There is no supersedes/predecessor link between versions, no
> effective date, and no "current version" flag. See GAP-C4.

### 3.2 `SOPChunk` — `backend/sops/models.py:29`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `id` | BigAutoField | auto | auto | PK |
| `sop` | FK(SOPDocument) | yes | — | `related_name="chunks"`, `on_delete=CASCADE` |
| `section_title` | CharField(180) | no | `""` | `blank=True`; task substitutes `"Auto chunk N"` |
| `page_number` | PositiveIntegerField | no | `NULL` | **never populated by the pipeline** — see below |
| `chunk_text` | TextField | yes | — | |
| `chunking_strategy` | CharField(20) | no | `NULL` | choices: `heading` / `semantic` / `fixed_length` |
| `created_at` | DateTimeField | auto | now | `auto_now_add=True` |

> IMPLEMENTED BUT NOT POPULATED: `page_number` is written only by `seed_demo`
> (`accounts/management/commands/seed_demo.py:120`). The real ingestion path
> (`sops/tasks.py:22`) never sets it, even though `extract_pdf_text` emits `[Page N]` markers
> into the text (`sops/services.py:38`). Page-level citation is therefore unavailable for
> genuinely uploaded PDFs. See GAP-B3.

---

## 4. `quiz` app

### 4.1 `Question` — `backend/quiz/models.py:7`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `id` | BigAutoField | auto | auto | PK |
| `sop` | FK(SOPDocument) | yes | — | `related_name="questions"`, CASCADE |
| `job_role` | FK(JobRole) | yes | — | `related_name="questions"`, CASCADE |
| `source_chunk` | FK(SOPChunk) | no | `NULL` | `on_delete=SET_NULL` — grounding provenance |
| `question_text` | TextField | yes | — | |
| `difficulty` | CharField(20) | yes | `"medium"` | choices: `easy` / `medium` / `hard` |
| `explanation` | TextField | yes | — | |
| `status` | CharField(20) | yes | `"draft"` | choices: `draft` / `approved` / `rejected` |
| `confidence_score` | FloatField | no | `NULL` | LLM self-reported, clamped 0.0–1.0 |
| `generation_source` | CharField(20) | no | `NULL` | choices: `nvidia_nim` / `mock` / `manual` |
| `elo_rating` | FloatField | yes | `1500` | live difficulty; seeded on create |
| `created_at` | DateTimeField | auto | now | `auto_now_add=True` |

**Ordering:** `["-created_at"]`. **No unique constraint** — de-duplication is done in
application code by normalised content signature (`ai_engine/tasks.py:33-51`), not by the DB.

**Custom `save()` (`quiz/models.py:58`):** on creation only, if `elo_rating` still equals the
field default, it is re-seeded from `DIFFICULTY_SEED_ELO = {"easy": 1300, "medium": 1500,
"hard": 1700}`. An explicitly supplied rating is preserved.

> Edge case in that logic: a caller who explicitly passes `elo_rating=1500` for an `easy`
> question is indistinguishable from one who passed nothing, so the value is silently
> overwritten with 1300. Benign today, but it is a real behavioural wrinkle.

### 4.2 `Option` — `backend/quiz/models.py:71`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `id` | BigAutoField | auto | auto | PK |
| `question` | FK(Question) | yes | — | `related_name="options"`, CASCADE |
| `option_text` | CharField(500) | yes | — | |
| `is_correct` | BooleanField | yes | `False` | |

**No constraint enforces exactly one correct option per question.** Nothing at the DB or
serializer level prevents zero or multiple correct options. The AI pipeline always writes
exactly one (`ai_engine/tasks.py:66`), but the REST API (`POST /api/quiz/options/`) does not.
See GAP-B1.

---

## 5. `attempts` app

### 5.1 `QuizAttempt` — `backend/attempts/models.py:14`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `id` | BigAutoField | auto | auto | PK |
| `learner` | FK(User) | yes | — | `related_name="quiz_attempts"`, CASCADE |
| `job_role` | FK(JobRole) | yes | — | CASCADE |
| `sop` | FK(SOPDocument) | yes | — | CASCADE |
| `score` | DecimalField(5,2) | yes | `0` | percentage 0.00–100.00 |
| `started_at` | DateTimeField | auto | now | `auto_now_add=True` |
| `completed_at` | DateTimeField | no | `NULL` | `NULL` ⇒ in progress |

**Ordering:** `["-started_at"]`. No uniqueness — a learner may hold many attempts per SOP,
which the auto-assignment logic depends on.

### 5.2 `AttemptAnswer` — `backend/attempts/models.py:29`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `id` | BigAutoField | auto | auto | PK |
| `attempt` | FK(QuizAttempt) | yes | — | `related_name="answers"`, CASCADE |
| `question` | FK(Question) | yes | — | CASCADE |
| `selected_option` | FK(Option) | no | `NULL` | `on_delete=SET_NULL` — `NULL` = unanswered |
| `is_correct` | BooleanField | yes | `False` | graded server-side at submit time |

**No `unique_together("attempt", "question")`** — the same question can be recorded twice in
one attempt. `analytics/tests.py:34-37` deliberately exploits this to fabricate statistics,
which shows the constraint genuinely is absent.

### 5.3 `MasteryState` (abstract) — `backend/attempts/models.py:39`

Shared base for both mastery granularities. `class Meta: abstract = True` — no table.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `box_index` | PositiveSmallIntegerField | yes | `0` | Leitner tier, display/audit only |
| `streak_correct` | PositiveSmallIntegerField | yes | `0` | consecutive passing attempts |
| `mastery_status` | CharField(20) | yes | `"in_progress"` | choices: `in_progress` / `mastered` |
| `next_eligible_at` | DateTimeField | yes | `timezone.now` | FSRS-computed due date |
| `elo_rating` | FloatField | yes | `1500` | learner ability at this granularity |
| `fsrs_stability` | FloatField | no | `NULL` | `NULL` until first review |
| `fsrs_difficulty` | FloatField | no | `NULL` | `NULL` until first review |
| `updated_at` | DateTimeField | auto | now | `auto_now=True`; read *before* save as "last review" |

**Class constants:** `BOX_INTERVAL_DAYS = [1,2,4,7,14,30]` (retained for `box_index` capping
only — no longer drives scheduling), `MASTERY_STREAK_THRESHOLD = 3`, `PASS_THRESHOLD = 80`.

### 5.4 `TopicMastery(MasteryState)` — `backend/attempts/models.py:120`

Adds: `learner` FK(User, `related_name="topic_masteries"`, CASCADE), `sop` FK(SOPDocument,
CASCADE), `job_role` FK(JobRole, CASCADE).
**Constraints:** `unique_together = ("learner", "sop")`. **Ordering:** `["next_eligible_at"]`.

Note `job_role` is *outside* the unique key: a learner who changes job role reuses the same
row, and `job_role` is overwritten on each submit (`attempts/views.py:150`).

### 5.5 `ChunkMastery(MasteryState)` — `backend/attempts/models.py:150`

Adds: `learner` FK(User, `related_name="chunk_masteries"`, CASCADE), `sop_chunk`
FK(SOPChunk, `related_name="masteries"`, CASCADE), `job_role` FK(JobRole, CASCADE).
**Constraints:** `unique_together = ("learner", "sop_chunk")`. **Ordering:** `["next_eligible_at"]`.

> Deliberate design note recorded in the model docstring: `TopicMastery` is **not** derived
> from `ChunkMastery`, because questions with `source_chunk = NULL` would otherwise make
> whole-SOP mastery unreachable. The two are independent signals from the same attempt.

**Cascade consequence:** re-processing an SOP deletes and recreates all its `SOPChunk` rows
(`sops/tasks.py:20`), which cascades away every `ChunkMastery` for that SOP and nulls every
`Question.source_chunk`. All section-level learner history is silently destroyed on re-process.
See GAP-A3.

---

## 6. `audit` app

### 6.1 `AuditLog` — `backend/audit/models.py:5`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `id` | BigAutoField | auto | auto | PK |
| `user` | FK(User) | no | `NULL` | `on_delete=SET_NULL`, `related_name="audit_logs"` |
| `action` | CharField(40) | yes | — | 10 choices, below |
| `object_type` | CharField(60) | no | `""` | class name string, **not** a FK |
| `object_id` | PositiveIntegerField | no | `NULL` | **not** a FK |
| `summary` | CharField(255) | yes | — | human-readable |
| `details` | JSONField | no | `{}` | structured payload |
| `created_at` | DateTimeField | auto | now | `auto_now_add=True` |

**Ordering:** `["-created_at"]`.

**`ACTION_CHOICES` (10):** `sop_uploaded`, `sop_processed`, `sop_process_failed`,
`questions_generated`, `question_approved`, `question_rejected`, `quiz_attempt_submitted`,
`sop_chat_query`, `quiz_attempt_auto_assigned`, `retraining_escalation`.

**Append-only enforcement** is at the Django-admin layer only (`audit/admin.py:13-21`:
`has_add_permission`, `has_change_permission`, `has_delete_permission` all return `False`;
every field is in `readonly_fields`). There is **no database trigger, no immutable storage,
and no ORM-level guard** — any code path calling `AuditLog.objects.filter(...).delete()` or
`.update()` would succeed. See GAP-E5.

**`on_delete=SET_NULL` on `user`** means deleting a user silently anonymises their entire
audit history to `NULL` rather than preserving attribution. See GAP-E6.

---

## 7. Migration inventory

| App | Migrations | Notes |
|---|---|---|
| `accounts` | `0001_initial`, `0002_create_role_groups` | 0002 is a data migration (Admin/SME groups) |
| `sops` | `0001_initial`, `0002_sopchunk_chunking_strategy` | |
| `quiz` | `0001_initial`, `0002_question_confidence_score`, `0003_question_generation_source`, `0004_question_elo_rating` | |
| `attempts` | `0001_initial`, `0002_topicmastery`, `0003_topicmastery_elo_rating`, `0004_topicmastery_fsrs_difficulty_and_more`, `0005_chunkmastery` | |
| `audit` | `0001_initial`, `0002/0003/0004_alter_auditlog_action` | three successive `ACTION_CHOICES` expansions |
| `ai_engine`, `analytics` | none (no models) | both are behaviour-only apps |

`manage.py makemigrations --check` was not run as part of this analysis; migration/model drift
is unverified.

---

## 8. Indexes

**No explicit `db_index=True` and no `Meta.indexes` anywhere in the codebase.** The only
indexes present are those Django creates automatically: primary keys, every `ForeignKey`
column, and the `unique_together` composite indexes on `SOPDocument`, `TopicMastery`, and
`ChunkMastery`.

Frequently-filtered columns with **no** index include `Question.status`,
`SOPDocument.status`, `QuizAttempt.completed_at`, `AttemptAnswer.is_correct`, and
`MasteryState.next_eligible_at` (which is the ordering key for two models and the filter
predicate for the auto-assignment query). See GAP-F2.
