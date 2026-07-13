import re
from pathlib import Path

import fitz
from docx import Document

HEADING_PATTERN = re.compile(
    r"^(?:(?:section|chapter|part|appendix)\s+\d+\b|\d+(?:\.\d+)*[.)])\s*\S.*$",
    re.IGNORECASE,
)


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


def chunk_text(text, max_chars=1200):
    """Split extracted SOP text into (title, body) chunks.

    Prefers section-heading boundaries ("Section 2: Gowning Sequence", "3.1 Cleaning
    Verification", ...) over blind character cuts, so a chunk maps to a coherent part
    of the document instead of an arbitrary slice, and the heading becomes the chunk's
    section_title instead of a generic "Auto chunk N". Falls back to plain length-based
    splitting when no headings are detected, or splits an overlong section further.
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
        return [(None, body) for body in _split_by_length(lines, max_chars)]

    chunks = []
    for title, section_lines in sections:
        body = "\n".join(section_lines)
        if not body:
            continue
        if len(body) <= max_chars:
            chunks.append((title, body))
        else:
            for sub_body in _split_by_length(section_lines, max_chars):
                chunks.append((title, sub_body))
    return chunks
