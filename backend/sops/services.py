import os
import re
from pathlib import Path

import fitz
from docx import Document
from openai import OpenAI

HEADING_PATTERN = re.compile(
    r"^(?:(?:section|chapter|part|appendix)\s+\d+\b|\d+(?:\.\d+)*[.)])\s*\S.*$",
    re.IGNORECASE,
)

NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_EMBED_MODEL = "nvidia/nv-embedqa-e5-v5"
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
        model=NVIDIA_EMBED_MODEL,
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
    except Exception:
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
