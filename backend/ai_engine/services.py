import json
import logging
import os
import random
import re
import time

from openai import OpenAI

logger = logging.getLogger(__name__)

NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_NIM_MODEL = "meta/llama-3.1-8b-instruct"
NVIDIA_NIM_MAX_ATTEMPTS = 3
NVIDIA_NIM_RETRY_BACKOFF_SECONDS = 0.5

REQUIRED_KEYS = {"question_text", "options", "correct_option_index", "explanation"}


def classify_llm_error(exc):
    """Bucket a provider exception into an operator-actionable category.

    The fallback contract deliberately swallows every failure so the pipeline degrades
    instead of breaking -- which previously made an expired API key, a quota exhaustion and
    a transient network blip completely indistinguishable in production. The categories
    below are what an operator actually needs to tell apart, derived from the exception
    type name and message because the OpenAI SDK's typed exceptions are not all importable
    across the version range this project pins.
    """
    name = type(exc).__name__.lower()
    message = str(exc).lower()

    if "notfound" in name or "model_not_found" in message:
        return "model_not_found"
    if "authentication" in name or "permissiondenied" in name or "401" in message or "403" in message:
        return "authentication_failure"
    if "ratelimit" in name or "429" in message or "rate limit" in message or "quota" in message:
        return "rate_limit"
    if "timeout" in name or "timed out" in message:
        return "timeout"
    if "connection" in name or "apiconnection" in name:
        return "connection_error"
    if isinstance(exc, json.JSONDecodeError) or "json" in message:
        return "invalid_model_output"
    if isinstance(exc, ValueError):
        return "validation_failure"
    if "internalserver" in name or "500" in message or "502" in message or "503" in message:
        return "provider_error"
    return "unknown"

DISTRACTOR_TEMPLATES = [
    "This step is optional and may be skipped without documentation.",
    "This requirement only applies during audits, not routine operations.",
    "This step should be performed after batch release rather than before.",
    "Verbal confirmation from a supervisor replaces the documented step.",
    "This applies only to new employees during their first month.",
]


def build_quiz_prompt(role_name, sop_chunk, number_of_questions=5):
    return f"""
You are a GxP training assistant for pharma and life sciences SOP training.
Create {number_of_questions} role-specific multiple-choice questions for the role: {role_name}.

Rules:
- Use only the SOP text below.
- Return valid JSON only: a JSON array of question objects (no wrapping object, no markdown fences).
- Each question object must include question_text, difficulty (easy/medium/hard), options (array of 4 strings), correct_option_index (0-based int), explanation, confidence.
- confidence is your own estimate, from 0.0 to 1.0, of how unambiguous and well-supported this
  question and its correct answer are given only the SOP text above. Use a lower value when the
  SOP text is vague, when more than one option could reasonably be argued as correct, or when you
  had to infer beyond what the text states outright.
- Explanation must explain why the correct answer is compliant and why the wrong answers are risky.

SOP text:
{sop_chunk}
"""


def _strip_markdown_fences(content):
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()


def _normalize_confidence(raw_value):
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, value))


def _normalize_drafts(parsed):
    if isinstance(parsed, dict):
        parsed = parsed.get("questions", [parsed])
    if not isinstance(parsed, list):
        raise ValueError("AI response was not a list of questions")

    drafts = []
    for item in parsed:
        if not REQUIRED_KEYS.issubset(item.keys()):
            continue
        if not isinstance(item["options"], list) or len(item["options"]) < 2:
            continue
        drafts.append(
            {
                "question_text": item["question_text"],
                "difficulty": item.get("difficulty", "medium"),
                "options": item["options"],
                "correct_option_index": int(item["correct_option_index"]),
                "explanation": item["explanation"],
                "confidence": _normalize_confidence(item.get("confidence")),
            }
        )
    if not drafts:
        raise ValueError("AI response contained no usable questions")
    return drafts


