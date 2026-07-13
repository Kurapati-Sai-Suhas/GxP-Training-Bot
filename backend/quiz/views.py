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

    @decorators.action(detail=True, methods=["patch"])
    def approve(self, request, pk=None):
        question = self.get_object()
        question.status = "approved"
        question.save(update_fields=["status"])
        log_action(
            request.user, "question_approved", question,
            summary=f"Approved question #{question.id}: {question.question_text[:80]}",
        )
        return response.Response(self.get_serializer(question).data)

    @decorators.action(detail=True, methods=["patch"])
    def reject(self, request, pk=None):
        question = self.get_object()
        question.status = "rejected"
        question.save(update_fields=["status"])
        log_action(
            request.user, "question_rejected", question,
            summary=f"Rejected question #{question.id}: {question.question_text[:80]}",
        )
        return response.Response(self.get_serializer(question).data)


class OptionViewSet(viewsets.ModelViewSet):
    queryset = Option.objects.select_related("question").all()
    serializer_class = OptionSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]
