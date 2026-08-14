# Requirements Traceability Matrix
## GxP Training Bot — commit `d783815`

Each functional requirement is traced **Requirement → API → Service/Logic → Model → Test**.
Requirement IDs match `docs/SRS.md` §4.

**Test-coverage key:** ✅ direct automated test · ⚠️ indirect/partial coverage · ❌ no test.
Backend suite: **89 tests, all passing** (`manage.py test`, verified). Frontend: **0 tests**.

> This matrix records coverage **as found at audit time** (commit `d783815`), which is what makes
> the ❌ rows meaningful. Several of those gaps were closed in the sprints that followed — the
> suite is now 176 tests. See [`CHANGELOG.md`](CHANGELOG.md) and [`TESTING.md`](TESTING.md).

---

## 1. Authentication

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-001 | Token login | `POST /api/accounts/login/` | `accounts/views.py:58` | `authtoken.Token` | `test_login_returns_token_and_learner_profile`, `test_login_rejects_bad_password` | ✅ |
| FR-002 | Logout invalidates tokens | `POST /api/accounts/logout/` | `accounts/views.py:76` | `Token` | — | ❌ |
| FR-003 | Current identity + role tiers | `GET /api/accounts/me/` | `_serialize_current_user` `accounts/views.py:30` | `User`, `LearnerProfile`, `Group` | `test_me_*` (5 tests incl. all three role tiers) | ✅ |
| FR-004 | SPA session restore from `localStorage` | `GET /api/accounts/me/` | `App.jsx:2107`, `services/api.js:4` | — | — | ❌ |

> FR-002 gap: nothing verifies that a token is actually rejected after logout.

---

## 2. Authorization

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-010 | Admin = `is_staff` or `"Admin"` group | all admin-gated routes | `IsAdminUser` `accounts/permissions.py:7` | `Group` | `test_plain_learner_cannot_create_job_role`, `test_admin_can_create_job_role`, `test_generate_rejected_for_plain_learner`, `test_upload_rejected_for_plain_learner` | ✅ |
| FR-011 | Reviewer = Admin or `"SME"` group | approve/reject, retraining status | `IsReviewerUser` `accounts/permissions.py:19` | `Group` | `test_approve_succeeds_for_sme_reviewer`, `test_approve_rejected_for_plain_learner`, `test_retraining_status_requires_reviewer_or_admin`, `test_section_mastery_status_requires_reviewer_or_admin` | ✅ |
| FR-012 | Learners see only their own attempts | `/api/attempts/quiz-attempts/`, `/answers/` | `get_queryset` `attempts/views.py:93,203` | `QuizAttempt` | `test_submit_hidden_from_non_owner_learner`, `test_admin_can_view_but_not_submit_on_behalf_of_a_learner`, `test_auto_assigned_scoped_to_requesting_learner` | ✅ |
| FR-013 | Role-gated navigation | — (client) | `App.jsx:86,2253` | — | — | ❌ |
| FR-014 | Role groups seeded by migration | — | `accounts/migrations/0002` | `Group` | — | ⚠️ indirectly, via every RBAC test |

> FR-013 is **defence-in-depth only** — every gated action is independently enforced
> server-side, and tests prove it from both the permitted and denied side.

---

## 3. Job roles & learner profiles

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-020 | JobRole CRUD (read all / write Admin) | `/api/accounts/job-roles/` | `JobRoleViewSet` `accounts/views.py:10` | `JobRole` | `RoleBasedWritePermissionTests` (2) | ✅ |
| FR-021 | LearnerProfile CRUD (read all / write Admin) | `/api/accounts/learner-profiles/` | `accounts/views.py:20` | `LearnerProfile` | — | ❌ |

> FR-021 has no test at all, despite being the mechanism that assigns a learner to a job role —
> which determines which quizzes they can see. Untested authorization boundary.

---

