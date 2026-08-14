from django.http import FileResponse, Http404
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

    def perform_update(self, serializer):
        before = {
            "title": serializer.instance.title,
            "sop_code": serializer.instance.sop_code,
            "version": serializer.instance.version,
            "department": serializer.instance.department,
        }
        sop = serializer.save()
        changed = sorted(k for k, v in before.items() if getattr(sop, k) != v)
        log_action(
            self.request.user, "sop_updated", sop,
            summary=f"Updated metadata on {sop.sop_code} v{sop.version}",
            details={"fields_changed": changed, "previous": {k: before[k] for k in changed}},
        )

    def destroy(self, request, *args, **kwargs):
        """Deleting an SOP cascades to its chunks, questions, options, quiz attempts,
        answers, and mastery rows. That is a large, irreversible loss of training history,
        and it previously left no trace at all -- the single largest hole in the
        append-only-trail claim. The counts are captured *before* the delete because
        afterwards there is nothing left to count.
        """
        sop = self.get_object()
        from attempts.models import QuizAttempt
        from quiz.models import Question

        impact = {
            "sop_code": sop.sop_code,
            "version": sop.version,
            "title": sop.title,
            "chunks_deleted": sop.chunks.count(),
            "questions_deleted": Question.objects.filter(sop=sop).count(),
            "approved_questions_deleted": Question.objects.filter(sop=sop, status="approved").count(),
            "attempts_deleted": QuizAttempt.objects.filter(sop=sop).count(),
        }
        log_action(
            request.user, "sop_deleted", sop,
            summary=(
                f"Deleted {sop.sop_code} v{sop.version} ({sop.title}) -- cascaded "
                f"{impact['questions_deleted']} question(s) and {impact['attempts_deleted']} attempt(s)"
            ),
            details=impact,
        )
        return super().destroy(request, *args, **kwargs)

    @decorators.action(detail=True, methods=["post"])
    def process(self, request, pk=None):
        sop = self.get_object()
        # Reprocessing deletes and rebuilds every SOPChunk, which cascades away each
        # learner's ChunkMastery for this SOP and nulls Question.source_chunk -- silently
        # destroying section-level training history and the grounding provenance of
        # already-approved questions. Blocked once approved content exists; the full fix is
        # a versioned SOP model (see docs/IMPLEMENTATION_PLAN.md, deferred).
        from quiz.models import Question

        approved_count = Question.objects.filter(sop=sop, status="approved").count()
        if approved_count:
            return response.Response(
                {
                    "error": (
                        f"{sop.sop_code} has {approved_count} approved question(s) generated from its "
                        "current sections. Reprocessing would delete those sections, orphaning the "
                        "approved questions and erasing every learner's section-level mastery for this "
                        "SOP. Upload the revised procedure as a new version instead."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        user_id = request.user.id if request.user.is_authenticated else None
        # .delay() + .get() offloads the actual PDF/DOCX parsing and chunking onto a
        # Celery worker (when CELERY_TASK_ALWAYS_EAGER=False) so it doesn't block this
        # web worker's thread, while keeping the HTTP response shape unchanged.
        payload = process_sop_document_task.delay(sop.id, user_id).get(timeout=60)
        if "error" in payload:
            return response.Response(payload, status=status.HTTP_400_BAD_REQUEST)
        return response.Response(payload)

    @decorators.action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """Authenticated access to the uploaded source file.

        Django's static() media serving is unauthenticated and only active under DEBUG,
        which meant every uploaded SOP was world-readable in the Docker stack. Serving
        through this action puts the file behind the same authentication as the rest of the
        API. Lookup is by primary key against the model, so no user-supplied path ever
        reaches the filesystem -- traversal is not reachable.
        """
        sop = self.get_object()
        if not sop.file:
            raise Http404("This SOP has no stored file.")
        try:
            handle = sop.file.open("rb")
        except FileNotFoundError as exc:
            raise Http404("The stored file for this SOP is missing.") from exc
        return FileResponse(handle, as_attachment=True, filename=sop.file.name.rsplit("/", 1)[-1])


class SOPChunkViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SOPChunk.objects.select_related("sop").all()
    serializer_class = SOPChunkSerializer
