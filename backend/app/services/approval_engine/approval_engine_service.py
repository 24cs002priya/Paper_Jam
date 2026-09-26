import hashlib
import json
import logging
from datetime import datetime, timezone
from bson import ObjectId

from app.db.mongodb import get_database
from app.models.common import utc_now
from app.schemas.approval_engine import ApprovalEngineResponse, ApprovalResult
from app.services.audit_service import record_audit
from app.services.business_profile_service import calculate_profile_completion
from app.services.approval_engine.context_builder import build_business_context
from app.services.approval_engine.decision_service import decide_rule
from app.services.approval_engine.regulatory_retrieval_service import retrieve_regulatory_chunks
from app.services.approval_engine.roadmap_service import build_roadmap
from app.services.approval_engine.rule_extraction_service import RuleExtractionUnavailable, extract_approval_rules
from app.services.approval_engine.deterministic_rule_service import extract_known_statutory_rules
from app.services.approval_engine.source_service import compact_chunk, source_from_chunk
from app.services.approval_engine.rule_postprocessing_service import coalesce_fssai_authorizations, missing_fssai_applicability_facts

logger = logging.getLogger("paper_jam.approval_engine")


def _approval_family(name: str) -> str:
    lowered = name.casefold()
    for family, terms in (
        ("pollution_consent", ("consent",)),
        ("food_authorization", ("licence", "license", "registration")),
        ("license", ("licence", "license")),
        ("registration", ("registration",)),
        ("permit", ("permit",)),
        ("approval", ("approval",)),
        ("noc", ("noc", "no-objection", "no objection")),
        ("certificate", ("certificate",)),
    ):
        if any(term in lowered for term in terms):
            return family
    return " ".join(lowered.split())


def _merge_source_rules(model_rules, source_rules, chunks):
    """Keep model breadth and add source-matched rules without same-source duplicates."""
    regulation_by_chunk = {str(chunk["_id"]): str(chunk.get("regulation_id") or chunk.get("regulation_name") or "")
                           for chunk in chunks}
    merged = list(model_rules)
    for source_rule in source_rules:
        source_regulations = {regulation_by_chunk.get(chunk_id) for chunk_id in source_rule.source_chunk_ids}
        duplicate = any(
            _approval_family(model_rule.approval_name) == _approval_family(source_rule.approval_name)
            and bool(source_regulations & {regulation_by_chunk.get(chunk_id) for chunk_id in model_rule.source_chunk_ids})
            for model_rule in merged
        )
        if not duplicate:
            merged.append(source_rule)
    return merged


def _json_hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _current_payload(business_id: ObjectId, business: dict | None = None) -> dict:
    business = business or await get_database().businesses.find_one({"_id": business_id})
    if not business:
        return {"business_id": str(business_id), "engine_status": "not_generated", "approvals": [], "roadmap": {"status": "not_generated"}, "missing_information": []}
    generation_id = business.get("approval_generation_id")
    if not generation_id:
        return {"business_id": str(business_id), "engine_status": business.get("approval_engine_status", "not_generated"),
                "approvals": [], "roadmap": {"status": "not_generated"}, "missing_information": calculate_profile_completion(business)["missing_fields"]}
    db = get_database()
    run = await db.approval_engine_runs.find_one({"_id": generation_id, "business_id": business_id})
    approvals = await db.approval_results.find({"business_id": business_id, "generation_id": generation_id}).to_list(200)
    roadmap = await db.approval_roadmaps.find_one({"business_id": business_id, "generation_id": generation_id})
    return {
        "business_id": str(business_id), "generation_id": str(generation_id),
        "engine_status": business.get("approval_engine_status") if business.get("approval_engine_status") in {"queued", "running", "generation_failed"} else (run.get("engine_status", "generated") if run else "not_generated"),
        "approvals": [_serialize(item) for item in approvals],
        "roadmap": roadmap.get("roadmap", {"status": "not_generated"}) if roadmap else {"status": "not_generated"},
        "missing_information": run.get("missing_information", []) if run else [],
        "retrieved_chunk_count": run.get("retrieved_chunk_count", 0) if run else 0,
        "generated_at": run.get("created_at") if run else None,
    }


def _serialize(value: dict, id_field: str = "approval_id") -> dict:
    result = {}
    for key, item in value.items():
        if key == "_id":
            result[id_field] = str(item)
        elif isinstance(item, ObjectId):
            result[key] = str(item)
        elif isinstance(item, list):
            result[key] = [_serialize(v, id_field) if isinstance(v, dict) else (str(v) if isinstance(v, ObjectId) else v) for v in item]
        elif isinstance(item, dict):
            result[key] = _serialize(item, id_field)
        else:
            result[key] = item
    return result


