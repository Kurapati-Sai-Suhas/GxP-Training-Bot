# Security Controls and Residual Risks
## GxP Training Bot

**Scope:** what is actually implemented in this repository, and what is not. No claim of
regulatory compliance or certification is made anywhere in this document. Controls are
described as *implemented*, *partial*, or *gap*, with the evidence for each.

**Last verified:** 176 backend tests passing.

---

## 1. Assessment integrity

| Control | Status | Implementation | Verified by |
|---|---|---|---|
| Answer key withheld from learners | **Implemented** | `LearnerQuestionSerializer` / `LearnerOptionSerializer` omit `is_correct` and `explanation`; `QuestionViewSet.get_serializer_class()` selects by role, not by a client-supplied parameter | `AnswerKeyConfidentialityTests` (11 tests, incl. raw-body inspection) |
| Options endpoint not a side channel | **Implemented** | `OptionViewSet` requires `IsReviewerUser` for reads | `test_learner_cannot_read_the_options_endpoint` |
| Learners see only approved content | **Implemented** | `get_queryset()` forces `status="approved"` for non-reviewers, regardless of query params | `test_learner_cannot_request_drafts_explicitly` |
| Server-side grading | **Implemented** (pre-existing) | Correctness re-derived from `Option.is_correct` in the DB; client input never trusted | `test_submit_scores_correctly_and_returns_full_answer_detail` |
| One submission per attempt | **Implemented** | Atomic conditional `UPDATE ... WHERE completed_at IS NULL` claims the attempt; losers get `409` | `CompletedAttemptImmutabilityTests` (10 tests) |
| Concurrent-submission safety | **Implemented** | Compare-and-set rather than read-then-check, so two racing requests cannot both proceed. Backend-agnostic (works on SQLite, which has no row locking) | design; not load-tested |

**Residual risk.** Post-submission the correct answers are disclosed (by design — it is the
teaching moment). A learner may therefore start a *new* attempt already knowing the answers.
This is normal for training systems and is visible in the record: each attempt is stored and
audited separately. Attempt limits are not implemented.

---

## 2. Record integrity

| Control | Status | Implementation | Verified by |
|---|---|---|---|
| Approved content immutable | **Implemented** | `update`/`partial_update`/`destroy` return `403` when `status="approved"`; covers PUT and PATCH separately | `ApprovedContentImmutabilityTests` (6 tests) |
| Signature bound to content | **Implemented** | `Question.content_hash` — SHA-256 over question text, explanation, difficulty and the full ordered option set including the answer key; computed at approval | `ElectronicSignatureBindingTests` (8 tests) |
| Signature identifies signer and time | **Implemented** | `approved_by`, `approved_at`, plus `content_hash`/`signed_by`/`signed_at` in the audit record | `test_content_hash_is_recorded_in_the_audit_trail` |
| Out-of-band tampering detectable | **Implemented** | `Question.signature_is_intact()` recomputes and compares; catches changes made via ORM, admin, or migration | `test_signature_detects_content_changed_behind_the_api`, `test_signature_detects_a_changed_answer_key` |
| Rejection clears the binding | **Implemented** | Hash/approver/timestamp nulled on reject, so a rejected question is never mistaken for signed content | `test_rejecting_clears_the_signature_binding` |
| Destructive reprocessing blocked | **Partial** | `POST /process/` returns `409` when approved questions exist, preventing chunk deletion from cascading away `ChunkMastery` and orphaning approved questions | `test_reprocessing_is_blocked_once_approved_questions_exist` |

**Residual risk — significant.** `signature_is_intact()` is a *detection* backstop, not
prevention: nothing calls it automatically, so a tampered question is only discovered if
something asks. There is no scheduled integrity sweep and no question revision history — the
correction path is reject → edit → re-approve, which produces a new signature but does not
retain the superseded version. **SOP versioning is not implemented**; reprocessing is blocked
rather than versioned, which prevents data loss but does not support the legitimate
"procedure was revised" workflow.