## 4. SOP document management

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-030 | Upload with type + size validation | `POST /api/sops/documents/` | `validate_file` `sops/serializers.py:39` | `SOPDocument` | `test_upload_rejected_for_unsupported_extension`, `test_upload_rejected_for_oversized_file`, `test_upload_rejected_for_plain_learner` | ✅ |
| FR-031 | List/retrieve SOPs | `GET /api/sops/documents/` | `SOPDocumentViewSet` | `SOPDocument` | `test_upload_then_process_extracts_and_persists_chunks` | ⚠️ |
| FR-032 | Delete SOP (Admin) | `DELETE /api/sops/documents/{id}/` | default `ModelViewSet` | cascade | — | ❌ |
| FR-033 | Process: extract → chunk → persist | `POST .../{id}/process/` | `process_sop_document_task` `sops/tasks.py:9` | `SOPChunk` | `test_upload_then_process_extracts_and_persists_chunks` (incl. stale-prefetch regression), `test_process_corrupted_file_marks_sop_failed` | ✅ |
| FR-034 | 3-tier chunking cascade | — | `chunk_text` `sops/services.py:121` | `SOPChunk.chunking_strategy` | `test_splits_on_detected_section_headings`, `test_falls_back_to_semantic_chunking_when_no_headings_but_api_key_present`, `test_falls_back_to_length_based_split_when_no_headings_and_no_api_key`, `test_splits_an_overlong_section_further`, `test_numeric_headings_are_also_detected` | ✅ |
| FR-035 | Read chunks | `GET /api/sops/chunks/` | `SOPChunkViewSet` | `SOPChunk` | — | ❌ |

> **FR-032 is the highest-risk untested requirement.** A cascading delete of an SOP removes its
> questions, attempts, answers, and mastery records with no audit entry (GAP-E4) — and no test
> asserts anything about the behaviour.

---

## 5. AI content generation

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-040 | Generate role-specific MCQs from chunks | `POST /api/ai_engine/generate/` | `generate_quiz_task` `ai_engine/tasks.py:15` → `generate_questions` | `Question`, `Option` | `test_generate_falls_back_to_mock_and_creates_draft_questions`, `test_generate_requires_sop_and_job_role`, `test_generate_rejects_sop_with_no_chunks` | ⚠️ |
| FR-041 | Offline fallback on any failure | — | `generate_questions` `ai_engine/services.py:170` | `generation_source` | `test_generate_falls_back_to_mock_and_creates_draft_questions` | ⚠️ |
| FR-042 | 3 retries, linear backoff | — | `ai_engine/services.py:98-115` | — | — | ❌ |
| FR-043 | Content-signature de-duplication | — | `ai_engine/tasks.py:33-51` | — | `test_generate_skips_duplicates_on_a_repeat_run` | ✅ |
| FR-044 | Capture LLM self-reported confidence | — | `_normalize_confidence` `ai_engine/services.py:53` | `Question.confidence_score` | `test_low_confidence_question_excluded_from_mastery_scoring` | ⚠️ |
| FR-045 | Record which path produced each question | — | `ai_engine/tasks.py:62` | `Question.generation_source` | — | ❌ |
| FR-046 | *(claimed)* difficulty control | `POST /api/ai_engine/generate/` | **NOT IMPLEMENTED** | — | — | ❌ |

> **FR-040/041 are only ⚠️:** every test forces `NVIDIA_API_KEY=""`, which returns before the
> HTTP client is ever constructed. The live NIM path, the JSON parsing (`_normalize_drafts`),
> the markdown-fence stripping, and the retry loop (FR-042) are **entirely untested** — that is
> the majority of the AI code by line count. See GAP-B5.
>
> **FR-046 is DOCUMENTED BUT NOT IMPLEMENTED** — the UI sends `difficulty`; the view never
> reads it. See GAP-A4.

---

