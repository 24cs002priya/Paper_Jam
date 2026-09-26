from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from bson import ObjectId
from pymongo import ReturnDocument

from app.core.security import require_admin, require_business_user
from app.db.mongodb import get_database
from app.models.common import utc_now
from app.schemas.business import BusinessApplicationCreate, BusinessApplicationUpdate, BusinessProfileUpdate
from app.services.audit_service import record_audit
from app.services.business_profile_service import calculate_profile_completion, load_business
from app.services.storage_service import storage
from app.services.passport_service import ensure_passport, passport_view, normalize_document_type
from app.services.document_validation_service import inspect_pdf, validate_document_type
from app.services.application_readiness_service import calculate_readiness

router = APIRouter(prefix="/business", tags=["Business Workspace"])
admin_router = APIRouter(prefix="/admin/passports", tags=["Approval Passport Review"])
tenant = lambda user: user["business_id"]

def public(record):
    if not record: return None
    internal = {"password_hash", "storage_key", "approval_engine_request_id", "approval_context_hash"}
    def safe(value):
        if isinstance(value, ObjectId): return str(value)
        if isinstance(value, list): return [safe(item) for item in value]
        if isinstance(value, dict): return {("id" if key == "_id" else key): safe(item) for key, item in value.items() if key not in internal}
        return value
    return {("id" if key == "_id" else key): safe(value) for key, value in record.items() if key not in internal}

async def require_profile(user):
    record = await load_business(user)
    if not record: raise HTTPException(404, "Business profile not found")
    return record

@router.get("/me")
async def business_me(user=Depends(require_business_user)):
    return {"business": public(await require_profile(user)), "user": {"id": str(user["_id"]), "full_name": user.get("full_name", user.get("name")), "email": user["email"], "role": user["role"]}}

@router.get("/profile")
async def get_profile(user=Depends(require_business_user)):
    business = await require_profile(user)
    return {**public(business), "completion": calculate_profile_completion(business)}

@router.put("/profile")
async def update_profile(payload: BusinessProfileUpdate, background_tasks: BackgroundTasks, user=Depends(require_business_user)):
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes: raise HTTPException(400, "No profile fields were provided")
    if changes.get("email"): changes["email"] = str(changes["email"]).lower()
    changes["updated_at"] = utc_now()
    changes["approval_engine_status"] = "queued"
    changes["approval_engine_request_id"] = ObjectId()
    db = get_database()
    result = await db.businesses.update_one({"_id": tenant(user)}, {"$set": changes})
    if not result.matched_count: raise HTTPException(404, "Business profile not found")
    business = await db.businesses.find_one({"_id": tenant(user)})
    passport = await ensure_passport(tenant(user), db=db)
    await db.approval_passports.update_one({"_id": passport["_id"]}, {"$set": {"updated_at": utc_now()}})
    completion = calculate_profile_completion(business)
    await db.businesses.update_one({"_id": tenant(user)}, {"$set": {"profile_completion": completion["percentage"]}})
    await record_audit("profile_updated", actor_id=user["_id"], business_id=tenant(user), target_type="business", target_id=tenant(user), details={"fields": sorted(changes)})
    from app.api.approval_engine import run_generation_task
    background_tasks.add_task(run_generation_task, str(tenant(user)), str(changes["approval_engine_request_id"]))
    return {**public(business), "profile_completion": completion["percentage"], "completion": completion}


@router.get("/passport")
async def get_passport(user=Depends(require_business_user)):
    await require_profile(user)
    return await passport_view(tenant(user), db=get_database())


@router.patch("/passport")
async def update_passport_profile(payload: BusinessProfileUpdate, background_tasks: BackgroundTasks,
                                  user=Depends(require_business_user)):
    # Passport business information is the canonical business profile, never a second data copy.
    return await update_profile(payload, background_tasks, user)

@router.get("/profile/completion")
async def profile_completion(user=Depends(require_business_user)):
    return calculate_profile_completion(await require_profile(user))

