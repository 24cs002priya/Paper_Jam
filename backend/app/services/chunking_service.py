import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.services.pdf_service import PageText

HEADING = re.compile(r"^\s*((?:\d+(?:\.\d+)*[.)]?\s+)?[A-Z][^\n]{2,140})\s*$")


@dataclass
class TextChunk:
    text: str
    page_start: int
    page_end: int
    section: str | None
    subsection: str | None


def _paragraphs(page: PageText) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", page.text) if part.strip()]


def chunk_pages(pages: list[PageText]) -> list[TextChunk]:
    settings = get_settings()
    size, overlap = settings.chunk_size, settings.chunk_overlap
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("CHUNK_SIZE must be positive and CHUNK_OVERLAP smaller than CHUNK_SIZE")
    chunks: list[TextChunk] = []
    buffer = ""
    start = end = 0
    section: str | None = None
    subsection: str | None = None

    def emit(text: str, first: int, last: int, current_section: str | None, current_subsection: str | None) -> None:
        value = text.strip()
        if value:
            chunks.append(TextChunk(value, first, last, current_section, current_subsection))

    for page in pages:
        if not page.text:
            continue
        for paragraph in _paragraphs(page):
            if HEADING.match(paragraph) and len(paragraph) < 140:
                section = paragraph
                subsection = None
            if not buffer:
                start = page.page
            candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
            if len(candidate) > size and buffer:
                emit(buffer, start, end, section, subsection)
                tail = buffer[-overlap:] if overlap else ""
                buffer = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
                start = max(1, page.page - (1 if end < page.page else 0))
            else:
                buffer = candidate
            end = page.page
            while len(buffer) > size:
                # Long paragraphs are broken at sentence boundaries where possible.
                boundary = max(buffer.rfind(". ", 0, size), buffer.rfind("; ", 0, size), buffer.rfind(" ", 0, size))
                boundary = boundary if boundary > size // 2 else size
                emit(buffer[:boundary], start, end, section, subsection)
                tail = buffer[max(0, boundary - overlap):boundary].strip()
                buffer = (tail + " " + buffer[boundary:].lstrip()).strip()
                start = page.page
    emit(buffer, start, end, section, subsection)
    return chunks