def generate_questions_with_nvidia_nim(role_name, sop_chunk, number_of_questions=5):
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY is not configured")

    client = OpenAI(api_key=api_key, base_url=NVIDIA_NIM_BASE_URL)
    last_error = None
    # A single bad response (network blip, rate limit, occasionally-malformed JSON from
    # the model) shouldn't drop straight to the offline fallback — retry a couple of times
    # with a short linear backoff first, then let the caller fall back as before.
    for attempt in range(1, NVIDIA_NIM_MAX_ATTEMPTS + 1):
        try:
            result = client.chat.completions.create(
                model=NVIDIA_NIM_MODEL,
                messages=[
                    {"role": "system", "content": "Return strict JSON for a GxP quiz generation task."},
                    {"role": "user", "content": build_quiz_prompt(role_name, sop_chunk, number_of_questions)},
                ],
                temperature=0.2,
            )
            content = _strip_markdown_fences(result.choices[0].message.content)
            drafts = _normalize_drafts(json.loads(content))
            return drafts[:number_of_questions]
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see fallback contract below
            last_error = exc
            logger.warning(
                "NVIDIA NIM quiz generation attempt %s/%s failed (%s): %s",
                attempt, NVIDIA_NIM_MAX_ATTEMPTS, classify_llm_error(exc), exc,
                extra={"provider": "nvidia_nim", "model": NVIDIA_NIM_MODEL,
                       "error_category": classify_llm_error(exc), "attempt": attempt},
            )
            if attempt < NVIDIA_NIM_MAX_ATTEMPTS:
                time.sleep(NVIDIA_NIM_RETRY_BACKOFF_SECONDS * attempt)
    raise last_error


HEADING_PATTERN = re.compile(r"^(section|chapter|part|appendix)\s+\d", re.IGNORECASE)


def _split_sentences(text):
    candidates = re.split(r"(?<=[.!?])\s+|\n+", text)
    sentences = []
    for candidate in candidates:
        cleaned = candidate.strip()
        if len(cleaned) > 40 and not HEADING_PATTERN.match(cleaned):
            sentences.append(cleaned)
    return sentences


def generate_mock_questions(role_name, sop_chunk, number_of_questions=5):
    """Deterministic, offline question generator.

    Used when no NVIDIA_API_KEY is configured or the live API call fails, so a
    demo never depends on NVIDIA NIM being reachable.
    """
    sentences = _split_sentences(sop_chunk) or [sop_chunk.strip()[:200] or "This SOP section defines a required step."]
    difficulties = ["easy", "medium", "hard"]

    drafts = []
    for i in range(number_of_questions):
        correct = sentences[i % len(sentences)]
        distractors = random.sample(DISTRACTOR_TEMPLATES, k=3)
        options = [correct] + distractors
        random.shuffle(options)
        correct_index = options.index(correct)
        drafts.append(
            {
                "question_text": (
                    f"Per this SOP section, which statement correctly reflects the requirement "
                    f"relevant to the {role_name} role?"
                ),
                "difficulty": difficulties[i % len(difficulties)],
                "options": options,
                "correct_option_index": correct_index,
                "explanation": (
                    f"The correct statement is drawn directly from the SOP text: \"{correct[:200]}\". "
                    "The other options are incorrect because they weaken, skip, or invert this documented "
                    "requirement, which would create a compliance risk if followed in practice."
                ),
                # Unlike the live LLM's self-reported estimate, the mock generator's correct
                # option is copied verbatim from the SOP text by construction, so 1.0 reflects
                # genuine certainty rather than a guess.
                "confidence": 1.0,
            }
        )
    return drafts


def generate_questions(role_name, sop_chunk, number_of_questions=1):
    """Try the live NVIDIA NIM generator; fall back to the offline mock generator on any failure.

    Returns (drafts, source) where source is "nvidia_nim" or "mock", so callers can
    surface which path produced the content.
    """
    try:
        drafts = generate_questions_with_nvidia_nim(role_name, sop_chunk, number_of_questions)
        return drafts, "nvidia_nim"
    except Exception as exc:  # noqa: BLE001 - the fallback contract: degrade, never fail
        logger.error(
            "NVIDIA NIM unavailable after %s attempts (%s); falling back to the offline "
            "generator. Questions from this run are marked generation_source='mock'.",
            NVIDIA_NIM_MAX_ATTEMPTS, classify_llm_error(exc),
            extra={"provider": "nvidia_nim", "error_category": classify_llm_error(exc),
                   "fallback_used": True},
        )
        return generate_mock_questions(role_name, sop_chunk, number_of_questions), "mock"


# --- RAG-based SOP chatbot -------------------------------------------------
#
# "Retrieval" here is deliberately simple: a word-overlap score over the SOP's
# own chunks, not an embeddings/vector search. Chunking-strategy research already
# cited in this project (heading-aware chunking beating embeddings on structured
# technical documents) motivates the same call here — a typical SOP has a handful
# of short chunks, so a lightweight lexical filter selects the relevant ones with
# no new infrastructure (no embeddings model, no vector DB), while still capping
# how much text is stuffed into the prompt as the document grows. The live path
# is grounded exclusively in the selected chunk text (see the prompt below); the
# offline path answers by quoting the best-matching chunk directly, so both paths
# satisfy "grounded in the SOP's own chunks" even with no NVIDIA_API_KEY configured.

