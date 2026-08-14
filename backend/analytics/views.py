from collections import defaultdict

from django.db.models import Avg, Case, Count, FloatField, When
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import is_reviewer
from attempts.models import AttemptAnswer, QuizAttempt, TopicMastery
from quiz.models import Question
from sops.models import SOPDocument


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    """Organisation-wide training metrics.

    Named personal data (learner_progress) is scoped to the requesting user unless they
    hold a reviewer/admin role. Previously this endpoint declared no permission class at
    all, so it inherited the global IsAuthenticated default and returned every learner's
    name, job role, SOP, score, and pass/fail status to any authenticated caller --
    including their peers. Aggregates that carry no identity (counts, role averages, weak
    topics) stay visible to everyone, since they are what makes the dashboard useful to a
    learner at all.
    """
    can_see_all_learners = is_reviewer(request.user)
    attempts = QuizAttempt.objects.select_related("learner", "job_role", "sop").order_by("-started_at")
    visible_attempts = attempts if can_see_all_learners else attempts.filter(learner=request.user)
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
    for attempt in visible_attempts[:8]:
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

    # Distinct (SOP, job role) pairs with at least one approved question -- how many
    # role-specific quizzes are actually live for learners to take, not just drafted.
    published_quiz_count = (
        Question.objects.filter(status="approved").values("sop_id", "job_role_id").distinct().count()
    )
    # Proves the Leitner adaptive-retraining scheduler (attempts.models.TopicMastery) is
    # live, not decorative: how many learner/SOP pairs are due for a spaced retest right now.
    retraining_due_count = TopicMastery.objects.filter(next_eligible_at__lte=timezone.now()).count()

    # Retraining-improvement evidence: for every (learner, SOP) pair with 2+ completed
    # attempts, compare the score on their first attempt to their most recent one. This is
    # the one number that shows retraining actually helps, not just that it runs.
    by_pair = defaultdict(list)
    for a in QuizAttempt.objects.filter(completed_at__isnull=False).order_by("completed_at"):
        by_pair[(a.learner_id, a.sop_id)].append(float(a.score))
    first_scores, latest_scores = [], []
    for scores in by_pair.values():
        if len(scores) >= 2:
            first_scores.append(scores[0])
            latest_scores.append(scores[-1])
    retraining_improvement = None
    if first_scores:
        avg_first = sum(first_scores) / len(first_scores)
        avg_latest = sum(latest_scores) / len(latest_scores)
        retraining_improvement = {
            "pairs_with_retraining": len(first_scores),
            "avg_first_score": round(avg_first, 1),
            "avg_latest_score": round(avg_latest, 1),
            "avg_improvement": round(avg_latest - avg_first, 1),
        }

    return Response(
        {
            "sops": SOPDocument.objects.count(),
            "processed_sops": SOPDocument.objects.filter(status="processed").count(),
            "questions": Question.objects.count(),
            "approved_questions": Question.objects.filter(status="approved").count(),
            "published_quiz_count": published_quiz_count,
            "attempts": total_attempts,
            "average_score": round(float(average_score), 1),
            "completion_rate": completion_rate,
            "retraining_due_count": retraining_due_count,
            "retraining_improvement": retraining_improvement,
            "attempts_by_role": role_rows,
            "recent_activity": recent_activity,
            "learner_progress": learner_progress,
            "weak_topics": weak_topics,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recommended_refresher(request):
    """Adaptive-retraining suggestion (extends FR-6.5's weak-topics aggregate to the
    individual learner): find the SOP the requesting learner has personally answered
    incorrectly most often across their own past attempts, and suggest it as a refresher
    quiz. This is a recommendation surfaced on the Learner Quiz screen, not an
    auto-assigned quiz — the learner still chooses to start it."""
    rows = (
        AttemptAnswer.objects.filter(attempt__learner=request.user, is_correct=False)
        .values("question__sop_id", "question__sop__sop_code", "question__sop__title", "attempt__job_role_id")
        .annotate(wrong_count=Count("id"))
        .order_by("-wrong_count")
    )
    top = rows.first()
    if not top:
        return Response({"recommendation": None})

    return Response(
        {
            "recommendation": {
                "sop_id": top["question__sop_id"],
                "sop_code": top["question__sop__sop_code"],
                "sop_title": top["question__sop__title"],
                "job_role_id": top["attempt__job_role_id"],
                "wrong_count": top["wrong_count"],
                "reason": (
                    f"You've missed {top['wrong_count']} question"
                    f"{'s' if top['wrong_count'] != 1 else ''} on this SOP in past attempts."
                ),
            }
        }
    )