---

## 3. Authentication and session management

| Control | Status | Notes |
|---|---|---|
| Password hashing | **Implemented** | Django defaults; 4 validators enabled |
| Token authentication | **Implemented** | DRF `TokenAuthentication` |
| Login rate limiting | **Implemented** | `LoginRateThrottle`, 10/min per IP, env-tunable. Throttles regardless of outcome, so a correct guess on attempt N+1 is still blocked (`test_throttle_also_blocks_a_subsequent_correct_password`) |
| E-signature rate limiting | **Implemented** | `ESignatureRateThrottle`, 20/min per user |
| Token expiry / rotation | **GAP** | DRF tokens never expire; `get_or_create` returns the same key indefinitely |
| Token storage | **GAP** | `localStorage`, readable by any XSS |
| MFA | **GAP** | Not implemented |
| Account lockout | **GAP** | Rate limiting only; no lockout, and throttling counts all attempts rather than failures |
| Password change / reset | **GAP** | No endpoint exists; users are created only by `seed_demo` or the Django admin |

**Residual risk.** A stolen token is valid forever. This is the most significant remaining
authentication weakness and should be addressed before any real deployment.

---

## 4. Authorization

| Control | Status | Notes |
|---|---|---|
| Role model | **Implemented** | `is_staff` / `Admin` group / `SME` group, resolved by `is_admin()` and `is_reviewer()` — single definition shared by permission classes, serializer selection and queryset scoping |
| Endpoint permissions | **Implemented** | Per-action `get_permissions()`; tested from both the permitted and denied side |
| Attempt ownership | **Implemented** | Queryset scoping plus an explicit ownership check on submit |
| Cross-learner data isolation | **Implemented** | `dashboard-summary` scopes `learner_progress` to the requesting user unless they are a reviewer; non-identifying aggregates remain shared | `DashboardAccessControlTests` (6 tests) |
| Uploaded document access | **Implemented** | Unauthenticated `/media/` route removed entirely; files served only via `GET /api/sops/documents/{id}/download/` behind normal auth | `SopFileAccessControlTests` (5 tests) |
| Separation of duties | **GAP** | An Admin can both generate and approve the same question — `is_reviewer()` accepts `is_staff`. Nothing requires the approver to differ from the initiator |
| Department/role scoping of SOPs | **GAP** | Any authenticated user can read any SOP's chunks and download any SOP file |

**Note on the download control.** SOP *text* is already readable by every authenticated role via
`/api/sops/chunks/`, so restricting the source file more tightly than its own extracted content
would be theatre. The control restored here is **authentication**, not role separation.

---

## 5. Input handling and injection

| Control | Status | Notes |
|---|---|---|
| SQL injection | **Implemented** | ORM throughout; no raw SQL anywhere |
| XSS | **Partial** | React escapes by default; no `dangerouslySetInnerHTML`. No CSP header is set |
| Path traversal | **Implemented** | File access is by primary key against the model; no user-supplied path reaches the filesystem |
| Upload validation | **Partial** | Extension allow-list + 20 MB cap, enforced server-side. **No content sniffing, no AV scan** |
| CSRF | **Implemented** | Middleware enabled; DRF enforces for session auth; `CSRF_TRUSTED_ORIGINS` configurable |
| CORS | **Implemented** | Explicit allow-list from environment, never `ALLOW_ALL` |
| Prompt injection | **GAP** | SOP text is interpolated directly into prompts. Mitigated for quizzes by the SME approval gate; **not mitigated for the chatbot**, whose output reaches learners with no human review |
| CSV injection in audit export | **GAP** | Values written unescaped; `details` embeds user-supplied chat questions. A question beginning `=`/`+`/`-`/`@` executes as a formula in Excel |

---

## 6. Transport and configuration