@router.get("/home")
async def home(user=Depends(require_business_user)):
    db = get_database(); business = await require_profile(user)
    apps = await db.applications.find({"business_id": tenant(user)}).sort("updated_at", -1).to_list(500)
    compliance_count = await db.compliance_items.count_documents({"business_id": tenant(user)})
    from app.services.approval_engine.approval_engine_service import current_approvals
    approval_data = await current_approvals(tenant(user))
    applicable = [item for item in approval_data.get("approvals", []) if item.get("status") != "not_applicable"]
    status = approval_data.get("engine_status", "not_generated")
    chunk_count = approval_data.get("retrieved_chunk_count", 0)
    roadmap_message = {
        "generated": f"{len(applicable)} evidence-based approval results are available.",
        "queued": "The approval engine is queued to evaluate regulatory evidence.",
        "running": "The approval engine is evaluating regulatory evidence.",
        "insufficient_regulatory_evidence": (
            "No active regulatory text matched this profile. Check that a regulation version is active and its vector index is ready."
            if chunk_count == 0 else
            f"Retrieved {chunk_count} regulatory text chunk(s), but none supported a validated approval rule."
        ),
        "retrieval_unavailable": "Regulatory search is unavailable. Check the regulatory vector search configuration, then retry.",
        "extraction_unavailable": "Regulatory text was retrieved, but approval rules could not be evaluated. Retry when the extraction service is available.",
        "generation_failed": "The latest approval evaluation failed. Retry the evaluation.",
        "not_generated": "The approval roadmap has not been generated yet.",
    }.get(status, "The approval evaluation has an unknown status. Retry the evaluation.")
    return {"business": public(business), "profile_completion": calculate_profile_completion(business),
            "applications": {"total": len(apps), "active": sum(a.get("status") not in {"approved", "rejected", "withdrawn"} for a in apps), "items": [public(a) for a in apps[:5]]},
            "compliance": {"total": compliance_count}, "roadmap": {"status": status, "message": roadmap_message, "approval_count": len(applicable), "retrieved_chunk_count": chunk_count}}

@router.get("/roadmap")
async def roadmap(user=Depends(require_business_user)):
    await require_profile(user)
    return {"status": "not_generated", "message": "Approval roadmap will be generated after business profile evaluation."}

@router.get("/documents")
async def documents(user=Depends(require_business_user)):
    items = await get_database().business_documents.find({"business_id": tenant(user)}).sort("uploaded_at", -1).to_list(500)
    return {"items": [{**public(item), "file_url": f"/api/business/documents/{item['_id']}/content"} for item in items], "total": len(items)}

