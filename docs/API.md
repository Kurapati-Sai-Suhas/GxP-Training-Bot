# API Specification
## GxP Training Bot

**Basis:** route map dumped from Django's own URL resolver at commit `d783815`, cross-read
against each view and serializer. Format-suffix variants (`.json`, `.api`) that DRF's
`DefaultRouter` generates automatically are omitted for readability; they exist for every
router-backed path.

---

## 0. Global conventions

**Authentication** (`config/settings.py:117-121`) — three classes are enabled, tried in order:

1. `TokenAuthentication` — `Authorization: Token <key>` (this is what the SPA uses)
2. `SessionAuthentication` — Django session cookie (used by the browsable API and admin)
3. `BasicAuthentication` — HTTP Basic

**Default permission** (`config/settings.py:116`): `IsAuthenticated`. Every endpoint requires
authentication *unless* it explicitly declares `AllowAny` — only `/api/accounts/login/` does.

**Permission classes** (`accounts/permissions.py`):

| Class | Grants access to |
|---|---|
| `IsAdminUser` | `user.is_staff == True` **or** membership of the `"Admin"` group |
| `IsReviewerUser` | `is_staff` **or** membership of `"Admin"` **or** `"SME"` |

**Tokens never expire.** `login` does `Token.objects.get_or_create` (`accounts/views.py:72`) —
the same key is returned on every login and is only invalidated by an explicit `logout`.

**No pagination is configured.** Every list endpoint returns the full result set as a bare JSON
array. (The SPA defensively handles `data.results` — `App.jsx:2143` — but that shape is never
produced.)

**No throttling / rate limiting is configured** anywhere.

**Error body shape** is inconsistent: hand-written views return `{"error": "..."}`, while DRF
serializer validation returns `{"<field>": ["..."]}`. The SPA only reads `.error`
(`services/api.js:25`), so serializer-level validation messages surface to users as the generic
`"Request failed: <path>"`.

---

## 1. `accounts` — authentication & identity

### `POST /api/accounts/login/`
- **Auth:** none (`AllowAny`) — `accounts/views.py:59`
- **Request:** `{"username": str, "password": str}`
- **Validation:** both fields required, else `400`
- **Response `200`:** `{"token": str, "user": {...}}` where `user` carries `id`, `username`,
  `first_name`, `last_name`, `is_staff`, `roles: {is_admin, is_reviewer}`, and
  `learner_profile` (nullable, with nested `job_role`)
- **Errors:** `400` missing fields · `401` invalid credentials
- **DB impact:** creates a `Token` row on first login for that user
- **Async:** none
- **Note:** unthrottled — see GAP-E2 (credential brute force)

### `POST /api/accounts/logout/`
- **Auth:** required · **Permission:** `IsAuthenticated`
- **Response `200`:** `{"message": "Logged out"}`
- **DB impact:** **deletes all `Token` rows for the user** — logging out on one device
  invalidates every other session for that account (`accounts/views.py:79`)

### `GET /api/accounts/me/`
- **Auth:** required · **Response `200`:** same `user` object as login
- Used by the SPA on boot to restore a session from `localStorage` (`App.jsx:2112`)

### `GET|POST /api/accounts/job-roles/` · `GET|PUT|PATCH|DELETE /api/accounts/job-roles/{pk}/`
- **Read:** `IsAuthenticated` · **Write (`create`/`update`/`partial_update`/`destroy`):** `IsAdminUser`
- **Ordering:** `("department", "name")`
- **Fields:** `id`, `name`, `department`, `description`
- **No audit logging on any write.** See GAP-E4.

### `GET|POST /api/accounts/learner-profiles/` · `GET|PUT|PATCH|DELETE /api/accounts/learner-profiles/{pk}/`
- **Read:** `IsAuthenticated` · **Write:** `IsAdminUser`
- **Fields:** `id`, `user`, `username`, `first_name`, `last_name`, `email`, `job_role`,
  `job_role_name`, `employee_code`
- **Privacy note:** any authenticated user — including a plain learner — can list every
  learner's full name and email. See GAP-E3.
- **No audit logging on any write** (assigning a learner to a job role is an untracked change).

> NOT FOUND IN CURRENT IMPLEMENTATION: user self-registration/signup, password change,
> password reset, email verification, account deactivation. Users exist only via
> `manage.py seed_demo` or the Django admin site.

---

## 2. `sops` — document management & processing

### `GET /api/sops/documents/`
- **Auth:** required · **Permission:** `IsAuthenticated` (all roles)
- **Response:** array of documents with `chunks_count` and `status_label` annotations
- **Note:** `file` is returned as a URL under `MEDIA_URL`, which is served **without
  authentication** — see GAP-E1.