_WORD_PATTERN = re.compile(r"[a-zA-Z]{4,}")
MAX_CHAT_QUESTION_LENGTH = 500


def _significant_words(text):
    return {word.lower() for word in _WORD_PATTERN.findall(text)}


def select_relevant_chunks(question, chunks, max_chunks=6):
    """Rank a SOP's chunks by lexical overlap with the question; ties (including
    the all-zero-overlap case) keep the chunks' original document order, so a
    question with no keyword overlap still gets the SOP's opening sections
    rather than nothing at all."""
    question_words = _significant_words(question)
    scored = []
    for index, chunk in enumerate(chunks):
        overlap = len(question_words & _significant_words(chunk.chunk_text))
        scored.append((overlap, -index, chunk))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [chunk for _score, _index, chunk in scored[:max_chunks]]


def build_sop_chat_prompt(sop_title, question, chunks):
    sections = "\n\n".join(
        f"[{chunk.section_title or 'Untitled section'}]\n{chunk.chunk_text}" for chunk in chunks
    )
    return f"""
You are a GxP training assistant helping an employee understand one specific SOP.

Rules:
- Answer using ONLY the SOP text below. Do not use any outside knowledge.
- If the answer is not covered by this text, say so plainly: "This SOP does not cover that —
  please check with your supervisor or QA." Do not guess.
- Keep the answer to 2-4 sentences, and name the section it comes from.

SOP: {sop_title}

{sections}

Question: {question}
"""


def answer_sop_question_with_nvidia_nim(sop_title, question, chunks):
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY is not configured")

    relevant_chunks = select_relevant_chunks(question, chunks, max_chunks=6)
    client = OpenAI(api_key=api_key, base_url=NVIDIA_NIM_BASE_URL)
    last_error = None
    for attempt in range(1, NVIDIA_NIM_MAX_ATTEMPTS + 1):
        try:
            result = client.chat.completions.create(
                model=NVIDIA_NIM_MODEL,
                messages=[
                    {"role": "system", "content": "Answer strictly from the provided SOP text."},
                    {"role": "user", "content": build_sop_chat_prompt(sop_title, question, relevant_chunks)},
                ],
                temperature=0.2,
            )
            answer = result.choices[0].message.content.strip()
            sections_used = [c.section_title or "Untitled section" for c in relevant_chunks]
            return answer, sections_used
        except Exception as exc:  # noqa: BLE001 - same fallback contract as generate_questions_with_nvidia_nim
            last_error = exc
            logger.warning(
                "NVIDIA NIM SOP chat attempt %s/%s failed (%s): %s",
                attempt, NVIDIA_NIM_MAX_ATTEMPTS, classify_llm_error(exc), exc,
                extra={"provider": "nvidia_nim", "model": NVIDIA_NIM_MODEL,
                       "error_category": classify_llm_error(exc), "attempt": attempt},
            )
            if attempt < NVIDIA_NIM_MAX_ATTEMPTS:
                time.sleep(NVIDIA_NIM_RETRY_BACKOFF_SECONDS * attempt)
    raise last_error


def answer_sop_question_offline(question, chunks):
    """Deterministic fallback: quote the single best-matching chunk instead of
    generating prose, so the app never depends on NVIDIA NIM being reachable."""
    top_chunks = select_relevant_chunks(question, chunks, max_chunks=1)
    if not top_chunks:
        return (
            "This SOP has no processed content to answer from yet.",
            [],
        )
    chunk = top_chunks[0]
    section = chunk.section_title or "this SOP"
    excerpt = chunk.chunk_text.strip()[:400]
    answer = f"Based on \"{section}\": {excerpt}"
    return answer, [chunk.section_title or "Untitled section"]


def answer_sop_question(sop_title, question, chunks):
    """Try the live NVIDIA NIM chatbot; fall back to a deterministic chunk quote
    on any failure. Returns (answer, sections_used, source)."""
    try:
        answer, sections_used = answer_sop_question_with_nvidia_nim(sop_title, question, chunks)
        return answer, sections_used, "nvidia_nim"
    except Exception as exc:  # noqa: BLE001 - the fallback contract: degrade, never fail
        logger.error(
            "NVIDIA NIM unavailable for SOP chat after %s attempts (%s); answering from the "
            "best-matching chunk instead.",
            NVIDIA_NIM_MAX_ATTEMPTS, classify_llm_error(exc),
            extra={"provider": "nvidia_nim", "error_category": classify_llm_error(exc),
                   "fallback_used": True},
        )
        answer, sections_used = answer_sop_question_offline(question, chunks)
        return answer, sections_used, "mock"