## 6. Review workflow (electronic signature)

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-050 | Approve with password e-signature | `PATCH .../questions/{id}/approve/` | `_verify_e_signature` `quiz/views.py:35` | `Question.status` | `test_approve_succeeds_for_sme_reviewer`, `test_approve_succeeds_for_admin`, `test_approve_rejected_without_e_signature_password`, `test_approve_rejected_for_wrong_e_signature_password`, `test_approve_requires_authentication` | ✅ |
| FR-051 | Reject with password e-signature | `PATCH .../questions/{id}/reject/` | same | `Question.status` | `test_reject_succeeds_with_e_signature_password` | ✅ |
| FR-052 | Edit question content (Admin) | `PATCH .../questions/{id}/` | `ModelViewSet` | `Question` | `test_sme_reviewer_cannot_create_raw_questions` (create only) | ❌ |
| FR-053 | Filter questions by sop/role/status | `GET /api/quiz/questions/?…` | `get_queryset` `quiz/views.py:14` | — | `test_question_list_filters_by_job_role_and_status` | ✅ |
| FR-054 | Only approved questions reach learners | `GET .../questions/?status=approved` | client-supplied filter | `Question.status` | `test_question_list_filters_by_job_role_and_status` | ⚠️ |

> **FR-050/051 are the best-tested requirements in the system** — 6 tests covering both success
> paths and all three failure modes.
>
> **FR-052 is untested and unguarded**: no test asserts that an approved question cannot be
> edited, and the server permits it (GAP-E4). The lock exists only in the SPA.
>
> **FR-054 is ⚠️ by design flaw**: "only approved questions reach learners" is enforced by the
> *client* passing `status=approved`. The endpoint will happily return drafts to a learner who
> omits the filter.

---

## 7. Assessment

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-060 | Start an attempt (learner-scoped) | `POST /api/attempts/quiz-attempts/` | `perform_create` `attempts/views.py:99` | `QuizAttempt` | `_start_attempt` asserts `learner == user` | ✅ |
| FR-061 | Submit + server-side grading | `POST .../{id}/submit/` | `attempts/views.py:102` | `AttemptAnswer`, `score` | `test_submit_scores_correctly_and_returns_full_answer_detail` (incl. stale-prefetch regression), `test_submit_requires_authentication` | ✅ |
| FR-062 | Ownership enforced on submit | same | `attempts/views.py:105` | — | `test_admin_can_view_but_not_submit_on_behalf_of_a_learner`, `test_submit_hidden_from_non_owner_learner` | ✅ |
| FR-063 | Result review w/ correct answer + explanation | — (client) | `App.jsx:1435-1481` | — | — | ❌ |
| FR-064 | Prevent resubmission of a completed attempt | — | **NOT IMPLEMENTED** | — | — | ❌ |

> **FR-064 does not exist.** No guard on `completed_at`; resubmission overwrites score and
> mastery indefinitely. See GAP-A2.

---