### `POST /api/sops/documents/`
- **Permission:** `IsAdminUser` · **Content-Type:** `multipart/form-data`
- **Request:** `title`, `sop_code`, `version`, `department`, `file`
- **Validation** (`sops/serializers.py:39`): extension must be one of `.pdf`, `.docx`, `.txt`,
  `.md`; size ≤ 20 MB. `unique_together(sop_code, version)` enforced by the DB.
- **Response `201`** · **Errors:** `400` validation · `403` non-admin
- **DB impact:** creates `SOPDocument` (status `uploaded`) **and** an `AuditLog`
  `sop_uploaded` row (`sops/views.py:23`)
- **Security note:** validation is extension + size only. File *content* is never sniffed, so
  a renamed executable passes upload and fails later at extraction. See GAP-E7.

### `POST /api/sops/documents/{pk}/process/`
- **Permission:** `IsAdminUser`
- **Request:** empty body
- **Response `200`:** `{"message": "SOP processed", "chunks": int}` · `400` `{"error": ...}`
- **Async:** dispatches `process_sop_document_task.delay(...)` then **blocks** on
  `.get(timeout=60)` (`sops/views.py:35`) — the HTTP request waits for the worker
- **DB impact:** **deletes and recreates every `SOPChunk` for this SOP**, sets status to
  `processed` or `failed`, writes `sop_processed` or `sop_process_failed` to the audit log
- **Destructive side effect:** the chunk delete cascades to `ChunkMastery` and nulls
  `Question.source_chunk`. See GAP-A3.
- **Idempotency:** re-processing is allowed at any time with no guard and no confirmation.

### `PUT|PATCH|DELETE /api/sops/documents/{pk}/`
- **Permission:** `IsAdminUser`
- **`DELETE` cascades** to `SOPChunk` → `Question` → `Option` → `AttemptAnswer`, and to
  `QuizAttempt` and `TopicMastery` (all `on_delete=CASCADE`). A single call can erase an
  entire training history.
- **No audit log entry is written for update or delete.** For a system whose stated purpose is
  an append-only compliance trail, deletion of the primary record type is untracked.
  See GAP-E4 (this is the single most serious auditability gap).

### `GET /api/sops/chunks/` · `GET /api/sops/chunks/{pk}/`
- **Permission:** `IsAuthenticated` (read-only viewset, no role restriction)
- Exposes full `chunk_text` of every SOP to every authenticated user.

---

## 3. `quiz` — question authoring & review workflow

### `GET /api/quiz/questions/`
- **Permission:** `IsAuthenticated`
- **Query filters** (`quiz/views.py:14-26`): `sop`, `job_role`, `status` — all exact-match,
  all optional, applied independently
- **Response:** array of questions, each embedding its full `options` array

> ### 🔴 CRITICAL — the answer key is shipped to the client
> `OptionSerializer` (`quiz/serializers.py:6`) includes `is_correct`, and `QuestionSerializer`
> nests it. The learner quiz screen fetches questions from this exact endpoint
> (`services/api.js:128`) **before** the learner answers.
>
> Verified empirically against the development database:
> ```json
> {"id": 310, "option_text": "Annually, or per the instrument manufacturer's…", "is_correct": true}
> ```
> The `explanation` field is exposed the same way. Server-side scoring is genuinely tamper-proof
> (`attempts/views.py:122` re-derives correctness from the DB), but every assessment is
> open-book to anyone who opens browser devtools. See GAP-A1.

### `POST /api/quiz/questions/` · `PUT|PATCH|DELETE /api/quiz/questions/{pk}/`
- **Permission:** `IsAdminUser`
- **Read-only fields:** `created_at`, `generation_source`, `elo_rating`
- **🔴 No approval lock and no audit logging.** An Admin can `PATCH` the text, explanation, or
  difficulty of an **already-approved** question, and nothing is recorded. The SPA hides the
  Edit button for approved questions (`App.jsx:1135`) but that is a client-side affordance
  only — the API accepts the write. This voids the integrity of the electronic signature that
  approved the earlier content. See GAP-E4 / GXP-6.

### `PATCH /api/quiz/questions/{pk}/approve/` and `/reject/`
- **Permission:** `IsReviewerUser` (SME or Admin)
- **Request:** `{"password": "<the reviewer's own password>"}`
- **Validation** (`quiz/views.py:35-48`): password required, then verified with
  `request.user.check_password(...)`
- **Response `200`:** the updated question · **`400`** `{"error": ...}` for missing/incorrect
  password, changing nothing
- **DB impact:** sets `status`; writes `question_approved` / `question_rejected` to the audit
  log with `details={"e_signature": True}`
- **This is the system's one genuine 21 CFR Part 11-style control.** Caveats: the password
  check is unthrottled (brute-forceable, GAP-E2), and the audit record stores only a boolean —
  not the signed content hash, not the *meaning* of the signature. See GXP-2.

