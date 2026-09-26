import asyncio
import re
from dataclasses import dataclass

import pymupdf


@dataclass
class PageText:
    page: int
    text: str


def _extract(data: bytes) -> list[PageText]:
    pages: list[PageText] = []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if document.is_encrypted:
            raise ValueError("Encrypted PDFs cannot be processed")
        for index, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            text = text.replace("\x00", "")
            text = re.sub(r"[\t\u00a0 ]+", " ", text)
            text = re.sub(r" *\n *", "\n", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            pages.append(PageText(page=index, text=text))
    return pages


async def extract_pdf(data: bytes) -> list[PageText]:
    try:
        return await asyncio.to_thread(_extract, data)
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise ValueError("The uploaded file is not a readable PDF") from exc
