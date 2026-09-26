import json
import logging
import re
from typing import Any

from groq import APIStatusError, AsyncGroq
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.approval_engine import RuleExtraction
from app.prompts.approval_rule_extraction import APPROVAL_RULE_EXTRACTION_PROMPT
from app.services.approval_engine.groq_schema import approval_extraction_schema

logger = logging.getLogger("paper_jam.approval_engine.extraction")
_AUTHORIZATION_TERM = re.compile(
    r"\b(licen[cs](?:e|ing)|registration|permit|approval|consent|clearance|no[- ]objection|noc|authorization|authorisation|certificate)\b",
    re.IGNORECASE,
)
_NON_APPROVAL_HEADING = re.compile(
    r"^(documents?|document checklist|list of documents|information|records?|fees?|technical person|qualified person|staff|personnel)\b",
    re.IGNORECASE,
)
_LEGAL_REQUIREMENT = re.compile(
    r"\b(shall|must|required|prohibited|may not|prior approval|no person|will be (?:registered|licen[cs](?:e|ing)))\b",
    re.IGNORECASE,
)


class RuleExtractionUnavailable(RuntimeError):
    def __init__(self, message: str, *, code: str = "rule_extraction_failed") -> None:
        super().__init__(message)
        self.code = code


def _provider_error_fields(exc: APIStatusError) -> tuple[str, str]:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body) if isinstance(body, dict) else {}
    code = str(error.get("code") or type(exc).__name__) if isinstance(error, dict) else type(exc).__name__
    detail = error.get("message") if isinstance(error, dict) else None
    detail = str(detail or exc.message or type(exc).__name__)
    detail = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", detail)
    return code[:100], detail[:500]