### `GET|POST /api/quiz/options/` · `GET|PUT|PATCH|DELETE /api/quiz/options/{pk}/`
- **Read:** `IsAuthenticated` · **Write:** `IsAdminUser`
- **No validation that a question has exactly one correct option** — an Admin can create a
  question with zero or several. See GAP-B1.

---

## 4. `attempts` — assessment & adaptive retraining

### `GET|POST /api/attempts/quiz-attempts/`
- **Permission:** `IsAuthenticated`
- **Queryset scoping** (`attempts/views.py:93`): Admin sees all attempts; every other user sees
  only their own — a correctly implemented object-level control
- **`POST`** forces `learner = request.user` (`perform_create`), so a learner cannot open an
  attempt in someone else's name
- **Read-only fields:** `learner`, `score`, `started_at`, `completed_at`

### `POST /api/attempts/quiz-attempts/{pk}/submit/`
- **Permission:** `IsAuthenticated` **plus** an explicit ownership check
  (`attempts/views.py:105`) returning `403` if the attempt belongs to someone else
- **Request:** `{"answers": [{"question": int, "selected_option": int|null}, ...]}`
- **Response `200`:** the full attempt with graded `answers`
- **Grading:** entirely server-side — `Option.objects.filter(id=…, question_id=…,
  is_correct=True).exists()`. The client's opinion of correctness is never trusted.
- **Score:** `round(correct / len(submitted_answers) * 100, 2)`; an empty list yields
  `total = 1` to avoid division by zero, scoring 0.00
- **Side effects, in order:** deletes prior `AttemptAnswer` rows → recreates them → sets
  `score`/`completed_at` → writes `quiz_attempt_submitted` audit row → computes whole-SOP and
  per-section pass signals → applies Elo updates → updates `TopicMastery` → updates each
  affected `ChunkMastery`

> 🔴 **No completion guard.** Nothing checks whether `completed_at` is already set. A learner
> can `POST` to `submit/` repeatedly on the same attempt; each call wipes the previous answers
> and overwrites the score, mastery state, and Elo ratings, while appending another
> `quiz_attempt_submitted` audit row. A learner can therefore submit, read the results screen
> (which reveals every correct answer), and resubmit a perfect score. See GAP-A2.

> **Unvalidated input:** `question_id` and `selected_option_id` are used without checking that
> the question belongs to this attempt's SOP or that it is `approved`. A crafted payload can
> record answers to arbitrary questions, which then feed the Elo and mastery calculations.
> An invalid `question_id` raises an unhandled `IntegrityError` → `500`. See GAP-B2.

### `GET /api/attempts/answers/` · `GET /api/attempts/answers/{pk}/`
- **Permission:** `IsAuthenticated`, read-only; scoped to the requesting learner unless Admin

### `GET /api/attempts/auto-assigned/`
- **Permission:** `IsAuthenticated`; implicitly scoped to `request.user`
- **Response:** `{"assignments": [...]}` — each entry carries `sop_id`, `sop_code`,
  `sop_title`, `job_role_id`, `box_index`, `streak_correct`, `elo_rating`,
  `memory_stability_days`, `due_since`, `suggested_difficulty`, `question_count_available`,
  `attempt_id`, `question_ids`, `targeted`, `unmastered_section_count`, `reason`
- **⚠️ A `GET` with significant write side effects:** it **creates** `QuizAttempt` rows and
  **writes** `quiz_attempt_auto_assigned` and `retraining_escalation` audit entries. The SPA
  calls it on every Learner Quiz page load (`App.jsx:1279`). Idempotency is preserved by
  reusing any existing incomplete attempt, but a non-idempotent `GET` violates HTTP semantics
  and means a page refresh can emit compliance-escalation audit records. See GAP-D1.
- **Targeting cascade:** unmastered `ChunkMastery` sections → else previously-missed questions
  → else the full approved set

### `GET /api/attempts/retraining-status/`
- **Permission:** `IsReviewerUser`
- **Response:** `{"learners": [...]}`, sorted by failed attempts desc then due date
- **Performance:** one extra `COUNT` query per unmastered row — N+1. See GAP-F1.

### `GET /api/attempts/section-mastery/`
- **Permission:** `IsReviewerUser`
- **Response:** `{"sections": [...]}` — per-section mastery for every learner, sorted by due date

---

## 5. `analytics`

### `GET /api/analytics/dashboard-summary/`
- **Permission:** ⚠️ **none declared** (`analytics/views.py:14`) — falls through to the global
  `IsAuthenticated` default, so **every authenticated user, including a plain learner, can read
  it**
- **Response:** `sops`, `processed_sops`, `questions`, `approved_questions`,
  `published_quiz_count`, `attempts`, `average_score`, `completion_rate`,
  `retraining_due_count`, `retraining_improvement`, `attempts_by_role`, `recent_activity`,
  `learner_progress`, `weak_topics`
