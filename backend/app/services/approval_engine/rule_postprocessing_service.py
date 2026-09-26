import re

from app.schemas.approval_engine import ExtractedDocument


_AUTHORIZATION = re.compile(r"\b(licen[cs]e|licen[cs]ing|registration)\b", re.IGNORECASE)
_CAPACITY = re.compile(r"installed capacity[^.\n]{0,140}", re.IGNORECASE)
_TURNOVER = re.compile(r"\bturnover\b[\s\S]{0,140}", re.IGNORECASE)
_LIST_ITEM = re.compile(r"(?m)^\s*(\d{1,2})[.)]\s+")


def _fssai_license_documents(chunks: list[dict]) -> list[ExtractedDocument]:
    documents = []
    for chunk in chunks:
        text = chunk.get("text", "")
        if "documents to be enclosed for new application for license" not in text.casefold():
            continue
        matches = list(_LIST_ITEM.finditer(text))
        for index, match in enumerate(matches):
            if index + 1 < len(matches):
                end = matches[index + 1].start()
            elif text.rstrip().endswith((".", ")")):
                end = len(text.rstrip())
            else:
                continue  # Do not publish a checklist item cut off at a chunk boundary.
            evidence = " ".join(text[match.start():end].split())
            name = re.sub(r"^\d{1,2}[.)]\s*", "", evidence).strip()
            name = name.rstrip(".")
            if len(name) < 8 or len(name) > 300 or len(evidence) > 1000:
                continue
            documents.append(ExtractedDocument(
                document_name=name,
                requirement_type="conditional",
                source_chunk_id=str(chunk["_id"]),
                evidence=evidence,
            ))
    return documents


def _fssai_authorization(rule, chunks_by_id: dict[str, dict]) -> bool:
    if not _AUTHORIZATION.search(rule.approval_name):
        return False
    return any(
        "food safety and standards" in str(chunks_by_id.get(chunk_id, {}).get("regulation_name", "")).casefold()
        or "fssai" in str(chunks_by_id.get(chunk_id, {}).get("regulation_name", "")).casefold()
        for chunk_id in rule.source_chunk_ids
    )


def coalesce_fssai_authorizations(rules, chunks: list[dict], context: dict):
    """Collapse repeated FSSAI licence/registration clauses into one roadmap item."""
    chunks_by_id = {str(chunk["_id"]): chunk for chunk in chunks}
    grouped = []
    fssai_rules = []
    for rule in rules:
        (fssai_rules if _fssai_authorization(rule, chunks_by_id) else grouped).append(rule)
    if not fssai_rules:
        return rules

    chosen = max(fssai_rules, key=lambda item: ("registration" in item.approval_name.casefold(), item.confidence))
    chosen.approval_name = "FSSAI food business licence or registration"
    chosen.rule_type = "mandatory" if any(item.rule_type == "mandatory" for item in fssai_rules) else "conditional"
    chosen.confidence = min(item.confidence for item in fssai_rules)
    chosen.source_chunk_ids = list(dict.fromkeys(
        chunk_id for item in fssai_rules for chunk_id in item.source_chunk_ids
    ))
    chosen.conditions = [condition for item in fssai_rules for condition in item.conditions]
    chosen.required_documents = list({
        (doc.document_name.casefold(), doc.source_chunk_id): doc
        for item in fssai_rules for doc in item.required_documents
    }.values())
    chosen.dependencies = list({
        (dep.approval_name.casefold(), dep.source_chunk_id): dep
        for item in fssai_rules for dep in item.dependencies
    }.values())
    existing_document_keys = {doc.document_name.casefold() for doc in chosen.required_documents}
    for document in _fssai_license_documents(chunks):
        if document.document_name.casefold() not in existing_document_keys:
            chosen.required_documents.append(document)
            chosen.source_chunk_ids.append(document.source_chunk_id)
            existing_document_keys.add(document.document_name.casefold())
    activity_text = " ".join(str(value) for value in context.get("business_activities", [])).casefold()
    for chunk in chunks:
        regulation_name = str(chunk.get("regulation_name", "")).casefold()
        if "food safety and standards" not in regulation_name and "fssai" not in regulation_name:
            continue
        text = chunk.get("text", "")
        if _CAPACITY.search(text) or ("packag" in activity_text and _TURNOVER.search(text) and "registration" in text.casefold()):
            chosen.source_chunk_ids.append(str(chunk["_id"]))
    chosen.source_chunk_ids = list(dict.fromkeys(chosen.source_chunk_ids))
    if not chosen.authority:
        chosen.authority = next((
            chunks_by_id[chunk_id].get("authority") for chunk_id in chosen.source_chunk_ids
            if chunks_by_id.get(chunk_id, {}).get("authority")
        ), None)

    return grouped + [chosen]


def missing_fssai_applicability_facts(rule, chunks: list[dict], context: dict) -> list[dict[str, str]]:
    """Ask for profile facts only when retrieved FSSAI text uses those criteria."""
    if not _fssai_authorization(rule, {str(chunk["_id"]): chunk for chunk in chunks}):
        return []
    cited = set(rule.source_chunk_ids)
    relevant = [chunk for chunk in chunks if str(chunk.get("_id")) in cited]
    facts = []
    has_capacity_criterion = any(_CAPACITY.search(chunk.get("text", "")) for chunk in relevant)
    if has_capacity_criterion and context.get("production_capacity_kg_per_day") is None:
        source = next(chunk for chunk in relevant if _CAPACITY.search(chunk.get("text", "")))
        evidence = _CAPACITY.search(source["text"]).group(0).strip()
        facts.append({"field": "production_capacity_kg_per_day", "label": "installed production capacity (kg/day)", "evidence": evidence,
                      "chunk_id": str(source["_id"])})

    activities = " ".join(str(value) for value in context.get("business_activities", []))
    has_packaged_activity = "packag" in activities.casefold()
    has_turnover_criterion = has_packaged_activity and any(
        _TURNOVER.search(chunk.get("text", "")) and "registration" in chunk.get("text", "").casefold()
        for chunk in relevant
    )
    if has_turnover_criterion and context.get("annual_turnover") is None:
        source = next(chunk for chunk in relevant if _TURNOVER.search(chunk.get("text", "")) and "registration" in chunk.get("text", "").casefold())
        evidence = " ".join(_TURNOVER.search(source["text"]).group(0).split())
        facts.append({"field": "annual_turnover", "label": "annual turnover (INR)", "evidence": evidence,
                      "chunk_id": str(source["_id"])})
    return facts
