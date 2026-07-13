from rest_framework import decorators, permissions, response, status, viewsets

from accounts.permissions import IsAdminUser
from audit.models import log_action

from .models import SOPChunk, SOPDocument
from .serializers import SOPChunkSerializer, SOPDocumentSerializer
from .tasks import process_sop_document_task


class SOPDocumentViewSet(viewsets.ModelViewSet):
    queryset = SOPDocument.objects.prefetch_related("chunks").all()
    serializer_class = SOPDocumentSerializer

    def get_permissions(self):
        if self.action in ("create", "process", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        sop = serializer.save(uploaded_by=user)
        log_action(
            user, "sop_uploaded", sop,
            summary=f"Uploaded {sop.sop_code} v{sop.version} ({sop.title})",
        )

    @decorators.action(detail=True, methods=["post"])
    def process(self, request, pk=None):
        sop = self.get_object()
        user_id = request.user.id if request.user.is_authenticated else None
        # .delay() + .get() offloads the actual PDF/DOCX parsing and chunking onto a
        # Celery worker (when CELERY_TASK_ALWAYS_EAGER=False) so it doesn't block this
        # web worker's thread, while keeping the HTTP response shape unchanged.
        payload = process_sop_document_task.delay(sop.id, user_id).get(timeout=60)
        if "error" in payload:
            return response.Response(payload, status=status.HTTP_400_BAD_REQUEST)
        return response.Response(payload)


class SOPChunkViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SOPChunk.objects.select_related("sop").all()
    serializer_class = SOPChunkSerializer
