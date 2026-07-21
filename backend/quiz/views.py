from rest_framework import decorators, permissions, response, viewsets

from accounts.permissions import IsAdminUser, IsReviewerUser
from audit.models import log_action

from .models import Option, Question
from .serializers import OptionSerializer, QuestionSerializer


class QuestionViewSet(viewsets.ModelViewSet):
    queryset = Question.objects.select_related("sop", "job_role", "source_chunk").prefetch_related("options")
    serializer_class = QuestionSerializer

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
        return queryset

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [IsReviewerUser()]
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]

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

    @decorators.action(detail=True, methods=["patch"])
    def approve(self, request, pk=None):
        signature_error = self._verify_e_signature(request)
        if signature_error is not None:
            return signature_error
        question = self.get_object()
        question.status = "approved"
        question.save(update_fields=["status"])
        log_action(
            request.user, "question_approved", question,
            summary=f"Approved question #{question.id}: {question.question_text[:80]}",
            details={"e_signature": True},
        )
        return response.Response(self.get_serializer(question).data)

    @decorators.action(detail=True, methods=["patch"])
    def reject(self, request, pk=None):
        signature_error = self._verify_e_signature(request)
        if signature_error is not None:
            return signature_error
        question = self.get_object()
        question.status = "rejected"
        question.save(update_fields=["status"])
        log_action(
            request.user, "question_rejected", question,
            summary=f"Rejected question #{question.id}: {question.question_text[:80]}",
            details={"e_signature": True},
        )
        return response.Response(self.get_serializer(question).data)


class OptionViewSet(viewsets.ModelViewSet):
    queryset = Option.objects.select_related("question").all()
    serializer_class = OptionSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]
