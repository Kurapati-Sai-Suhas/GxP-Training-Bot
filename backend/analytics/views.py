from django.db.models import Avg, Case, Count, FloatField, When
from rest_framework.decorators import api_view
from rest_framework.response import Response

from attempts.models import AttemptAnswer, QuizAttempt
from quiz.models import Question
from sops.models import SOPDocument


@api_view(["GET"])
def dashboard_summary(request):
    attempts = QuizAttempt.objects.select_related("learner", "job_role", "sop").order_by("-started_at")
    total_attempts = attempts.count()
    completed_attempts = attempts.exclude(completed_at__isnull=True).count()
    completion_rate = round((completed_attempts / total_attempts) * 100, 1) if total_attempts else 0
    average_score = QuizAttempt.objects.aggregate(value=Avg("score"))["value"] or 0

    role_rows = []
    for row in attempts.values("job_role__name").annotate(total=Count("id"), average=Avg("score")).order_by("job_role__name"):
        role_rows.append(
            {
                "role": row["job_role__name"] or "Unassigned",
                "total": row["total"],
                "average_score": round(float(row["average"] or 0), 1),
            }
        )

    recent_activity = [
        {
            "text": f"{sop.sop_code} {sop.title} is {sop.get_status_display().lower()}",
            "meta": f"{sop.created_at:%d %b %Y} · SOP Library",
        }
        for sop in SOPDocument.objects.order_by("-created_at")[:5]
    ]

    learner_progress = []
    for attempt in attempts[:8]:
        learner_progress.append(
            {
                "learner": attempt.learner.get_full_name() or attempt.learner.username,
                "role": attempt.job_role.name,
                "sop": attempt.sop.sop_code,
                "score": float(attempt.score),
                "status": "Passed" if attempt.completed_at and attempt.score >= 80 else "Failed" if attempt.completed_at else "In Progress",
                "completed_date": attempt.completed_at.date().isoformat() if attempt.completed_at else "-",
            }
        )

    weak_topics = []
    weak_rows = (
        AttemptAnswer.objects.values("question_id", "question__question_text", "question__sop__sop_code")
        .annotate(
            attempts=Count("id"),
            correct_rate=Avg(Case(When(is_correct=True, then=1.0), default=0.0, output_field=FloatField())),
        )
        .filter(attempts__gte=1)
        .order_by("correct_rate")[:5]
    )
    for row in weak_rows:
        weak_topics.append(
            {
                "question_id": row["question_id"],
                "topic": row["question__question_text"][:90],
                "sop_code": row["question__sop__sop_code"],
                "attempts": row["attempts"],
                "correct_rate": round(row["correct_rate"] * 100, 1),
            }
        )

    return Response(
        {
            "sops": SOPDocument.objects.count(),
            "processed_sops": SOPDocument.objects.filter(status="processed").count(),
            "questions": Question.objects.count(),
            "approved_questions": Question.objects.filter(status="approved").count(),
            "attempts": total_attempts,
            "average_score": round(float(average_score), 1),
            "completion_rate": completion_rate,
            "attempts_by_role": role_rows,
            "recent_activity": recent_activity,
            "learner_progress": learner_progress,
            "weak_topics": weak_topics,
        }
    )