- **🔴 Data exposure:** `learner_progress` returns **other learners' full names, job roles, SOP
  codes, scores, and pass/fail status** (`analytics/views.py:40-51`). No role gate, no scoping.
  See GAP-E3.
- **Performance:** loads every completed attempt into memory to compute
  `retraining_improvement` (`analytics/views.py:87`). See GAP-F1.

### `GET /api/analytics/recommended-refresher/`
- **Permission:** `IsAuthenticated`, explicitly scoped to `request.user` — correct
- **Response:** `{"recommendation": {...}|null}`

---

## 6. `ai_engine`

### `POST /api/ai_engine/generate/`
- **Permission:** `IsAdminUser`
- **Request:** `{"sop": int, "job_role": int, "count": int}` — `count` clamped to 1–20
- **Response `201`:** `{"questions": [...], "source": "nvidia_nim"|"mock"|"mixed",
  "skipped_duplicates": int}`
- **Errors:** `400` missing `sop`/`job_role`, non-integer `count`, or SOP with no chunks ·
  `404` unknown SOP or job role · `403` non-admin
- **Async:** `.delay(...).get(timeout=120)` — dispatched to Celery but **awaited synchronously**
- **DB impact:** creates `Question` + `Option` rows in `draft` status; writes one
  `questions_generated` audit entry

> 🔴 **DOCUMENTED BUT NOT IMPLEMENTED — `difficulty` is silently ignored.** The SPA renders a
> Difficulty selector and sends the value (`services/api.js:181`, `App.jsx:903`), but
> `generate_quiz` never reads `request.data["difficulty"]` and never forwards it
> (`ai_engine/views.py:16-47`). A repo-wide grep finds no `difficulty` key anywhere in
> `ai_engine/views.py` or `tasks.py` beyond reading it *back* off the model's own output.
> Difficulty is whatever the LLM self-assigns per question. The control is dead UI. See GAP-A4.

### `POST /api/ai_engine/sop-chat/`
- **Permission:** `IsAuthenticated` — deliberately open to all roles, per the view docstring
- **Request:** `{"sop": int, "question": str}` — question capped at 500 chars
- **Response `200`:** `{"answer": str, "sections_used": [str], "source":
  "nvidia_nim"|"mock", "sop_id": int, "sop_code": str}`
- **Errors:** `400` missing fields / over-length / unprocessed SOP · `404` unknown SOP
- **Async:** `.delay(...).get(timeout=60)`
- **DB impact:** writes a `sop_chat_query` audit entry containing the learner's full question text
- **Note:** any authenticated user may query **any** SOP, including ones outside their job role.

---

## 7. `audit`

### `GET /api/audit/logs/` · `GET /api/audit/logs/{pk}/`
- **Permission:** `IsAdminUser` — read-only viewset, no write routes exposed
- **Fields:** `id`, `user`, `username`, `action`, `action_label`, `object_type`, `object_id`,
  `summary`, `details`, `created_at`
- **No filtering, no date range, no pagination** — returns the entire trail, which grows
  unboundedly. See GAP-F3.

### `GET /api/audit/logs/export/`
- **Permission:** `IsAdminUser`
- **Response `200`:** `text/csv`, `Content-Disposition: attachment;
  filename=gxp-audit-log.csv`, header row
  `Timestamp,User,Action,Object Type,Object ID,Summary,Details`
- **Note:** the `Details` column is a raw `str(dict)`, not valid nested CSV/JSON, so
  machine-parsing the export is unreliable.
- **CSV injection:** values are written unescaped via `csv.writer`; a summary beginning `=`,
  `+`, `-`, or `@` would be interpreted as a formula by Excel. `summary` is server-composed,
  but `details` embeds user-supplied chat questions. See GAP-E8.

---

## 8. Django admin

### `/admin/`
Standard Django admin, registered for `SOPDocument`, `SOPChunk`, `Question`, `Option`,
`QuizAttempt`, `AttemptAnswer`, and `AuditLog`. Requires `is_staff`.
`AuditLog` is registered read-only/append-only (`audit/admin.py`). `TopicMastery` and
`ChunkMastery` are **not** registered.

---

## 9. Endpoint summary

| App | URL patterns | Distinct method+path operations |
|---|---:|---:|
| `accounts` | 7 | 11 |
| `sops` | 5 | 9 |
| `quiz` | 6 | 12 |
| `attempts` | 7 | 11 |
| `analytics` | 2 | 2 |
| `ai_engine` | 2 | 2 |
| `audit` | 3 | 3 |
| **Total** | **32** | **~50** |

Permission distribution: 1 public (`login`), 3 `IsReviewerUser`, 4 `IsAdminUser` read +
~14 `IsAdminUser` write, remainder `IsAuthenticated`.
