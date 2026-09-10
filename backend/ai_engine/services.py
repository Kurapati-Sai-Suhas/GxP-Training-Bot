import json
import logging
import math
import os
import random
import re
import time

from openai import OpenAI

from . import metrics

logger = logging.getLogger(__name__)

NVIDIA_NIM_BASE_URL = os.getenv("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")

# The model id is CONFIGURATION, not code.
#
# It was previously hardcoded to "meta/llama-3.1-8b-instruct". That model reached end of
# life on 2026-08-26 and the endpoint now returns HTTP 410 Gone. Because the fallback
# contract swallows every provider failure, the application kept serving traffic on the
# deterministic offline generator and nothing surfaced the outage -- correct availability
# behaviour, but a silent and total loss of the live AI path.
#
# Two things follow, and both are implemented here:
#   1. Rotating a retired model must be an environment change, not a code change and
#      redeploy. Read at CALL time, not import time, so a running worker picks up a new
#      value without a rebuild.
#   2. A retired model must be distinguishable from a transient blip -- see
#      classify_llm_error's "model_retired" category below.
#
# Verified invocable on 2026-09-10 via `manage.py check_ai_provider`.
DEFAULT_NVIDIA_NIM_MODEL = "openai/gpt-oss-20b"
NVIDIA_NIM_MODEL = DEFAULT_NVIDIA_NIM_MODEL  # retained for import compatibility

NVIDIA_NIM_MAX_ATTEMPTS = 3
NVIDIA_NIM_RETRY_BACKOFF_SECONDS = 0.5


