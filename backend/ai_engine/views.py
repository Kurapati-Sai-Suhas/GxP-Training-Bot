from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from accounts.models import JobRole
from accounts.permissions import IsAdminUser
from sops.models import SOPDocument

from .tasks import generate_quiz_task


@api_view(["POST"])
@permission_classes([IsAdminUser])
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
