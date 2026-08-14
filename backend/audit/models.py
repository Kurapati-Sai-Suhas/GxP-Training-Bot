from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only compliance trail (21 CFR Part 11 style): who did what, to which
    record, and when. Rows are never edited or deleted once written (see admin.py)."""

    ACTION_CHOICES = [
        ("sop_uploaded", "SOP Uploaded"),
        ("sop_processed", "SOP Processed"),
        ("sop_process_failed", "SOP Processing Failed"),
        ("sop_updated", "SOP Metadata Updated"),
        ("sop_deleted", "SOP Deleted"),
        ("questions_generated", "Questions Generated"),
        ("question_edited", "Question Edited"),
        ("question_deleted", "Question Deleted"),
        ("question_approved", "Question Approved"),
        ("question_rejected", "Question Rejected"),
        ("quiz_attempt_submitted", "Quiz Attempt Submitted"),
        ("quiz_attempt_resubmit_blocked", "Quiz Attempt Resubmission Blocked"),
        ("sop_chat_query", "SOP Chat Query"),
        ("quiz_attempt_auto_assigned", "Quiz Attempt Auto-Assigned"),
        ("retraining_escalation", "Retraining Escalation"),
        ("job_role_changed", "Job Role Changed"),
        ("learner_profile_changed", "Learner Profile Changed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    summary = models.CharField(max_length=255)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.get_action_display()} by {self.user or 'system'}"


def log_action(user, action, obj=None, summary="", details=None):
    AuditLog.objects.create(
        user=user if (user is not None and getattr(user, "is_authenticated", False)) else None,
        action=action,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=getattr(obj, "id", None),
        summary=summary,
        details=details or {},
    )