@router.post("/documents", status_code=201)
async def upload_document(document_type: str = Form(...), file: UploadFile = File(...),
                          application_id: str | None = Form(None), action_id: str | None = Form(None),
                          user=Depends(require_business_user)):
    if file.content_type != "application/pdf" or not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, "Only PDF documents are supported")
    content = await file.read(15 * 1024 * 1024 + 1)
    if len(content) > 15 * 1024 * 1024: raise HTTPException(413, "File exceeds 15 MB")
    if not content or not content.startswith(b"%PDF-"): raise HTTPException(415, "Uploaded file is not a valid PDF")
    document_type = " ".join(document_type.split())
    if not document_type or len(document_type) > 200: raise HTTPException(422, "A valid document type is required")
    application = None
    if application_id:
        application = await owned_application(application_id, user)
        if application.get("status") != "draft": raise HTTPException(409, "Documents cannot be linked to a submitted application")
    action = None
    if action_id:
        if not ObjectId.is_valid(action_id): raise HTTPException(404, "Action not found")
        action = await get_database().application_actions.find_one({"_id": ObjectId(action_id), "business_id": tenant(user), "status": "open"})
        if not action: raise HTTPException(404, "Action not found")
    try:
        pages, extracted, extraction_metadata = await inspect_pdf(content)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db = get_database()
    source_text = "\n".join(page.text for page in pages)
    validation_status, type_validation_error = validate_document_type(document_type, file.filename or "", source_text)
    passport = await ensure_passport(tenant(user), db=db)
    type_key = normalize_document_type(document_type)
    latest = await db.business_documents.find_one({"business_id": tenant(user), "$or": [
        {"document_type_key": type_key}, {"document_type_key": {"$exists": False}, "document_type": document_type}],
    }, sort=[("version", -1)])
    counter_filter = {"business_id": tenant(user), "document_type_key": type_key}
    await db.business_document_counters.update_one(counter_filter, {"$max": {"sequence": latest.get("version", 0) if latest else 0}}, upsert=True)
    counter = await db.business_document_counters.find_one_and_update(counter_filter, {"$inc": {"sequence": 1}},
                                                                       return_document=ReturnDocument.AFTER)
    version = counter["sequence"]
    key = await storage.save(file.filename or "document.pdf", content)
    now = utc_now()
    doc = {"business_id": tenant(user), "passport_id": passport["_id"], "document_type": document_type,
           "document_type_key": type_key, "document_name": file.filename,
           "storage_key": key, "file_name": file.filename, "mime_type": "application/pdf", "file_size": len(content),
           "status": "uploaded", "lifecycle_status": "active", "version": version,
           "verification_status": "pending", "extraction_status": "completed", "validation_status": validation_status,
           "validation_errors": [type_validation_error] if type_validation_error else [], "extracted_data": extracted, "extraction_metadata": extraction_metadata,
           "used_for_approvals": [], "expiry_date": extracted.get("expiry_date"), "uploaded_by": user["_id"], "uploaded_at": now, "created_at": now}
    try:
        result = await db.business_documents.insert_one(doc)
    except Exception:
        await storage.delete(key)
        raise
    doc["_id"] = result.inserted_id
    await db.business_documents.update_many({"business_id": tenant(user), "version": {"$lt": version}, "$or": [
        {"document_type_key": type_key}, {"document_type_key": {"$exists": False}, "document_type": document_type}],
    }, {"$set": {"lifecycle_status": "superseded", "superseded_by": result.inserted_id}})
    await db.approval_passports.update_one({"_id": passport["_id"]},
                                           {"$addToSet": {"document_ids": result.inserted_id}, "$set": {"updated_at": now}})
    if action:
        await db.application_actions.update_one({"_id": action["_id"]}, {"$addToSet": {"document_ids": result.inserted_id}})
    if application:
        await db.applications.update_one({"_id": application["_id"]}, {"$addToSet": {"document_links": {"document_id": result.inserted_id, "version": version}}})
    await record_audit("document_uploaded", actor_id=user["_id"], business_id=tenant(user), target_type="business_document", target_id=result.inserted_id)
    return public(doc)


@router.get("/documents/{document_id}/content")
async def document_content(document_id: str, user=Depends(require_business_user)):
    item = await _owned_document(document_id, user)
    try:
        content = await storage.read(item["storage_key"])
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Stored document is unavailable")
    safe_filename = item.get("file_name", "document.pdf").replace('"', "").replace("\r", "").replace("\n", "")
    return Response(content, media_type=item.get("mime_type", "application/pdf"),
                    headers={"Content-Disposition": f'inline; filename="{safe_filename}"',
                             "X-Content-Type-Options": "nosniff"})

@router.get("/documents/{document_id}")
async def get_document(document_id: str, user=Depends(require_business_user)):
    return public(await _owned_document(document_id, user))


