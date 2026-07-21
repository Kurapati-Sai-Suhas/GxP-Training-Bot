import json
import os
import random
import re
import time

from openai import OpenAI

NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_NIM_MODEL = "meta/llama-3.1-8b-instruct"
NVIDIA_NIM_MAX_ATTEMPTS = 3
NVIDIA_NIM_RETRY_BACKOFF_SECONDS = 0.5

REQUIRED_KEYS = {"question_text", "options", "correct_option_index", "explanation"}

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
    except Exception:
        return generate_mock_questions(role_name, sop_chunk, number_of_questions), "mock"
