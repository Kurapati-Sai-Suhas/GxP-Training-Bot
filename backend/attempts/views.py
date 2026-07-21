from django.utils import timezone
from rest_framework import decorators, permissions, response, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from accounts.permissions import ADMIN_GROUP
from audit.models import log_action
from quiz.models import Question

from .models import AttemptAnswer, QuizAttempt, TopicMastery
from .serializers import AttemptAnswerSerializer, QuizAttemptSerializer


def _is_admin(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.groups.filter(name=ADMIN_GROUP).exists()))


class QuizAttemptViewSet(viewsets.ModelViewSet):
    queryset = QuizAttempt.objects.select_related("learner", "job_role", "sop").prefetch_related("answers")
    serializer_class = QuizAttemptSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        if _is_admin(self.request.user):
            return queryset
        return queryset.filter(learner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(learner=self.request.user)

    @decorators.action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        attempt = self.get_object()
        if attempt.learner_id != request.user.id:
            return response.Response(
                {"error": "This quiz attempt does not belong to you."},
                status=403,
            )
        submitted_answers = request.data.get("answers", [])

        AttemptAnswer.objects.filter(attempt=attempt).delete()
        correct_count = 0
        for item in submitted_answers:
            question_id = item.get("question")
            selected_option_id = item.get("selected_option")
            is_correct = False
            if selected_option_id:
                from quiz.models import Option

                is_correct = Option.objects.filter(id=selected_option_id, question_id=question_id, is_correct=True).exists()
            correct_count += 1 if is_correct else 0
            AttemptAnswer.objects.create(
                attempt=attempt,
                question_id=question_id,
                selected_option_id=selected_option_id,
                is_correct=is_correct,
            )

        total = len(submitted_answers) or 1
        attempt.score = round((correct_count / total) * 100, 2)
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["score", "completed_at"])
        log_action(
            request.user, "quiz_attempt_submitted", attempt,
            summary=f"{request.user} submitted attempt #{attempt.id} on {attempt.sop.sop_code}, score {attempt.score}%",
            details={"score": float(attempt.score), "answers": len(submitted_answers)},
        )

        # get_object() prefetched `answers` before the delete/create above, so that cache is
        # stale now. Re-fetch so the serialized response reflects the answers just created.
        attempt = self.get_queryset().get(pk=attempt.pk)
        return response.Response(self.get_serializer(attempt).data)


class AttemptAnswerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AttemptAnswer.objects.select_related("attempt", "question", "selected_option").all()
    serializer_class = AttemptAnswerSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        if _is_admin(self.request.user):
            return queryset
        return queryset.filter(attempt__learner=self.request.user)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def auto_assigned_retraining(request):
    """Adaptive-retraining auto-assignment (soft): for the requesting learner, find every
    SOP whose TopicMastery schedule (Leitner-style box scheduling, see models.py) says a
    retest is now due and not yet mastered, and describe it as an assignment with a
    difficulty-matched suggestion. This only reads state kept up to date by the
    AttemptAnswer signal (signals.py) — it does not create a QuizAttempt itself; the
    learner still starts one via the existing POST /api/attempts/quiz-attempts/ endpoint,
    exactly as they do for any other quiz today."""
    due = (
        TopicMastery.objects.filter(learner=request.user, next_eligible_at__lte=timezone.now())
        .exclude(mastery_status="mastered")
        .select_related("sop", "job_role")
    )

    assignments = []
    for mastery in due:
        if mastery.streak_correct == 0:
            suggested_difficulty = "easy"
        elif mastery.streak_correct < TopicMastery.MASTERY_STREAK_THRESHOLD - 1:
            suggested_difficulty = "medium"
        else:
            suggested_difficulty = "hard"

        available_count = Question.objects.filter(
            sop=mastery.sop, job_role=mastery.job_role, status="approved"
        ).count()
        if available_count == 0:
            continue

        assignments.append(
            {
                "sop_id": mastery.sop_id,
                "sop_code": mastery.sop.sop_code,
                "sop_title": mastery.sop.title,
                "job_role_id": mastery.job_role_id,
                "box_index": mastery.box_index,
                "streak_correct": mastery.streak_correct,
                "due_since": mastery.next_eligible_at.isoformat(),
                "suggested_difficulty": suggested_difficulty,
                "question_count_available": available_count,
                "reason": (
                    f"Due for a spaced retest (box {mastery.box_index}) under our adaptive-retraining "
                    f"schedule — {mastery.streak_correct} correct in a row so far on this SOP."
                ),
            }
        )

    return Response({"assignments": assignments})
