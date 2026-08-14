import re

from celery import shared_task

from audit.models import log_action
from quiz.models import Option, Question
from quiz.serializers import QuestionSerializer
from sops.models import SOPDocument

from .services import answer_sop_question, generate_questions


def _normalize(text):
    return " ".join(text.split()).strip().lower()


# --- Near-duplicate detection ---------------------------------------------------------
# Exact-signature matching only catches a verbatim repeat. In practice the model rewords:
# "What must be done before batch release?" and "Prior to batch release, what is required?"
# are different signatures and the same question, and both would reach the review queue.
#
# Similarity is token-set Jaccard over significant words, deliberately not embeddings: an
# embedding call per candidate would add latency and cost to every generation run, and
# would make de-duplication depend on the provider being reachable -- which the whole
# pipeline is designed not to require. Lexical overlap is weaker, but it is free,
# deterministic, offline, and catches the rewording the model actually does.
_DUPLICATE_WORD_PATTERN = re.compile(r"[a-zA-Z]{3,}")
# Words too common in this domain to carry meaning; without these, any two questions about
# the same SOP look similar simply because both say "must", "SOP", "procedure".
_DUPLICATE_STOPWORDS = frozenset({
    "the", "and", "for", "are", "was", "when", "what", "which", "who", "how", "why", "does",
    "did", "must", "should", "shall", "can", "may", "with", "from", "that", "this", "these",
    "those", "have", "has", "had", "not", "any", "all", "per", "into", "onto", "before",
    "after", "during", "sop", "procedure", "following", "requirement", "requirements",
})
# The two thresholds are deliberately asymmetric, because the two fields carry different
# information:
#
#   The CORRECT ANSWER identifies *which fact* is being tested. Two questions with the same
#   correct answer are testing the same thing however differently they are phrased, so this
#   is the strong signal and takes the high bar.
#
#   The STEM confirms it is the same *subject matter*. Rewording mangles the stem badly --
#   "What must be done before batch release?" and "Prior to batch release, what is
#   required?" share only two significant words (0.40 overlap) despite being the same
#   question. A high stem bar would therefore miss exactly the case exact matching already
#   misses, so it takes the low bar and acts as a guard rather than a test.
#
# Requiring both is what suppresses false positives: two questions drawn from one short
# chunk often share stem vocabulary while testing different facts, and those differ in
# their correct answers.
DUPLICATE_ANSWER_THRESHOLD = 0.8
DUPLICATE_STEM_THRESHOLD = 0.4


def _significant_tokens(text):
    return {
        word.lower()
        for word in _DUPLICATE_WORD_PATTERN.findall(text or "")
        if word.lower() not in _DUPLICATE_STOPWORDS
    }


def token_similarity(first, second):
    """Jaccard overlap of significant tokens: |A ∩ B| / |A ∪ B|, in [0, 1]."""
    a = _significant_tokens(first)
    b = _significant_tokens(second)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_near_duplicate(question_text, correct_text, existing_pairs):
    """Whether this draft restates an existing question: same fact (answer), same subject
    (stem). See the threshold constants above for why the two bars differ."""
    for existing_question, existing_correct in existing_pairs:
        if (
            token_similarity(correct_text, existing_correct) >= DUPLICATE_ANSWER_THRESHOLD
            and token_similarity(question_text, existing_question) >= DUPLICATE_STEM_THRESHOLD
        ):
            return True
    return False


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
    existing_pairs = []  # (question_text, correct_text) for near-duplicate comparison
    for existing in Question.objects.filter(sop=sop, job_role=job_role).prefetch_related("options"):
        correct_text = next((o.option_text for o in existing.options.all() if o.is_correct), "")
        existing_signatures.add((_normalize(existing.question_text), _normalize(correct_text)))
        existing_pairs.append((existing.question_text, correct_text))

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
            # Exact signature first (cheap), then lexical near-duplicate (catches rewording).
            if signature in existing_signatures or is_near_duplicate(
                draft["question_text"], correct_text, existing_pairs
            ):
                skipped_duplicates += 1
                continue
            existing_signatures.add(signature)
            existing_pairs.append((draft["question_text"], correct_text))

            question = Question.objects.create(
                sop=sop,
                job_role=job_role,
                source_chunk=chunk,
                question_text=draft["question_text"],
                difficulty=draft.get("difficulty", "medium"),
                explanation=draft["explanation"],
                status="draft",
                confidence_score=draft.get("confidence"),
                generation_source=source,
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