## 8. Adaptive retraining

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-070 | Whole-SOP mastery per completed attempt | `POST .../submit/` | `attempts/views.py:147-175` | `TopicMastery` | `test_topic_mastery_created_and_advances_on_correct_answers`, `test_topic_mastery_scores_whole_attempt_not_last_answer`, `test_topic_mastery_resets_on_a_wrong_answer` | ✅ |
| FR-071 | Elo ability + question difficulty | — | `apply_elo_update` `attempts/services.py:37` | `elo_rating` ×2 | `AdaptiveEloRatingTests` (5) | ✅ |
| FR-072 | FSRS-derived review scheduling | — | `attempts/fsrs.py` | `fsrs_stability/difficulty`, `next_eligible_at` | `FSRSAlgorithmTests` (7) + `AdaptiveFSRSSchedulingTests` (3) | ✅ |
| FR-073 | Per-section mastery | `POST .../submit/` | `attempts/views.py:182-190` | `ChunkMastery` | `SectionMasteryTests` (7) incl. the exact motivating scenario and a double-Elo regression guard | ✅ |
| FR-074 | Auto-assign due retraining (idempotent) | `GET /api/attempts/auto-assigned/` | `attempts/views.py:212` | `QuizAttempt` | `test_auto_assigned_excludes_mastered_topics_even_if_due`, `test_auto_assigned_excludes_topics_not_yet_due`, `test_auto_assigned_requires_authentication`, `test_auto_assigned_scoped_to_requesting_learner` | ✅ |
| FR-075 | Target retest at unmastered sections | same | `attempts/views.py:286-311` | `ChunkMastery` | `test_auto_assigned_targets_unmastered_section_questions_first` | ✅ |
| FR-076 | Escalate after 3 failed attempts | same | `attempts/views.py:268-280` | `AuditLog` | `test_retraining_status_lists_unmastered_learners_and_flags_escalation` | ✅ |
| FR-077 | Reviewer view of retraining loops | `GET .../retraining-status/` | `attempts/views.py:354` | `TopicMastery` | 2 tests incl. permission boundary + `test_memory_stability_surfaced_in_retraining_status` | ✅ |
| FR-078 | Reviewer view of section mastery | `GET .../section-mastery/` | `attempts/views.py:389` | `ChunkMastery` | `test_section_mastery_status_requires_reviewer_or_admin`, `test_section_mastery_status_lists_unmastered_sections_only` | ✅ |
| FR-079 | Personal refresher recommendation | `GET /api/analytics/recommended-refresher/` | `analytics/views.py:127` | `AttemptAnswer` | `RecommendedRefresherTests` (3) incl. cross-learner isolation | ✅ |
| FR-080 | Confidence-aware, Elo-weighted pass signal | — | `_pass_signal_from_pairs` `attempts/views.py:50` | — | `test_hard_question_weighted_more_than_easy_for_mastery`, `test_low_confidence_question_excluded_from_mastery_scoring` | ✅ |

> **This is the best-covered subsystem in the project** — 30 tests in `attempts/tests.py`, with
> algorithm-level unit tests separated from end-to-end submission tests, and two regression
> guards written for bugs that were actually found and fixed.

---

## 9. RAG SOP chatbot

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-090 | Ask a free-text question about one SOP | `POST /api/ai_engine/sop-chat/` | `answer_sop_question_task` `ai_engine/tasks.py:100` | `SOPChunk` | `test_chat_requires_authentication`, `test_chat_requires_sop_and_question`, `test_chat_rejects_an_overlong_question`, `test_chat_rejects_sop_with_no_chunks` | ✅ |
| FR-091 | Lexical chunk retrieval | — | `select_relevant_chunks` `ai_engine/services.py:204` | — | `test_ranks_the_chunk_with_the_most_keyword_overlap_first`, `test_falls_back_to_document_order_when_nothing_matches` | ✅ |
| FR-092 | Grounded-only prompt | — | `build_sop_chat_prompt` `ai_engine/services.py:218` | — | — | ❌ |
| FR-093 | Deterministic offline chat fallback | — | `answer_sop_question_offline` `:267` | — | `test_chat_falls_back_to_offline_and_answers_from_sop_text` | ✅ |
| FR-094 | Log every chat query | — | `ai_engine/tasks.py:113` | `AuditLog` | `test_chat_writes_an_audit_log_entry` | ✅ |

---

## 10. Analytics

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-100 | Aggregate dashboard | `GET /api/analytics/dashboard-summary/` | `analytics/views.py:15` | multiple | `test_weak_topics_ranks_lowest_correct_rate_first` | ⚠️ |
| FR-101 | Weak-topic ranking | same | `analytics/views.py:53-72` | `AttemptAnswer` | `test_weak_topics_ranks_lowest_correct_rate_first` | ✅ |
| FR-102 | Retraining-improvement metric | same | `analytics/views.py:86-103` | `QuizAttempt` | — | ❌ |

> FR-100 is ⚠️: only the `weak_topics` slice is asserted. The other 13 response keys — including
> `learner_progress`, which leaks cross-learner data (GAP-E3) — are untested, and **no test
> asserts who is allowed to call this endpoint**.

---