@admin_router.patch("/{business_id}/documents/{document_id}/verification")
async def update_document_verification(business_id: str, document_id: str, payload: dict,
                                      admin=Depends(require_admin)):
    if not ObjectId.is_valid(business_id) or not ObjectId.is_valid(document_id):
        raise HTTPException(404, "Document not found")
    status = payload.get("verification_status")
    if status not in {"verified", "rejected", "needs_update", "pending"} or set(payload) != {"verification_status"}:
        raise HTTPException(422, "Provide one valid verification_status")
    db = get_database(); bid = ObjectId(business_id); did = ObjectId(document_id)
    item = await db.business_documents.find_one({"_id": did, "business_id": bid})
    if not item: raise HTTPException(404, "Document not found")
    now = utc_now()
    await db.business_documents.update_one({"_id": did, "business_id": bid},
                                           {"$set": {"verification_status": status, "verified_by": admin["_id"], "verified_at": now}})
    await record_audit("passport_document_verification_updated", actor_id=admin["_id"], business_id=bid,
                       target_type="business_document", target_id=did, details={"verification_status": status})
    return public(await db.business_documents.find_one({"_id": did, "business_id": bid}))


@admin_router.patch("/{business_id}/documents/{document_id}/validation")
async def update_document_validation(business_id: str, document_id: str, payload: dict,
                                     admin=Depends(require_admin)):
    if not ObjectId.is_valid(business_id) or not ObjectId.is_valid(document_id):
        raise HTTPException(404, "Document not found")
    status = payload.get("validation_status")
    if status not in {"passed", "failed", "needs_review"} or set(payload) != {"validation_status"}:
        raise HTTPException(422, "Provide one valid validation_status")
    db = get_database(); bid = ObjectId(business_id); did = ObjectId(document_id)
    item = await db.business_documents.find_one({"_id": did, "business_id": bid})
    if not item: raise HTTPException(404, "Document not found")
    now = utc_now()
    await db.business_documents.update_one({"_id": did, "business_id": bid},
                                           {"$set": {"validation_status": status, "validation_reviewed_by": admin["_id"],
                                                      "validation_reviewed_at": now,
                                                      "validation_errors": [] if status == "passed" else item.get("validation_errors", [])}})
    await record_audit("passport_document_validation_updated", actor_id=admin["_id"], business_id=bid,
                       target_type="business_document", target_id=did, details={"validation_status": status})
    return public(await db.business_documents.find_one({"_id": did, "business_id": bid}))


async def _owned_document(document_id: str, user):
    if not ObjectId.is_valid(document_id): raise HTTPException(404, "Document not found")
    item = await get_database().business_documents.find_one({"_id": ObjectId(document_id), "business_id": tenant(user)})
    if not item: raise HTTPException(404, "Document not found")
    return item

@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: str, user=Depends(require_business_user)):
    if not ObjectId.is_valid(document_id): raise HTTPException(404, "Document not found")
    db = get_database(); item = await db.business_documents.find_one({"_id": ObjectId(document_id), "business_id": tenant(user)})
    if not item: raise HTTPException(404, "Document not found")
    linked = await db.applications.find_one({"business_id": tenant(user), "document_links.document_id": item["_id"], "status": {"$ne": "draft"}})
    if linked: raise HTTPException(409, "A document version referenced by a submitted application cannot be removed")
    await db.business_documents.update_one({"_id": item["_id"], "business_id": tenant(user)},
                                           {"$set": {"lifecycle_status": "archived", "archived_at": utc_now()}})