async def _publish_run(business: dict, generation_id: ObjectId, context: dict, context_hash: str,
                       query: str | None, chunks: list[dict], engine_status: str,
                       approvals: list[dict], extracted_rules: list[dict], missing: list[str],
                       error_code: str | None = None) -> dict:
    db = get_database()
    business_id = business["_id"]
    latest_business = await db.businesses.find_one({"_id": business_id})
    if (not latest_business or latest_business.get("approval_engine_request_id") != generation_id
            or _json_hash(build_business_context(latest_business)) != context_hash):
        return await _current_payload(business_id, latest_business)
    roadmap = build_roadmap(approvals)
    now = utc_now()
    stored_approvals = []
    stored_rules = []
    for approval in approvals:
        item = {**approval, "_id": ObjectId(approval["approval_id"]), "business_id": business_id,
                "generation_id": generation_id, "created_at": now, "updated_at": now}
        ApprovalResult.model_validate({**item, "business_id": str(business_id), "generation_id": str(generation_id)})
        stored_approvals.append(item)
    for approval, stored in zip(approvals, stored_approvals, strict=True):
        stored["depends_on"] = [str(dep) for dep in approval.get("depends_on", [])]
    if stored_approvals:
        await db.approval_results.insert_many(stored_approvals, ordered=True)
    for rule in extracted_rules:
        stored_rules.append({"_id": ObjectId(), "business_id": business_id, "generation_id": generation_id,
                             "rule": rule, "created_at": now})
    if stored_rules:
        await db.approval_rules.insert_many(stored_rules, ordered=True)
    run = {"_id": generation_id, "business_id": business_id, "context_hash": context_hash,
           "business_context": context, "regulatory_query": query,
           "retrieved_chunks": [compact_chunk(chunk) for chunk in chunks],
           "extracted_rules": extracted_rules, "engine_status": engine_status,
           "retrieved_chunk_count": len(chunks), "approval_count": len(stored_approvals),
           "missing_information": list(dict.fromkeys(missing)), "error_code": error_code,
           "created_at": now}
    await db.approval_engine_runs.insert_one(run)
    publication = await db.businesses.update_one({"_id": business_id, "approval_engine_request_id": generation_id}, {"$set": {
        "approval_generation_id": generation_id, "approval_engine_status": engine_status,
        "approval_context_hash": context_hash, "updated_at": now,
    }})
    if not publication.matched_count:
        return await _current_payload(business_id)
    await db.approval_roadmaps.insert_one({"_id": generation_id, "business_id": business_id,
                                           "generation_id": generation_id, "roadmap": roadmap, "updated_at": now})
    try:
        await record_audit("approval_engine_generated", business_id=business_id, target_type="approval_generation",
                           target_id=generation_id, details={"status": engine_status, "approval_count": len(stored_approvals), "chunk_count": len(chunks)})
    except Exception:
        logger.exception("Unable to record approval generation audit event")
    return await _current_payload(business_id)


def _make_approval_records(rules, chunks: list[dict], context: dict, business_id: ObjectId, generation_id: ObjectId) -> tuple[list[dict], list[str], list[dict]]:
    rules = coalesce_fssai_authorizations(rules, chunks, context)
    chunk_by_id = {str(chunk["_id"]): chunk for chunk in chunks}
    ids_by_name = {rule.approval_name.casefold().strip(): str(ObjectId()) for rule in rules}
    records = []
    missing = []
    rule_traces = []
    for rule in rules:
        decision = decide_rule(rule, context)
        missing.extend(decision["missing_information"])
        applicability_facts = missing_fssai_applicability_facts(rule, chunks, context)
        if applicability_facts:
            decision["status"] = "needs_information"
            for fact in applicability_facts:
                missing.append(fact["field"])
                decision["conditions"].append({
                    "condition": f"Provide {fact['label']} to determine the applicable FSSAI authorization category",
                    "result": None,
                    "field": fact["field"],
                    "evidence": fact["evidence"],
                })
        references = [chunk_by_id[chunk_id] for chunk_id in rule.source_chunk_ids if chunk_id in chunk_by_id]
        sources = [source_from_chunk(chunk) for chunk in references]
        documents = []
        for requirement in rule.required_documents:
            chunk = chunk_by_id.get(requirement.source_chunk_id)
            if chunk:
                documents.append({"document_name": requirement.document_name, "requirement_type": requirement.requirement_type,
                                  "source": source_from_chunk(chunk), "evidence": requirement.evidence})
        depends_on = []
        for dependency in rule.dependencies:
            target_id = ids_by_name.get(dependency.approval_name.casefold().strip())
            chunk = chunk_by_id.get(dependency.source_chunk_id)
            if target_id and target_id != ids_by_name[rule.approval_name.casefold().strip()] and chunk:
                depends_on.append(target_id)
        reason = f'Retrieved regulatory text states: “{rule.evidence}”'
        if decision["status"] == "needs_information" and decision["missing_information"]:
            reason += " Additional business information is needed to evaluate: " + ", ".join(decision["missing_information"]) + "."
        elif decision["status"] == "not_applicable":
            reason += " The stated applicability condition is not met by the available business profile."
        elif decision["status"] == "mandatory":
            reason += " The rule conditions are satisfied by the available business profile."
        if applicability_facts:
            reason += " The retrieved FSSAI eligibility text makes the authorization category depend on business scale; provide " + ", ".join(fact["label"] for fact in applicability_facts) + " before this can be classified."
        approval_id = ids_by_name[rule.approval_name.casefold().strip()]
        records.append({
            "approval_id": approval_id, "business_id": str(business_id), "generation_id": str(generation_id),
            "approval_name": rule.approval_name, "authority": (sources[0]["authority"] if sources else None),
            "category": rule.category, "status": decision["status"], "confidence": rule.confidence,
            "reason": reason, "conditions": decision["conditions"], "required_documents": documents,
            "regulation_sources": sources, "retrieved_chunks": [compact_chunk(chunk) for chunk in references],
            "depends_on": depends_on,
        })
        rule_traces.append(rule.model_dump(mode="json"))
    return records, list(dict.fromkeys(missing)), rule_traces


