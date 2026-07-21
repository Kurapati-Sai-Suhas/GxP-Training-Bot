from celery import shared_task

from audit.models import log_action
from quiz.models import Option, Question
from quiz.serializers import QuestionSerializer
from sops.models import SOPDocument

from .services import answer_sop_question, generate_questions


def _normalize(text):
    return " ".join(text.split()).strip().lower()


@shared_task
def generate_quiz_task(sop_id, job_role_id, count, user_id=None):
    """Call the LLM (or offline fallback) per SOP chunk and persist draft questions.
    Runs on a Celery worker when CELERY_TASK_ALWAYS_EAGER=False, so a slow NVIDIA NIM
    call never ties up a web request thread."""
    from django.contrib.auth import get_user_model

    from accounts.models import JobRole

    sop = SOPDocument.objects.get(id=sop_id)
    job_role = JobRole.objects.get(id=job_role_id)
    user = get_user_model().objects.filter(id=user_id).first() if user_id else None
    chunks = list(sop.chunks.all())

    created_questions = []
    sources_used = set()
    skipped_duplicates = 0

    existing_signatures = set()
    for existing in Question.objects.filter(sop=sop, job_role=job_role).prefetch_related("options"):
        correct_text = next((o.option_text for o in existing.options.all() if o.is_correct), "")
        existing_signatures.add((_normalize(existing.question_text), _normalize(correct_text)))

    base, remainder = divmod(count, len(chunks))
    for chunk_index, chunk in enumerate(chunks):
        questions_for_chunk = base + (1 if chunk_index < remainder else 0)
        if questions_for_chunk == 0:
            continue
        drafts, source = generate_questions(job_role.name, chunk.chunk_text, number_of_questions=questions_for_chunk)
        sources_used.add(source)
        for draft in drafts:
            correct_text = draft["options"][draft["correct_option_index"]]
            signature = (_normalize(draft["question_text"]), _normalize(correct_text))
            if signature in existing_signatures:
                skipped_duplicates += 1
                continue
            existing_signatures.add(signature)

            question = Question.objects.create(
                sop=sop,
                job_role=job_role,
                source_chunk=chunk,
                question_text=draft["question_text"],
                difficulty=draft.get("difficulty", "medium"),
                explanation=draft["explanation"],
                status="draft",
                confidence_score=draft.get("confidence"),
            )
            options = list(draft["options"])
            correct_index = draft["correct_option_index"]
            Option.objects.bulk_create(
                [
                    Option(question=question, option_text=text, is_correct=(i == correct_index))
                    for i, text in enumerate(options)
                ]
            )
            created_questions.append(question)

    if sources_used == {"nvidia_nim"}:
        overall_source = "nvidia_nim"
    elif sources_used == {"mock"}:
        overall_source = "mock"
    else:
        overall_source = "mixed"

    log_action(
        user, "questions_generated", sop,
        summary=(
            f"Generated {len(created_questions)} draft question(s) for {sop.sop_code} / {job_role.name} "
            f"via {overall_source}" + (f", skipped {skipped_duplicates} duplicate(s)" if skipped_duplicates else "")
        ),
        details={
            "job_role": job_role.name, "count_requested": count, "count_created": len(created_questions),
            "skipped_duplicates": skipped_duplicates, "source": overall_source,
        },
    )

    questions = Question.objects.filter(id__in=[q.id for q in created_questions]).select_related(
        "sop", "job_role", "source_chunk"
    ).prefetch_related("options")
    serializer = QuestionSerializer(questions, many=True)
    return {"questions": serializer.data, "source": overall_source, "skipped_duplicates": skipped_duplicates}


@shared_task
def answer_sop_question_task(sop_id, question, user_id=None):
    """RAG-based SOP chatbot: answer a free-text question grounded in one SOP's own
    chunks. Runs on a Celery worker for the same reason generate_quiz_task does — a
    slow NVIDIA NIM call shouldn't occupy a web request thread."""
    from django.contrib.auth import get_user_model

    sop = SOPDocument.objects.get(id=sop_id)
    user = get_user_model().objects.filter(id=user_id).first() if user_id else None
    chunks = list(sop.chunks.all())

    answer, sections_used, source = answer_sop_question(sop.title, question, chunks)

    log_action(
        user, "sop_chat_query", sop,
        summary=f"{user or 'A user'} asked a question about {sop.sop_code}: \"{question[:80]}\"",
        details={"question": question, "source": source, "sections_used": sections_used},
    )

    return {"answer": answer, "sections_used": sections_used, "source": source, "sop_id": sop.id, "sop_code": sop.sop_code}