@router.post("/applications", status_code=201)
async def create_application(payload: BusinessApplicationCreate, user=Depends(require_business_user)):
    await require_profile(user)
    db = get_database()
    business = await db.businesses.find_one({"_id": tenant(user)}, {"approval_generation_id": 1})
    if not business or not business.get("approval_generation_id"):
        raise HTTPException(409, "Approval roadmap is not ready yet")
    approval = await db.approval_results.find_one({"_id": ObjectId(payload.approval_id), "business_id": tenant(user), "generation_id": business["approval_generation_id"]})
    if not approval:
        raise HTTPException(404, "Applicable approval not found")
    passport = await ensure_passport(tenant(user), db=db)
    if approval.get("status") not in {"mandatory", "conditional", "needs_information"}:
        raise HTTPException(409, "This approval is not currently applicable")
    existing = await db.applications.find_one({"business_id": tenant(user), "approval_name": approval["approval_name"], "status": "draft"})
    if existing:
        await db.applications.update_one({"_id": existing["_id"], "business_id": tenant(user)}, {"$set": {
            "approval_id": approval["_id"], "approval_generation_id": business["approval_generation_id"],
            "authority": approval.get("authority"), "department_name": approval.get("authority"), "updated_at": utc_now()}})
        existing = await db.applications.find_one({"_id": existing["_id"], "business_id": tenant(user)})
        return {**public(existing), "readiness": await calculate_readiness(existing)}
    now = utc_now(); record = {"business_id": tenant(user), "approval_id": approval["_id"], "approval_generation_id": business["approval_generation_id"],
        "passport_id": passport["_id"], "authority": approval.get("authority"),
        "approval_name": approval["approval_name"], "approval_type": approval.get("category") or approval["approval_name"],
        "department_id": None, "department_name": approval.get("authority"), "status": "draft", "current_stage": "draft", "sla_days": None,
        "submitted_at": None, "stage_history": [{"stage": "draft_created", "status": "completed", "occurred_at": now}],
        "created_at": now, "updated_at": now}
    result = await db.applications.insert_one(record); record["_id"] = result.inserted_id
    await record_audit("application_created", actor_id=user["_id"], business_id=tenant(user), target_type="application", target_id=result.inserted_id)
    return public(record)

@router.get("/applications")
async def applications(user=Depends(require_business_user)):
    items = await get_database().applications.find({"business_id": tenant(user)}).sort("updated_at", -1).to_list(500)
    return {"items": [public(item) for item in items], "total": len(items)}

async def owned_application(application_id, user):
    if not ObjectId.is_valid(application_id): raise HTTPException(404, "Application not found")
    item = await get_database().applications.find_one({"_id": ObjectId(application_id), "business_id": tenant(user)})
    if not item: raise HTTPException(404, "Application not found")
    return item

@router.get("/applications/{application_id}")
async def get_application(application_id: str, user=Depends(require_business_user)):
    item = await owned_application(application_id, user)
    readiness = await calculate_readiness(item)
    return {**public(item), "readiness": readiness}


@router.get("/applications/{application_id}/readiness")
async def application_readiness(application_id: str, user=Depends(require_business_user)):
    return await calculate_readiness(await owned_application(application_id, user))


@router.post("/applications/{application_id}/validate")
async def validate_application(application_id: str, user=Depends(require_business_user)):
    return await calculate_readiness(await owned_application(application_id, user))


@router.post("/applications/{application_id}/documents/{document_id}")
async def link_application_document(application_id: str, document_id: str, user=Depends(require_business_user)):
    app = await owned_application(application_id, user)
    if app.get("status") != "draft": raise HTTPException(409, "Submitted application documents are immutable")
    document = await _owned_document(document_id, user)
    if document.get("lifecycle_status", "active") != "active": raise HTTPException(409, "Only the active document version can be linked")
    await get_database().applications.update_one({"_id": app["_id"], "business_id": tenant(user)},
                                                 {"$addToSet": {"document_links": {"document_id": document["_id"], "version": document.get("version", 1)}}})
    return await calculate_readiness(app)