async def generate_for_business(business_id: ObjectId | str, *, force: bool = False,
                                request_id: ObjectId | str | None = None) -> dict:
    db = get_database()
    business_id = ObjectId(business_id) if not isinstance(business_id, ObjectId) else business_id
    business = await db.businesses.find_one({"_id": business_id})
    if not business:
        raise ValueError("Business profile not found")
    if request_id is not None:
        request_id = ObjectId(request_id) if not isinstance(request_id, ObjectId) else request_id
        if business.get("approval_engine_request_id") != request_id:
            return await _current_payload(business_id, business)
    context = build_business_context(business)
    context_hash = _json_hash(context)
    if not force and business.get("approval_context_hash") == context_hash and business.get("approval_generation_id"):
        return await _current_payload(business_id, business)
    generation_id = request_id or ObjectId()
    now = utc_now()
    await db.businesses.update_one({"_id": business_id}, {"$set": {"approval_engine_status": "running", "approval_engine_request_id": generation_id, "updated_at": now}})
    missing = calculate_profile_completion(business)["missing_fields"]
    try:
        query, chunks = await retrieve_regulatory_chunks(context)
    except Exception as exc:
        logger.exception("Approval retrieval failed")
        return await _publish_run(business, generation_id, context, context_hash, None, [], "retrieval_unavailable", [], [], missing, type(exc).__name__)
    if not chunks:
        return await _publish_run(business, generation_id, context, context_hash, query, [], "insufficient_regulatory_evidence", [], [], missing)
    # Gather explicit approvals across the complete, source-diverse RAG result;
    # also ask the LLM to identify clauses the generic source parser misses.
    source_extraction = extract_known_statutory_rules(context, chunks)
    try:
        model_extraction = await extract_approval_rules(context, chunks)
    except RuleExtractionUnavailable as exc:
        if not source_extraction.rules:
            logger.warning("Neither regulatory-text matching nor LLM extraction established an approval: code=%s", exc.code)
            return await _publish_run(business, generation_id, context, context_hash, query, chunks, "extraction_unavailable", [], [], missing, exc.code)
        logger.warning("LLM extraction failed; keeping %s directly source-matched approvals: code=%s", len(source_extraction.rules), exc.code)
        extraction = source_extraction
    else:
        model_extraction.rules = _merge_source_rules(model_extraction.rules, source_extraction.rules, chunks)
        extraction = model_extraction
    missing.extend(extraction.missing_information)
    if not extraction.rules:
        return await _publish_run(business, generation_id, context, context_hash, query, chunks, "insufficient_regulatory_evidence", [], [], missing)
    approvals, condition_missing, extracted_rules = _make_approval_records(extraction.rules, chunks, context, business_id, generation_id)
    missing.extend(condition_missing)
    return await _publish_run(business, generation_id, context, context_hash, query, chunks, "generated", approvals, extracted_rules, missing)


async def current_approvals(business_id: ObjectId | str) -> dict:
    business_id = ObjectId(business_id) if not isinstance(business_id, ObjectId) else business_id
    return await _current_payload(business_id)