## 11. Audit trail

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-110 | Write attributed audit entries | — | `log_action` `audit/models.py:39` | `AuditLog` | `test_approve_writes_an_attributed_audit_log_entry`, `test_chat_writes_an_audit_log_entry` | ⚠️ |
| FR-111 | Admin-only audit read | `GET /api/audit/logs/` | `AuditLogViewSet` `audit/views.py:12` | `AuditLog` | `test_audit_log_read_requires_admin` (all 3 tiers) | ✅ |
| FR-112 | CSV export for inspectors | `GET /api/audit/logs/export/` | `audit/views.py:20` | `AuditLog` | `test_audit_log_export_csv_requires_admin`, `test_audit_log_export_csv_returns_rows_for_admin` | ✅ |
| FR-113 | Append-only enforcement | — (admin site) | `audit/admin.py:13-21` | — | `test_audit_log_is_append_only_via_admin_site` | ⚠️ |
| FR-114 | Audit deletions & content edits | — | **NOT IMPLEMENTED** | — | — | ❌ |

> FR-110 is ⚠️: only 2 of the 10 action types are asserted. `sop_uploaded`, `sop_processed`,
> `sop_process_failed`, `questions_generated`, `question_rejected`, `quiz_attempt_submitted`,
> and `quiz_attempt_auto_assigned` have no direct assertion.
>
> FR-113 is ⚠️: the test verifies the three `ModelAdmin` methods return `False` — i.e. it tests
> the *Django admin UI*, not the underlying store. Nothing prevents programmatic mutation.
>
> **FR-114 does not exist** — see GAP-E4.

---

## 12. Administration

| ID | Requirement | API | Logic | Model | Test | Cov |
|---|---|---|---|---|---|---|
| FR-120 | Django admin for core entities | `/admin/` | `*/admin.py` | all | `test_audit_log_is_append_only_via_admin_site` | ⚠️ |
| FR-121 | Demo data seeding | — | `seed_demo.py` | all | — | ❌ |

> `TopicMastery` and `ChunkMastery` are not registered in the admin, so mastery state cannot be
> inspected or corrected through any UI.

---

## 13. Coverage summary

| Area | Requirements | ✅ | ⚠️ | ❌ |
|---|---:|---:|---:|---:|
| Authentication | 4 | 2 | 0 | 2 |
| Authorization | 5 | 3 | 1 | 1 |
| Roles & profiles | 2 | 1 | 0 | 1 |
| SOP management | 6 | 3 | 1 | 2 |
| AI generation | 7 | 1 | 3 | 3 |
| Review / e-signature | 5 | 3 | 1 | 1 |
| Assessment | 5 | 3 | 0 | 2 |
| Adaptive retraining | 11 | 11 | 0 | 0 |
| RAG chatbot | 5 | 4 | 0 | 1 |
| Analytics | 3 | 1 | 1 | 1 |
| Audit | 5 | 2 | 2 | 1 |
| Administration | 2 | 0 | 1 | 1 |
| **Total** | **60** | **34** | **10** | **16** |

### Where coverage is strong
Adaptive retraining (11/11), the e-signature workflow, RBAC boundaries (tested from both
sides), and the chunking cascade. Two regression tests exist for real bugs that were found and
fixed — stale `prefetch_related` caches in SOP processing and quiz submission — and both are
named for the bug they prevent.

### Where coverage is weakest, ranked by risk
1. **The live LLM path** — all fallback tests bypass it via an empty API key, so JSON parsing,
   fence stripping, normalisation, and the retry loop are untested (GAP-B5).
2. **Destructive operations** — SOP delete (FR-032) and question edit (FR-052) have no tests and
   no audit logging.
3. **`dashboard-summary` authorization** — no test asserts who may call the endpoint that leaks
   cross-learner data.
4. **The entire frontend** — 3,837 lines, zero tests.
5. **Requirements that do not exist but should** — FR-064 (resubmission guard), FR-114 (audit
   deletions), FR-046 (difficulty control).
