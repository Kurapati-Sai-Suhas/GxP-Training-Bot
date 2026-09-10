import logging
import os
import re
from pathlib import Path

import fitz
from docx import Document
from openai import OpenAI

logger = logging.getLogger(__name__)

HEADING_PATTERN = re.compile(
    r"^(?:(?:section|chapter|part|appendix)\s+\d+\b|\d+(?:\.\d+)*[.)])\s*\S.*$",
    re.IGNORECASE,
)

NVIDIA_NIM_BASE_URL = os.getenv("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")

# Embedding model id is CONFIGURATION, not code -- same reasoning as ai_engine.services.
#
# "nvidia/nv-embedqa-e5-v5" was hardcoded here and reached end of life on 2026-08-25; the
# endpoint returns HTTP 410 Gone. The chunking cascade absorbed it exactly as designed and
# fell through to fixed-length splitting, so no document failed to process -- but tier 2 of
# a three-tier cascade had been silently dead, and every affected chunk was recorded with
# chunking_strategy='fixed_length' rather than 'semantic'.
#
# MEASURED STATUS 2026-09-10: no embedding model in the NIM catalogue is invocable on this
# account -- every candidate returns HTTP 404 at call time despite being listed. Semantic
# chunking is therefore UNAVAILABLE, not merely misconfigured. The default below names the
# documented successor so the cascade recovers automatically if entitlement is granted;
# until then the heading-aware tier (which handles every SOP in the current corpus) and the
# fixed-length tier carry the load. `manage.py check_ai_provider` reports this state.
DEFAULT_NVIDIA_EMBED_MODEL = "nvidia/llama-3.2-nv-embedqa-1b-v1"
NVIDIA_EMBED_MODEL = DEFAULT_NVIDIA_EMBED_MODEL  # retained for import compatibility


def embed_model():
    """The embedding model id, resolved at call time from the environment."""
    return os.getenv("NVIDIA_EMBED_MODEL", DEFAULT_NVIDIA_EMBED_MODEL)
# Cosine-similarity floor for a sentence to join the current semantic chunk (Max-Min
# chunking; see _chunk_by_semantic_similarity below).
SEMANTIC_CHUNK_SIMILARITY_THRESHOLD = 0.5


def extract_text_from_file(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path)
    if suffix == ".docx":
        return extract_docx_text(path)
    if suffix in {".txt", ".md"}:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"Unsupported SOP file type: {suffix}")


