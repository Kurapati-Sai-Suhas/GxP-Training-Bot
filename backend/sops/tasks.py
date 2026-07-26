from celery import shared_task

from audit.models import log_action

from .models import SOPChunk, SOPDocument
from .services import chunk_text, extract_text_from_file


@shared_task
def process_sop_document_task(sop_id, user_id=None):
    """Extract text and (re)build chunks for an SOP. Runs on a Celery worker when
    CELERY_TASK_ALWAYS_EAGER=False, so PDF/DOCX parsing never blocks a web request."""
    from django.contrib.auth import get_user_model

    sop = SOPDocument.objects.get(id=sop_id)
    user = get_user_model().objects.filter(id=user_id).first() if user_id else None

    try:
        extracted = extract_text_from_file(sop.file.path)
        SOPChunk.objects.filter(sop=sop).delete()
        for index, (title, chunk, strategy) in enumerate(chunk_text(extracted), start=1):
            SOPChunk.objects.create(
                sop=sop,
                section_title=title or f"Auto chunk {index}",
                chunk_text=chunk,
                chunking_strategy=strategy,
            )
        sop.status = "processed"
        sop.save(update_fields=["status"])
        chunk_count = SOPChunk.objects.filter(sop=sop).count()
        log_action(
            user, "sop_processed", sop,
            summary=f"Processed {sop.sop_code} v{sop.version} into {chunk_count} chunk(s)",
            details={"chunks": chunk_count},
        )
        return {"message": "SOP processed", "chunks": chunk_count}
    except Exception as exc:
        sop.status = "failed"
        sop.save(update_fields=["status"])
        log_action(
            user, "sop_process_failed", sop,
            summary=f"Failed to process {sop.sop_code} v{sop.version}: {exc}",
        )
        return {"error": str(exc)}