def _normalize_quote(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _repair_omitted_grounding_fields(payload: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill only recoverable fallback omissions from exact retrieved text.

    JSON mode does not enforce the Pydantic contract. This narrowly repairs
    missing evidence/citations by selecting a verbatim authorization sentence;
    unsupported rules are left incomplete and rejected by Pydantic/grounding.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        return payload
    for rule in payload["rules"]:
        if not isinstance(rule, dict):
            continue
        if "confidence" not in rule:
            # This is a conservative placeholder for omitted model metadata,
            # not a calibrated probability. The source-grounding gate remains
            # the actual acceptance criterion.
            rule["confidence"] = 0.5
        evidence = rule.get("evidence")
        evidence_chunk_ids = []
        if isinstance(evidence, str) and evidence.strip():
            evidence_chunk_ids = [str(chunk["_id"]) for chunk in chunks
                                  if quote_is_in_chunk(evidence, chunk.get("text", ""))]
        if not evidence_chunk_ids:
            title = str(rule.get("approval_name") or "")
            title_terms = {term for term in re.findall(r"[a-z0-9]+", title.casefold())
                           if len(term) > 3 and term not in {"license", "licence", "approval", "registration"}}
            candidates = []
            for chunk in chunks:
                text = str(chunk.get("text", ""))
                for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
                    sentence = " ".join(sentence.split()).strip()
                    if (len(sentence) >= 20 and len(sentence) <= 1500
                            and _AUTHORIZATION_TERM.search(sentence)
                            and _LEGAL_REQUIREMENT.search(sentence)):
                        overlap = sum(term in sentence.casefold() for term in title_terms)
                        candidates.append((overlap, sentence, str(chunk["_id"])))
            if candidates:
                overlap, sentence, chunk_id = max(candidates, key=lambda item: (item[0], len(item[1])))
                # Do not attach a generic authorization clause to an unrelated
                # generated name when there is no meaningful textual overlap.
                if overlap or not title_terms:
                    rule["evidence"] = sentence
                    rule["source_chunk_ids"] = [chunk_id]
                    evidence_chunk_ids = [chunk_id]
        if evidence_chunk_ids and not rule.get("source_chunk_ids"):
            rule["source_chunk_ids"] = evidence_chunk_ids[:3]
    return payload


def quote_is_in_chunk(quote: str, chunk_text: str) -> bool:
    normalized_quote = _normalize_quote(quote)
    return bool(normalized_quote) and normalized_quote in _normalize_quote(chunk_text)


def _is_explicit_approval_rule(rule) -> bool:
    """Exclude general duties and document checklist headings from approval rows."""
    title = rule.approval_name.strip()
    evidence = rule.evidence
    return bool(
        _AUTHORIZATION_TERM.search(title)
        and not _NON_APPROVAL_HEADING.search(title)
        and _AUTHORIZATION_TERM.search(evidence)
        and _LEGAL_REQUIREMENT.search(evidence)
    )


def _select_extraction_chunks(chunks: list[dict[str, Any]], *, limit: int = 12, per_regulation: int = 2) -> list[dict[str, Any]]:
    ranked = sorted(chunks, key=lambda chunk: chunk.get("score", 0), reverse=True)
    selected = []
    counts: dict[str, int] = {}
    for chunk in ranked:
        source = str(chunk.get("regulation_id") or chunk.get("regulation_name") or "unknown")
        if counts.get(source, 0) >= per_regulation:
            continue
        selected.append(chunk)
        counts[source] = counts.get(source, 0) + 1
        if len(selected) == limit:
            return selected
    selected_ids = {str(chunk.get("_id")) for chunk in selected}
    selected.extend(chunk for chunk in ranked if str(chunk.get("_id")) not in selected_ids)
    return selected[:limit]


def _conditions_are_grounded(conditions, cited_chunks: list[dict]) -> bool:
    for condition in conditions:
        if condition.kind == "condition":
            if not condition.evidence or not any(quote_is_in_chunk(condition.evidence, chunk["text"]) for chunk in cited_chunks):
                return False
        elif not _conditions_are_grounded(condition.children, cited_chunks):
            return False
    return True


def validate_extraction(extraction: RuleExtraction, chunks: list[dict[str, Any]]) -> RuleExtraction:
    by_id = {str(chunk["_id"]): chunk for chunk in chunks}
    grounded = []
    for rule in extraction.rules:
        cited = [chunk_id for chunk_id in rule.source_chunk_ids if chunk_id in by_id]
        cited_chunks = [by_id[chunk_id] for chunk_id in cited]
        if (not cited or not _is_explicit_approval_rule(rule)
                or not any(quote_is_in_chunk(rule.evidence, chunk["text"]) for chunk in cited_chunks)
                or not any(_normalize_quote(rule.approval_name) in _normalize_quote(chunk["text"]) for chunk in cited_chunks)
                or not _conditions_are_grounded(rule.conditions, cited_chunks)):
            continue
        rule.source_chunk_ids = list(dict.fromkeys(cited))
        rule.required_documents = [doc for doc in rule.required_documents
            if doc.source_chunk_id in cited and quote_is_in_chunk(doc.evidence, by_id[doc.source_chunk_id]["text"])
            and _normalize_quote(doc.document_name) in _normalize_quote(by_id[doc.source_chunk_id]["text"])]
        rule.dependencies = [dep for dep in rule.dependencies
            if dep.source_chunk_id in cited and quote_is_in_chunk(dep.evidence, by_id[dep.source_chunk_id]["text"])
            and _normalize_quote(dep.approval_name) in _normalize_quote(by_id[dep.source_chunk_id]["text"])]
        grounded.append(rule)
    extraction.rules = grounded
    return extraction


async def extract_approval_rules(context: dict[str, Any], chunks: list[dict[str, Any]]) -> RuleExtraction:
    settings = get_settings()
    api_key = settings.groq_api_key.get_secret_value()
    if not api_key:
        raise RuleExtractionUnavailable("Regulatory rule extraction is not configured", code="groq_key_missing")
    extraction_chunks = _select_extraction_chunks(chunks)
    compact_chunks = [{
        "chunk_id": str(chunk["_id"]), "regulation_name": chunk.get("regulation_name"),
        "authority": chunk.get("authority"), "section": chunk.get("section"),
        "jurisdiction": chunk.get("jurisdiction"), "source_url": chunk.get("source_url"),
        "page_number": chunk.get("page_start"), "text": chunk.get("text", "")[:1100],
    } for chunk in extraction_chunks]
    # Do not let the SDK replay failed requests invisibly. In particular, a
    # burst of manual refreshes can otherwise amplify Groq 429 rate limits.
    client = AsyncGroq(api_key=api_key, max_retries=0)
    messages = [
        {"role": "system", "content": APPROVAL_RULE_EXTRACTION_PROMPT},
        {"role": "user", "content": json.dumps({"business_profile": context, "regulatory_chunks": compact_chunks}, ensure_ascii=False, default=str)},
    ]
    strict_response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "approval_rule_extraction",
            "strict": True,
            "schema": approval_extraction_schema(),
        },
    }
    model_candidates = [settings.groq_model]
    if settings.groq_fallback_model and settings.groq_fallback_model not in model_candidates:
        model_candidates.append(settings.groq_fallback_model)
    completion = None
    completion_model = None
    try:
        for index, model in enumerate(model_candidates):
            try:
                # Groq's 120B model is more reliable for this extraction in
                # JSON mode under the available on-demand tier; Pydantic and
                # exact-source validation remain mandatory server-side.
                response_format = {"type": "json_object"} if "gpt-oss-120b" in model else strict_response_format
                completion = await client.chat.completions.create(
                    model=model, messages=messages, temperature=0, max_completion_tokens=1800,
                    response_format=response_format,
                )
                completion_model = model
                if index:
                    logger.info("Approval rule extraction succeeded with fallback model %s", model)
                break
            except APIStatusError as exc:
                provider_code, detail = _provider_error_fields(exc)
                request_id = getattr(exc, "request_id", None) or getattr(exc, "_request_id", None)
                logger.warning("Groq extraction request failed: model=%s status=%s code=%s request_id=%s detail=%s",
                               model, exc.status_code, provider_code, request_id, detail)
                # GPT-OSS 20B sometimes cannot finish this large constrained
                # extraction. Retry only that model-generation failure on the
                # configured 120B model; never spend another request on 429s,
                # invalid schemas, authentication failures, or other 4xxs.
                can_fallback = (index == 0 and len(model_candidates) > 1
                                and exc.status_code == 400 and provider_code == "json_validate_failed")
                if can_fallback:
                    continue
                code = "groq_rate_limited" if exc.status_code == 429 else f"groq_http_{exc.status_code}"
                raise RuleExtractionUnavailable("Groq could not complete regulatory rule extraction", code=code) from exc
        if completion is None:
            raise RuleExtractionUnavailable("No configured Groq model completed regulatory rule extraction", code="groq_models_failed")
    except Exception as exc:
        if isinstance(exc, RuleExtractionUnavailable):
            raise
        logger.exception("Unexpected Groq extraction request failure")
        raise RuleExtractionUnavailable("Groq could not complete regulatory rule extraction", code="groq_request_failed") from exc
    finally:
        await client.close()
    content = completion.choices[0].message.content or "{}"
    try:
        parsed = RuleExtraction.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            issues = [{"path": ".".join(str(part) for part in item["loc"]), "type": item["type"]}
                      for item in exc.errors(include_input=False)[:20]]
            logger.error("Groq returned output rejected by the local rule model: %s", issues)
        else:
            logger.error("Groq returned invalid JSON for approval extraction")
        # Strict 20B output can still fail Pydantic's business-level schema
        # constraints; use the verified 120B JSON mode once, then apply the
        # same local schema and evidence checks.
        if completion_model != settings.groq_fallback_model and settings.groq_fallback_model:
            logger.info("Retrying invalid model output once with configured fallback model %s", settings.groq_fallback_model)
            fallback_client = AsyncGroq(api_key=api_key, max_retries=0)
            try:
                fallback = await fallback_client.chat.completions.create(
                    model=settings.groq_fallback_model, messages=messages, temperature=0,
                    max_completion_tokens=1800, response_format={"type": "json_object"},
                )
                fallback_content = fallback.choices[0].message.content or "{}"
                try:
                    parsed = RuleExtraction.model_validate_json(fallback_content)
                except (ValidationError, ValueError):
                    repaired_payload = _repair_omitted_grounding_fields(
                        json.loads(fallback_content), extraction_chunks,
                    )
                    parsed = RuleExtraction.model_validate(repaired_payload)
            except Exception as fallback_exc:
                logger.warning("Fallback extraction was rejected locally: %s", type(fallback_exc).__name__)
                raise RuleExtractionUnavailable("Groq returned an invalid approval extraction", code="model_output_invalid") from fallback_exc
            finally:
                await fallback_client.close()
        else:
            raise RuleExtractionUnavailable("Groq returned an invalid approval extraction", code="model_output_invalid") from exc
    return validate_extraction(parsed, extraction_chunks)