def extract_pdf_text(path):
    lines = []
    with fitz.open(path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text().strip()
            if text:
                lines.append(f"[Page {page_number}]\n{text}")
    return "\n\n".join(lines)


def extract_docx_text(path):
    doc = Document(path)
    return "\n".join(paragraph.text for paragraph in doc.paragraphs if paragraph.text.strip())


def _split_by_length(lines, max_chars):
    chunks = []
    current = []
    current_len = 0
    for line in lines:
        if current and current_len + len(line) > max_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


def _cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embed_sentences(sentences):
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY is not configured")
    client = OpenAI(api_key=api_key, base_url=NVIDIA_NIM_BASE_URL)
    result = client.embeddings.create(
        model=embed_model(),
        input=sentences,
        extra_body={"input_type": "passage", "truncate": "END"},
    )
    return [item.embedding for item in result.data]


def _chunk_by_semantic_similarity(lines, max_chars):
    """Max-Min semantic chunking (Kiss, Nagy & Szilagyi, "Max-Min semantic chunking of
    documents for RAG application", Discover Computing, 2025): grow a chunk of sentences
    while the newest sentence's similarity to every sentence already in the chunk stays
    above SEMANTIC_CHUNK_SIMILARITY_THRESHOLD; start a new chunk once that drops. Used as
    the fallback when a SOP has no detectable heading structure, in place of blind
    fixed-length splitting -- see Moreno-Cediel et al. ("Optimising retrieval performance
    in RAG systems", Knowledge-Based Systems, 2025) on how fixed-size splits create "weak
    semantic boundaries" that hurt downstream retrieval quality.

    Returns None (caller falls back to _split_by_length) if NVIDIA_API_KEY is unset or the
    embedding call fails, matching this app's existing offline-fallback pattern.
    """
    try:
        embeddings = _embed_sentences(lines)
    except Exception as exc:  # noqa: BLE001 - cascade falls through to fixed-length splitting
        from ai_engine.services import classify_llm_error

        logger.warning(
            "Semantic chunking unavailable (%s): %s. Falling back to fixed-length splitting; "
            "affected chunks are recorded with chunking_strategy='fixed_length'.",
            classify_llm_error(exc), exc,
            extra={"provider": "nvidia_nim", "model": embed_model(),
                   "error_category": classify_llm_error(exc), "fallback_used": True},
        )
        return None

    chunks = []
    current_lines = [lines[0]]
    current_embeddings = [embeddings[0]]
    current_len = len(lines[0])
    for line, emb in zip(lines[1:], embeddings[1:]):
        min_similarity = min(_cosine_similarity(emb, existing) for existing in current_embeddings)
        if min_similarity >= SEMANTIC_CHUNK_SIMILARITY_THRESHOLD and current_len + len(line) <= max_chars:
            current_lines.append(line)
            current_embeddings.append(emb)
            current_len += len(line)
        else:
            chunks.append("\n".join(current_lines))
            current_lines, current_embeddings, current_len = [line], [emb], len(line)
    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks


def chunk_text(text, max_chars=1200):
    """Split extracted SOP text into (title, body, strategy) chunks.

    Prefers section-heading boundaries ("Section 2: Gowning Sequence", "3.1 Cleaning
    Verification", ...) over blind character cuts, so a chunk maps to a coherent part
    of the document instead of an arbitrary slice, and the heading becomes the chunk's
    section_title instead of a generic "Auto chunk N". When no headings are detected at
    all, falls back to semantic (embeddings-based) chunking rather than a blind
    fixed-length split -- see _chunk_by_semantic_similarity for the research basis -- and
    only drops to fixed-length splitting if that also fails (no NVIDIA_API_KEY, or the
    embedding call errors). An overlong heading-delimited section is still split by length,
    since it already has a semantically coherent boundary either side of it.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    sections = []
    current_title = None
    current_lines = []
    for line in lines:
        if HEADING_PATTERN.match(line):
            if current_lines:
                sections.append((current_title, current_lines))
            current_title = line[:150]
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, current_lines))

    # Fold a heading-less preamble into the first real section rather than leaving it as a
    # standalone chunk.
    #
    # Real SOPs open with a document title ("Standard Operating Procedure: Cleanroom Entry and
    # Gowning"), which does not match HEADING_PATTERN and so became its own chunk titled
    # "Auto chunk 1". That chunk is pure title text, and a learner's question naturally repeats
    # the document's title words -- so it outranked genuine content on lexical overlap. Measured
    # on the retrieval gold set (P2-001): the query "What is the purpose of the warehouse receipt
    # procedure and what must be inspected?" ranked the title chunk first and the correct section
    # second. Merging lifted Hit@1 from 0.786 to 0.857 and MRR from 0.893 to 0.929, and removed
    # all three title-only chunks from the corpus.
    #
    # Merged rather than discarded: the title text is genuine document content and stays
    # searchable, just attributed to the section it introduces instead of standing alone.
    # Discarding it scored identically but destroys information, so it was rejected.
    #
    # Only applies when a real heading exists. A document with no headings at all still takes
    # the semantic/fixed-length path below, unchanged.
    if len(sections) > 1 and sections[0][0] is None:
        preamble_lines = sections[0][1]
        first_title, first_lines = sections[1]
        sections = [(first_title, preamble_lines + first_lines)] + sections[2:]

    if len(sections) == 1 and sections[0][0] is None:
        semantic_chunks = _chunk_by_semantic_similarity(lines, max_chars)
        if semantic_chunks is not None:
            return [(None, body, "semantic") for body in semantic_chunks]
        return [(None, body, "fixed_length") for body in _split_by_length(lines, max_chars)]

    chunks = []
    for title, section_lines in sections:
        body = "\n".join(section_lines)
        if not body:
            continue
        if len(body) <= max_chars:
            chunks.append((title, body, "heading"))
        else:
            for sub_body in _split_by_length(section_lines, max_chars):
                chunks.append((title, sub_body, "heading"))
    return chunks