@router.put("/applications/{application_id}")
async def update_application(application_id: str, payload: BusinessApplicationUpdate, user=Depends(require_business_user)):
    item = await owned_application(application_id, user)
    if item.get("status") != "draft": raise HTTPException(409, "Only draft applications can be edited")
    changes = {k: v.strip() for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not changes: raise HTTPException(400, "No application fields were provided")
    changes["updated_at"] = utc_now()
    await get_database().applications.update_one({"_id": item["_id"], "business_id": tenant(user)}, {"$set": changes})
    return public(await owned_application(application_id, user))

@router.post("/applications/{application_id}/submit")
async def submit_application(application_id: str, user=Depends(require_business_user)):
    item = await owned_application(application_id, user)
    if item.get("status") != "draft": raise HTTPException(409, "Application is not a draft")
    readiness = await calculate_readiness(item)
    if not readiness.get("can_submit"):
        raise HTTPException(409, detail={"message": "Application is not ready to submit", "readiness": readiness})
    item = await owned_application(application_id, user)
    now = utc_now(); history = item.get("stage_history", []) + [{"stage": "submitted", "status": "completed", "occurred_at": now, "authority": item.get("authority")}]
    snapshot = {**readiness, "readiness_status": "ready", "readiness_percentage": 100, "can_submit": False}
    await get_database().applications.update_one({"_id": item["_id"], "business_id": tenant(user)}, {"$set": {
        "status": "submitted", "current_stage": "submitted", "submitted_at": now, "stage_history": history,
        "readiness_snapshot": snapshot, "submission_document_links": item.get("document_links", []),
        "updated_at": now}})
    await record_audit("application_submitted", actor_id=user["_id"], business_id=tenant(user), target_type="application", target_id=item["_id"])
    return public(await owned_application(application_id, user))

@router.get("/applications/{application_id}/timeline")
async def application_timeline(application_id: str, user=Depends(require_business_user)):
    item = await owned_application(application_id, user)
    history = item.get("stage_history", [])
    timeline = [{"stage": event.get("stage", "status_update"), "status": event.get("status", "recorded"),
                 "completed_at": event.get("occurred_at", event.get("completed_at")), "authority": event.get("authority"),
                 "message": event.get("message")} for event in history]
    return {"application_id": str(item["_id"]), "current_stage": item.get("current_stage", item.get("status", "draft")), "timeline": timeline}

@router.get("/actions")
async def actions(user=Depends(require_business_user)):
    items = await get_database().application_actions.find({"business_id": tenant(user)}).sort("created_at", -1).to_list(500)
    return {"items": [public(item) for item in items], "total": len(items)}

@router.get("/actions/{action_id}")
async def get_action(action_id: str, user=Depends(require_business_user)):
    if not ObjectId.is_valid(action_id): raise HTTPException(404, "Action not found")
    item = await get_database().application_actions.find_one({"_id": ObjectId(action_id), "business_id": tenant(user)})
    if not item: raise HTTPException(404, "Action not found")
    return public(item)

@router.post("/actions/{action_id}/resolve")
async def resolve_action(action_id: str, user=Depends(require_business_user)):
    if not ObjectId.is_valid(action_id): raise HTTPException(404, "Action not found")
    db = get_database(); item = await db.application_actions.find_one({"_id": ObjectId(action_id), "business_id": tenant(user)})
    if not item: raise HTTPException(404, "Action not found")
    if item.get("status") == "open":
        document_ids = item.get("document_ids", [])
        if not document_ids: raise HTTPException(409, "Upload the requested document before resolving this action")
        documents = await db.business_documents.find({"_id": {"$in": document_ids}, "business_id": tenant(user),
                                                       "lifecycle_status": "active", "validation_status": "passed"}).to_list(100)
        expected = normalize_document_type(str(item.get("document_type", "")))
        if expected and not any(normalize_document_type(doc.get("document_type", "")) == expected for doc in documents):
            raise HTTPException(409, "Upload a validated document matching the request before resolving this action")
        await db.application_actions.update_one({"_id": item["_id"], "business_id": tenant(user)}, {"$set": {"status": "resolved", "resolved_at": utc_now()}})
    await record_audit("action_resolved", actor_id=user["_id"], business_id=tenant(user), target_type="application_action", target_id=item["_id"])
    return public(await db.application_actions.find_one({"_id": item["_id"], "business_id": tenant(user)}))

@router.get("/compliance")
async def compliance(user=Depends(require_business_user)):
    items = await get_database().compliance_items.find({"business_id": tenant(user)}).sort("due_date", 1).to_list(500)
    return {"items": [public(item) for item in items], "total": len(items)}

@router.get("/regulation-changes")
async def regulation_changes(user=Depends(require_business_user)):
    items = await get_database().regulation_changes.find({"business_id": tenant(user)}).sort("created_at", -1).to_list(500)
    return {"items": [public(item) for item in items], "total": len(items)}
