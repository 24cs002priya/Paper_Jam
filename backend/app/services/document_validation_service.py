from __future__ import annotations

import re
from datetime import datetime, timezone

from app.services.pdf_service import extract_pdf


def extract_document_fields(text: str) -> dict:
    """Conservative text extraction; absent or ambiguous fields stay absent."""
    patterns = {
        "business_name": [r"(?:business|firm|company|entity)\s*name\s*[:\-]\s*([^\n]{2,160})"],
        "owner_name": [r"(?:owner|proprietor|director|authorised\s+signatory)\s*(?:name)?\s*[:\-]\s*([^\n]{2,160})"],
        "business_address": [r"(?:registered|office|business)\s+address\s*[:\-]\s*([^\n]{5,240})"],
        "registration_number": [r"(?:registration|certificate|licen[cs]e)\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9/\-]{4,40})"],
        "pan": [r"\b([A-Z]{5}[0-9]{4}[A-Z])\b"],
        "gstin": [r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b"],
        "email": [r"\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b"],
        "phone": [r"(?:phone|mobile|contact)\s*[:\-]\s*([+0-9()\- ]{7,24})"],
        "production_capacity_kg_per_day": [r"(?:production|installed)\s+capacity\s*[:\-]?\s*([0-9,.]+\s*(?:kg|kilograms?)\s*(?:/|per\s*)day)"],
        "annual_turnover": [r"(?:annual\s+)?turnover\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([0-9,.]+\s*(?:lakh|lac|crore|million|billion)?)"],
        "document_number": [r"(?:document|reference|certificate)\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9/\-]{4,40})"],
        "authority": [r"(?:issued\s+by|authority)\s*[:\-]\s*([^\n]{2,160})"],
        "issue_date": [r"(?:date\s+of\s+issue|issued\s+on|issue\s+date)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})"],
        "expiry_date": [r"(?:valid\s+(?:until|upto)|expires?\s+(?:on|date)?|expiry\s+date)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})"],
    }
    extracted = {}
    for field, candidates in patterns.items():
        found = []
        for pattern in candidates:
            found.extend(re.findall(pattern, text, flags=re.IGNORECASE))
        normalized = list(dict.fromkeys(" ".join(item.split()).strip(" .,:;") for item in found if item.strip()))
        if len(normalized) == 1:
            extracted[field] = normalized[0]
        elif len(normalized) > 1:
            extracted[field] = None
    return extracted


def validate_document_type(expected_type: str, filename: str, text: str) -> tuple[str, str | None]:
    def normalize(value: str) -> str:
        value = re.sub(r"\bi[\s.]*d\.?\b", " id ", value.casefold())
        return re.sub(r"[^a-z0-9]+", " ", value).strip()
    expected = normalize(expected_type)
    corpus = normalize(f"{filename} {text}")
    if expected and expected in corpus:
        return "passed", None
    # Regulatory document descriptions often include the title followed by
    # signing, eligibility, and layout instructions. Validate against the
    # title phrase instead of requiring every instruction word in the PDF.
    instruction_boundaries = {"duly", "completed", "signed", "showing", "issued", "desired", "manufactured", "along"}
    connectors = {"and", "of", "the", "for", "to", "by", "with", "in", "on", "a", "an"}
    generic = {"document", "copy", "proof", "details", "other", "no", "number", "required", "full", "duplicate"}
    aliases = {"directors": "director", "equipments": "equipment", "licence": "license"}
    title_terms = []
    for token in expected.split():
        if token in instruction_boundaries:
            break
        if token in connectors or token in generic or token.isdigit():
            continue
        title_terms.append(aliases.get(token, token))
        if len(title_terms) == 4:
            break
    corpus_terms = {aliases.get(token, token) for token in corpus.split()}
    matched_terms = set(title_terms).intersection(corpus_terms)
    minimum_matches = 1 if len(title_terms) == 1 else 2
    if title_terms and len(matched_terms) >= minimum_matches and len(matched_terms) / len(title_terms) >= 0.5:
        return "passed", None
    return "needs_review", "The PDF is readable, but its text does not clearly confirm the selected document type."


async def inspect_pdf(content: bytes) -> tuple[list, dict, dict]:
    pages = await extract_pdf(content)
    text = "\n".join(page.text for page in pages).strip()
    if not pages or not text:
        raise ValueError("PDF contains no extractable text. Upload a readable, text-based PDF.")
    extracted = extract_document_fields(text)
    if extracted.get("expiry_date"):
        raw = extracted["expiry_date"]
        parsed = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"):
            try:
                parsed = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        extracted["expiry_date"] = parsed if parsed else None
    return pages, extracted, {"page_count": len(pages), "character_count": len(text), "text_extractable": True}
