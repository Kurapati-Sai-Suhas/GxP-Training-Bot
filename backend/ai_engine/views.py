from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from accounts.models import JobRole
from accounts.permissions import IsAdminUser
from accounts.throttling import AIGenerationRateThrottle, SopChatRateThrottle
from sops.models import SOPDocument

from .services import MAX_CHAT_QUESTION_LENGTH
from .tasks import answer_sop_question_task, generate_quiz_task


@api_view(["POST"])
@permission_classes([IsAdminUser])
@throttle_classes([AIGenerationRateThrottle])
def generate_quiz(request):
    sop_id = request.data.get("sop")
    job_role_id = request.data.get("job_role")

    if not sop_id or not job_role_id:
        return Response({"error": "sop and job_role are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        count = max(1, min(int(request.data.get("count", 5)), 20))
    except (TypeError, ValueError):
        return Response({"error": "count must be a whole number"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        sop = SOPDocument.objects.get(pk=sop_id)
    except SOPDocument.DoesNotExist:
        return Response({"error": "SOP not found"}, status=status.HTTP_404_NOT_FOUND)

    try:
        JobRole.objects.get(pk=job_role_id)
    except JobRole.DoesNotExist:
        return Response({"error": "Job role not found"}, status=status.HTTP_404_NOT_FOUND)

    if not sop.chunks.exists():
        return Response(
            {"error": "This SOP has no processed chunks yet. Run /process/ on it before generating a quiz."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # .delay() + .get() offloads the LLM call(s) onto a Celery worker (when
    # CELERY_TASK_ALWAYS_EAGER=False) so a slow NVIDIA NIM response never ties up this
    # web worker's thread, while keeping the HTTP response shape unchanged.
    user_id = request.user.id if request.user.is_authenticated else None
    payload = generate_quiz_task.delay(sop.id, int(job_role_id), count, user_id).get(timeout=120)
    return Response(payload, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([SopChatRateThrottle])
def sop_chat(request):
    """RAG-based SOP chatbot (additive companion to the quiz flow, not a replacement
    for it): a learner asks a free-text question about one SOP and gets an answer
    grounded in that SOP's own chunks. Open to any authenticated role, matching the
    existing read access to SOPChunk/SOPDocument — this reads content, it doesn't
    change anything role-scoped like Question/QuizAttempt do."""
    sop_id = request.data.get("sop")
    question = (request.data.get("question") or "").strip()

    if not sop_id or not question:
        return Response({"error": "sop and question are required"}, status=status.HTTP_400_BAD_REQUEST)
    if len(question) > MAX_CHAT_QUESTION_LENGTH:
        return Response(
            {"error": f"Question is too long (max {MAX_CHAT_QUESTION_LENGTH} characters)."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        sop = SOPDocument.objects.get(pk=sop_id)
    except SOPDocument.DoesNotExist:
        return Response({"error": "SOP not found"}, status=status.HTTP_404_NOT_FOUND)

    if not sop.chunks.exists():
        return Response(
            {"error": "This SOP has no processed chunks yet. Run /process/ on it before asking questions."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    payload = answer_sop_question_task.delay(sop.id, question, request.user.id).get(timeout=60)
    return Response(payload, status=status.HTTP_200_OK)
