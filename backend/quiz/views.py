from django.utils import timezone
from rest_framework import decorators, permissions, response, viewsets

from accounts.permissions import IsAdminUser, IsReviewerUser, is_reviewer
from accounts.throttling import ESignatureRateThrottle
from audit.models import log_action

from .models import Option, Question
from .serializers import (
    LearnerQuestionSerializer,
    OptionSerializer,
    QuestionSerializer,
)


class QuestionViewSet(viewsets.ModelViewSet):
    queryset = Question.objects.select_related("sop", "job_role", "source_chunk").prefetch_related("options")
    serializer_class = QuestionSerializer

    def get_serializer_class(self):
        """Reviewers and Admins get the full record; everyone else gets the learner view.

        This is the control that keeps the answer key off the wire during an assessment --
        see LearnerQuestionSerializer. It is chosen from the request user's role rather
        than from a query parameter so a learner cannot opt back into the full payload.
        """
        if is_reviewer(self.request.user):
            return QuestionSerializer
        return LearnerQuestionSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        sop_id = params.get("sop")
        job_role_id = params.get("job_role")
        status_param = params.get("status")
        if sop_id:
            queryset = queryset.filter(sop_id=sop_id)
        if job_role_id:
            queryset = queryset.filter(job_role_id=job_role_id)
        if status_param:
            queryset = queryset.filter(status=status_param)
        # Non-reviewers only ever see approved content, whatever they ask for. Previously
        # "learners only see approved questions" depended on the client remembering to pass
        # ?status=approved -- omitting it returned unreviewed drafts, and passing
        # ?status=draft returned them deliberately.
        if not is_reviewer(self.request.user):
            queryset = queryset.filter(status="approved")
        return queryset

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [IsReviewerUser()]
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_throttles(self):
        # Only the signature endpoints are throttled: they verify a password, so unlimited
        # calls are a guessing oracle. Ordinary reads stay unthrottled.
        if self.action in ("approve", "reject"):
            return [ESignatureRateThrottle()]
        return super().get_throttles()

    def _verify_e_signature(self, request):
        """21 CFR Part 11 electronic-signature step-up check: the reviewer must re-enter
        their own password to confirm an approve/reject decision, not just rely on the
        session/token already being authenticated. Returns an error Response, or None if
        the signature is valid."""
        password = request.data.get("password")
        if not password:
            return response.Response(
                {"error": "Password confirmation is required to approve or reject a question (electronic signature)."},
                status=400,
            )
        if not request.user.check_password(password):
            return response.Response({"error": "Incorrect password. Electronic signature not confirmed."}, status=400)
        return None

    def _reject_if_approved(self, question, verb):
        """Approved content is immutable through the ordinary edit/delete endpoints.

        An approved question carries an electronic signature bound to its exact wording
        (Question.content_hash). Editing it in place would leave that signature vouching
        for content the reviewer never saw, and deleting it would remove the record an
        attempt was scored against. Correcting approved content is a reject-then-redraft
        workflow, not an in-place edit.
        """
        if question.status == "approved":
            return response.Response(
                {
                    "error": (
                        f"This question is approved and electronically signed, so it cannot be {verb}. "
                        "Reject it first to return it to draft, then edit and re-approve."
                    )
                },
                status=403,
            )
        return None

    def update(self, request, *args, **kwargs):
        blocked = self._reject_if_approved(self.get_object(), "edited")
        if blocked is not None:
            return blocked
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        blocked = self._reject_if_approved(self.get_object(), "edited")
        if blocked is not None:
            return blocked
        return super().partial_update(request, *args, **kwargs)

    def perform_update(self, serializer):
        before = {
            "question_text": serializer.instance.question_text,
            "explanation": serializer.instance.explanation,
            "difficulty": serializer.instance.difficulty,
        }
        question = serializer.save()
        changed = sorted(k for k, v in before.items() if getattr(question, k) != v)
        log_action(
            self.request.user, "question_edited", question,
            summary=f"Edited question #{question.id}: {question.question_text[:80]}",
            details={"fields_changed": changed, "previous": {k: before[k] for k in changed}},
        )

    def destroy(self, request, *args, **kwargs):
        question = self.get_object()
        blocked = self._reject_if_approved(question, "deleted")
        if blocked is not None:
            return blocked
        log_action(
            request.user, "question_deleted", question,
            summary=f"Deleted question #{question.id}: {question.question_text[:80]}",
            details={
                "sop_code": question.sop.sop_code,
                "job_role": question.job_role.name,
                "status": question.status,
            },
        )
        return super().destroy(request, *args, **kwargs)

    @decorators.action(detail=True, methods=["patch"])
    def approve(self, request, pk=None):
        signature_error = self._verify_e_signature(request)
        if signature_error is not None:
            return signature_error
        question = self.get_object()
        # Bind the signature to the exact content being approved. Computed before the
        # status flips so the digest covers what the reviewer actually read.
        content_hash = question.compute_content_hash()
        question.status = "approved"
        question.content_hash = content_hash
        question.approved_by = request.user
        question.approved_at = timezone.now()
        question.save(update_fields=["status", "content_hash", "approved_by", "approved_at"])
        log_action(
            request.user, "question_approved", question,
            summary=f"Approved question #{question.id}: {question.question_text[:80]}",
            details={
                "e_signature": True,
                "content_hash": content_hash,
                "signed_by": request.user.get_username(),
                "signed_at": question.approved_at.isoformat(),
            },
        )
        return response.Response(self.get_serializer(question).data)

    @decorators.action(detail=True, methods=["patch"])
    def reject(self, request, pk=None):
        signature_error = self._verify_e_signature(request)
        if signature_error is not None:
            return signature_error
        question = self.get_object()
        question.status = "rejected"
        # Clear the approval binding: a rejected question is no longer vouched for, and
        # leaving a stale hash/approver would misrepresent it as still-signed content.
        question.content_hash = None
        question.approved_by = None
        question.approved_at = None
        question.save(update_fields=["status", "content_hash", "approved_by", "approved_at"])
        log_action(
            request.user, "question_rejected", question,
            summary=f"Rejected question #{question.id}: {question.question_text[:80]}",
            details={"e_signature": True, "signed_by": request.user.get_username()},
        )
        return response.Response(self.get_serializer(question).data)


class OptionViewSet(viewsets.ModelViewSet):
    queryset = Option.objects.select_related("question").all()
    serializer_class = OptionSerializer
    # Read access is reviewer-only: this serializer exposes `is_correct`, so leaving it on
    # the default IsAuthenticated made it a trivial side-channel around the learner-facing
    # question serializer. The SPA never calls this endpoint.
    permission_classes = [IsReviewerUser]

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [IsReviewerUser()]