def nim_model():
    """The chat/generation model id, resolved at call time from the environment."""
    return os.getenv("NVIDIA_NIM_MODEL", DEFAULT_NVIDIA_NIM_MODEL)

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

    # Checked FIRST, and deliberately kept separate from model_not_found.
    #
    # A retired model is permanent and operator-actionable: no amount of retrying will fix
    # it, and the only remedy is to point NVIDIA_NIM_MODEL / NVIDIA_EMBED_MODEL at a
    # replacement. Before this branch existed, NVIDIA's 410 Gone response fell through to
    # "unknown" -- indistinguishable from a transient network blip -- which is exactly how
    # both of this project's models stayed dead without anyone noticing.
    if "410" in message or "end of life" in message or "no longer available" in message:
        return "model_retired"
    # "404" is matched from the message as well as the type name. Every other category here
    # already keys off the status code in the message (401/403, 429, 500/502/503); 404 alone
    # relied on the SDK's exception type surviving intact, so a wrapped or re-raised 404
    # degraded to "unknown". NVIDIA returns 404 for a model that exists in the catalogue but
    # is not entitled for the account -- a real and distinctly actionable condition.
    if "notfound" in name or "model_not_found" in message or "404" in message:
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
                model=nim_model(),
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
                extra={"provider": "nvidia_nim", "model": nim_model(),
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
    started = time.time()
    try:
        drafts = generate_questions_with_nvidia_nim(role_name, sop_chunk, number_of_questions)
        metrics.record("question_generation", nim_model(), ok=True,
                       latency_ms=(time.time() - started) * 1000)
        return drafts, "nvidia_nim"
    except Exception as exc:  # noqa: BLE001 - the fallback contract: degrade, never fail
        category = classify_llm_error(exc)
        # Recorded on the fallback path too. A fallback rate is only meaningful against a
        # denominator of total attempts -- and since this path raises nothing to the caller,
        # this counter is the ONLY thing that can distinguish "AI is working" from "AI has
        # been dead for a fortnight and we are serving offline questions".
        metrics.record("question_generation", nim_model(), ok=False,
                       latency_ms=(time.time() - started) * 1000,
                       error_category=category, fallback_used=True)
        logger.error(
            "NVIDIA NIM unavailable after %s attempts (%s); falling back to the offline "
            "generator. Questions from this run are marked generation_source='mock'.",
            NVIDIA_NIM_MAX_ATTEMPTS, category,
            extra={"provider": "nvidia_nim", "error_category": category,
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


def _tokens(text):
    """Words WITH repeats. BM25 needs term frequency; _significant_words discards it."""
    return [word.lower() for word in _WORD_PATTERN.findall(text)]


# BM25 parameters. These are the standard defaults from the Okapi literature, not values
# fitted to this corpus -- fitting two free parameters on a 15-query gold set would overfit
# it, and the gold set is the measuring instrument.
#   k1 controls term-frequency saturation: repeating a term keeps helping, but with sharply
#      diminishing returns.
#   b  controls length normalisation: 0.75 partially corrects for long chunks accumulating
#      matches simply by being long.
BM25_K1 = 1.5
BM25_B = 0.75


def _bm25_scores(question, chunks):
    """Okapi BM25 over one SOP's chunks.

    WHY BM25 AND NOT PLAIN OVERLAP
    ------------------------------
    The previous ranker counted how many distinct query words appeared in a chunk. Every
    word counted equally, so a word appearing in nearly every chunk of the document -- "must",
    "procedure", "shall", the document's own title words -- contributed exactly as much
    evidence as a rare, discriminating term like "gowning" or "centrifuge".

    Measured consequence on the gold set (P1-007): query Q01 produced a THREE-WAY TIE at
    overlap score 3, because the tying chunks matched on common words. The correct chunk
    could not be separated from two irrelevant ones by a metric that cannot tell a rare term
    from a stopword.

    BM25 fixes exactly that failure mode with two terms the old ranker lacked:
      * IDF        -- a term appearing in most chunks carries little weight; a rare term
                      carries a lot. This is the term that breaks the Q01 tie.
      * length norm -- a long chunk no longer out-scores a short precise one just by
                      accumulating incidental matches.

    RESEARCH BASIS
    --------------
    Robertson & Zaragoza (2009), "The Probabilistic Relevance Framework: BM25 and Beyond",
    Foundations and Trends in IR -- the canonical derivation of the scoring function used here.

    Thakur et al. (2021), "BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of
    Information Retrieval Models", NeurIPS Datasets & Benchmarks -- finds BM25 a robust
    baseline that dense retrievers frequently FAIL to beat out-of-domain in zero-shot
    settings. That finding is why this project strengthens lexical retrieval before reaching
    for embeddings: a pharmaceutical SOP corpus is precisely the narrow, out-of-domain case
    where BEIR shows zero-shot dense retrieval is weakest. (It is also, separately, the only
    option currently available -- no NIM embedding model is invocable on this account.)

    The corpus for IDF is the chunk set passed in -- i.e. one SOP's own chunks. That is the
    correct reference population: "must" being ubiquitous *within this document* is exactly
    what should discount it when ranking *within this document*.
    """
    query_terms = _significant_words(question)
    if not chunks:
        return []

    chunk_tokens = [_tokens(chunk.chunk_text) for chunk in chunks]
    lengths = [len(tokens) for tokens in chunk_tokens]
    total = len(chunks)
    avg_length = (sum(lengths) / total) if total else 0.0
    if avg_length == 0:
        return [0.0] * total

    term_frequencies = []
    for tokens in chunk_tokens:
        counts = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        term_frequencies.append(counts)

    scores = []
    for index in range(total):
        counts = term_frequencies[index]
        length = lengths[index]
        score = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            # Document frequency: how many chunks contain this term at all.
            containing = sum(1 for tf in term_frequencies if term in tf)
            # Robertson/Sparck-Jones IDF, the +1 form -- always positive, so a term present
            # in every chunk contributes ~0 rather than a negative score that would perversely
            # penalise a chunk for containing a query term.
            idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
            denominator = frequency + BM25_K1 * (1 - BM25_B + BM25_B * length / avg_length)
            score += idf * (frequency * (BM25_K1 + 1)) / denominator
        scores.append(score)
    return scores


def _overlap_scores(question, chunks):
    """The pre-BM25 ranker: count of distinct query words present. Retained so the two can
    be compared on the gold set rather than swapped on assertion."""
    query_terms = _significant_words(question)
    return [float(len(query_terms & _significant_words(chunk.chunk_text))) for chunk in chunks]


RANKERS = {"bm25": _bm25_scores, "overlap": _overlap_scores}

# MEASURED DECISION — BM25 was implemented, evaluated, and REJECTED.
#
# The hypothesis was that IDF would break the three-way tie at overlap score 3 that P1-007
# identified as the cause of the Q01 failure. It did not. BM25 lost on every configuration
# tested, on both the live corpus and the post-P2-001 corrected chunking.
#
#   Corrected chunking (11 chunks, 14 scored queries, gold set v1.0, max_chunks=6)
#     ranker                Hit@1     R@3      MRR
#     overlap  (adopted)   0.8571     1.0   0.9286
#     bm25 b=0.75          0.8571     1.0   0.9048
#     bm25 b=0.50          0.8571     1.0   0.9048
#     bm25 b=0.25          0.8571     1.0   0.9048
#     bm25 b=0             0.8571     1.0   0.9167
#
# Two distinct reasons, both properties of THIS corpus rather than defects in BM25:
#
#   1. IDF needs a corpus to be a statistic over. Each SOP holds 4-5 chunks, so document
#      frequency takes about three distinct values and the resulting IDF weights span a
#      range too narrow to separate anything. Q01 ranked WORSE under every BM25 variant.
#
#   2. Length normalisation actively harms this corpus. On the live (pre-P2-001) corpus,
#      6-token title-only chunks scored 2.885 against the correct chunk's 1.472 purely
#      because they were short. Chunk length here reflects how much a section says, not
#      verbosity, which is the assumption b encodes.
#
# b was swept only to attribute the regression, never to select a value: fitting a free
# parameter on a 15-query gold set would overfit the measuring instrument itself.
#
# Kept, not deleted, because the negative result is the evidence for the current design and
# should be re-runnable when the corpus grows. Thakur et al. (BEIR, NeurIPS 2021) find BM25
# a robust baseline at realistic corpus scale -- this deployment is three orders of magnitude
# below that scale, which is precisely why the finding did not transfer.
#
# Re-run before revisiting: scratchpad/bm25_corrected.py
DEFAULT_RANKER = "overlap"


def select_relevant_chunks(question, chunks, max_chunks=6, ranker=None):
    """Rank a SOP's chunks against the question; ties (including the all-zero-score case)
    keep the chunks' original document order, so a question with no keyword overlap still
    gets the SOP's opening sections rather than nothing at all.

    `ranker` selects the scoring function (see RANKERS) and exists so the evaluation harness
    can measure one against the other on a fixed gold set. Production uses the default.
    """
    score_fn = RANKERS[ranker or DEFAULT_RANKER]
    scores = score_fn(question, chunks)
    scored = [(scores[index], -index, chunk) for index, chunk in enumerate(chunks)]
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
                model=nim_model(),
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
                extra={"provider": "nvidia_nim", "model": nim_model(),
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
    started = time.time()
    try:
        answer, sections_used = answer_sop_question_with_nvidia_nim(sop_title, question, chunks)
        metrics.record("sop_chat", nim_model(), ok=True,
                       latency_ms=(time.time() - started) * 1000)
        return answer, sections_used, "nvidia_nim"
    except Exception as exc:  # noqa: BLE001 - the fallback contract: degrade, never fail
        category = classify_llm_error(exc)
        metrics.record("sop_chat", nim_model(), ok=False,
                       latency_ms=(time.time() - started) * 1000,
                       error_category=category, fallback_used=True)
        logger.error(
            "NVIDIA NIM unavailable for SOP chat after %s attempts (%s); answering from the "
            "best-matching chunk instead.",
            NVIDIA_NIM_MAX_ATTEMPTS, category,
            extra={"provider": "nvidia_nim", "error_category": category,
                   "fallback_used": True},
        )
        answer, sections_used = answer_sop_question_offline(question, chunks)
        return answer, sections_used, "mock"