| Control | Status | Notes |
|---|---|---|
| Production server | **Implemented** | gunicorn, 3 workers, `--timeout 180` (deliberately above the 120s synchronous LLM wait) |
| `DEBUG=False` in production | **Implemented** | `docker-compose.prod.yml` |
| Weak-secret fail-fast | **Implemented** | Startup raises `ImproperlyConfigured` if `DEBUG=False` with the development `SECRET_KEY`, or with a wildcard/empty `ALLOWED_HOSTS` — verified to refuse to boot |
| Secure cookies / HSTS / nosniff / referrer policy | **Implemented** | Applied only when `DEBUG=False`, so local HTTP development is unaffected. HSTS is opt-in via env |
| Proxy TLS header | **Implemented** | `SECURE_PROXY_SSL_HEADER` only trusted when `USE_X_FORWARDED_PROTO=True`, so a client cannot spoof "I'm on HTTPS" |
| Django deployment checklist | **Implemented** | `manage.py check --deploy --fail-level WARNING` passes with **zero** issues and runs in CI |
| Static file serving | **Implemented** | WhiteNoise with compressed manifest storage |
| TLS termination | **GAP** | Not included; expected from an upstream proxy |
| Secrets management | **GAP** | Environment variables and `.env` files; no managed secret store |

---

## 7. Observability

| Control | Status | Notes |
|---|---|---|
| Logging configuration | **Implemented** | Console handler, env-tunable level, dedicated `ai_engine` and `sops` loggers |
| LLM failure visibility | **Implemented** | Every retry and every fallback is logged with an error category (`rate_limit`, `authentication_failure`, `timeout`, `invalid_model_output`, `validation_failure`, `provider_error`, `connection_error`, `model_not_found`, `unknown`). Previously all of these were silently swallowed |
| Request IDs / correlation | **GAP** | Not implemented |
| Metrics / tracing / APM | **GAP** | Not implemented |
| Security event logging | **Partial** | Auth failures are visible via Django's request logger; there is no dedicated security log |

---

## 8. Auditability

| Event | Audited | Added this sprint |
|---|---|---|
| SOP uploaded / processed / process failed | Yes | — |
| **SOP metadata updated** | Yes | ✅ |
| **SOP deleted** (with cascade impact counts) | Yes | ✅ |
| Questions generated | Yes | — |
| **Question edited** (with changed fields and previous values) | Yes | ✅ |
| **Question deleted** | Yes | ✅ |
| Question approved / rejected | Yes | enriched with content hash, signer, timestamp |
| Quiz attempt submitted | Yes | — |
| **Resubmission blocked** | Yes | ✅ |
| SOP chat query | Yes | — |
| Auto-assignment, retraining escalation | Yes | — |
| **Job role created / updated / deleted** | Yes | ✅ |
| **Learner profile changed** (with previous job role) | Yes | ✅ |

**Residual risk — significant.** Append-only enforcement is still **Django-admin-only**
(`has_add/change/delete_permission` return `False`). There is no database trigger, no WORM
storage, no hash chaining, and no digital signature over the log. Any code path or database
user can modify audit rows. `AuditLog.user` remains `on_delete=SET_NULL`, so deleting a user
anonymises their entire history.

---

## 9. Known unfixed issues

Carried forward from `GAP_ANALYSIS.md`, in rough risk order:

1. **Tokens never expire** and live in `localStorage` (§3).
2. **Audit trail is not tamper-evident** (§8) — the strongest remaining GxP gap.
3. **No SOP versioning** — reprocessing is blocked rather than versioned, so revising a
   procedure has no supported workflow.
4. **No separation of duties** — an Admin can generate and approve the same content.
5. **No training assignment or completion model** — the system still cannot answer "is this
   person qualified?"
6. **Prompt injection unmitigated** for the chatbot path.
7. **CSV injection** in the audit export.
8. **No content sniffing or AV** on uploads.
9. **No pagination** — the audit log is returned in full and grows without bound.
10. **No question revision history** — corrections replace rather than supersede.

---

## 10. Reporting

This is a student/bootcamp project with no production deployment. Security issues should be
raised as GitHub issues on the repository.
