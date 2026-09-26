from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from bson import ObjectId

from app.core.security import require_business_user
from app.db.mongodb import get_database
from app.models.common import utc_now
from app.schemas.business import BusinessApplicationCreate, BusinessApplicationUpdate, BusinessProfileUpdate
from app.services.audit_service import record_audit
from app.services.business_profile_service import calculate_profile_completion, load_business
from app.services.storage_service import storage

router = APIRouter(prefix="/business", tags=["Business Workspace"])
tenant = lambda user: user["business_id"]

def public(record):
    if not record: return None
    return {("id" if k == "_id" else k): (str(v) if k == "_id" or isinstance(v, ObjectId) else v)
            for k, v in record.items() if k not in {"password_hash", "storage_key"}}

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
async def update_profile(payload: BusinessProfileUpdate, user=Depends(require_business_user)):
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes: raise HTTPException(400, "No profile fields were provided")
    if changes.get("email"): changes["email"] = str(changes["email"]).lower()
    changes["updated_at"] = utc_now()
    db = get_database()
    result = await db.businesses.update_one({"_id": tenant(user)}, {"$set": changes})
    if not result.matched_count: raise HTTPException(404, "Business profile not found")
    business = await db.businesses.find_one({"_id": tenant(user)})
    completion = calculate_profile_completion(business)
    await db.businesses.update_one({"_id": tenant(user)}, {"$set": {"profile_completion": completion["percentage"]}})
    await record_audit("profile_updated", actor_id=user["_id"], business_id=tenant(user), target_type="business", target_id=tenant(user), details={"fields": sorted(changes)})
    return {**public(business), "profile_completion": completion["percentage"], "completion": completion}

@router.get("/profile/completion")
async def profile_completion(user=Depends(require_business_user)):
    return calculate_profile_completion(await require_profile(user))

@router.get("/home")
async def home(user=Depends(require_business_user)):
    db = get_database(); business = await require_profile(user)
    apps = await db.applications.find({"business_id": tenant(user)}).sort("updated_at", -1).to_list(500)
    compliance_count = await db.compliance_items.count_documents({"business_id": tenant(user)})
    return {"business": public(business), "profile_completion": calculate_profile_completion(business),
            "applications": {"total": len(apps), "active": sum(a.get("status") not in {"approved", "rejected", "withdrawn"} for a in apps), "items": [public(a) for a in apps[:5]]},
            "compliance": {"total": compliance_count}, "roadmap": {"status": "not_generated", "message": "Approval roadmap will be generated after business profile evaluation."}}

@router.get("/roadmap")
async def roadmap(user=Depends(require_business_user)):
    await require_profile(user)
    return {"status": "not_generated", "message": "Approval roadmap will be generated after business profile evaluation."}

@router.get("/documents")
async def documents(user=Depends(require_business_user)):
    items = await get_database().business_documents.find({"business_id": tenant(user)}).sort("uploaded_at", -1).to_list(500)
    return {"items": [public(item) for item in items], "total": len(items)}

@router.post("/documents", status_code=201)
async def upload_document(document_type: str = Form(...), file: UploadFile = File(...), user=Depends(require_business_user)):
    if file.content_type != "application/pdf": raise HTTPException(415, "Only PDF documents are supported")
    content = await file.read(15 * 1024 * 1024 + 1)
    if len(content) > 15 * 1024 * 1024: raise HTTPException(413, "File exceeds 15 MB")
    if not content.startswith(b"%PDF-"): raise HTTPException(415, "Uploaded file is not a valid PDF")
    key = await storage.save(file.filename or "document.pdf", content)
    doc = {"business_id": tenant(user), "document_type": document_type.strip(), "document_name": file.filename,
           "storage_key": key, "file_name": file.filename, "mime_type": "application/pdf", "file_size": len(content),
           "status": "uploaded", "uploaded_by": user["_id"], "uploaded_at": utc_now()}
    result = await get_database().business_documents.insert_one(doc); doc["_id"] = result.inserted_id
    await record_audit("document_uploaded", actor_id=user["_id"], business_id=tenant(user), target_type="business_document", target_id=result.inserted_id)
    return public(doc)

@router.get("/documents/{document_id}")
async def get_document(document_id: str, user=Depends(require_business_user)):
    if not ObjectId.is_valid(document_id): raise HTTPException(404, "Document not found")
    item = await get_database().business_documents.find_one({"_id": ObjectId(document_id), "business_id": tenant(user)})
    if not item: raise HTTPException(404, "Document not found")
    return public(item)

@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: str, user=Depends(require_business_user)):
    if not ObjectId.is_valid(document_id): raise HTTPException(404, "Document not found")
    db = get_database(); item = await db.business_documents.find_one({"_id": ObjectId(document_id), "business_id": tenant(user)})
    if not item: raise HTTPException(404, "Document not found")
    await db.business_documents.delete_one({"_id": item["_id"], "business_id": tenant(user)})
    await storage.delete(item["storage_key"])

@router.post("/applications", status_code=201)
async def create_application(payload: BusinessApplicationCreate, user=Depends(require_business_user)):
    await require_profile(user)
    now = utc_now(); record = {"business_id": tenant(user), "approval_name": payload.approval_name, "approval_type": payload.approval_type,
        "department_id": None, "department_name": None, "status": "draft", "current_stage": "draft", "sla_days": None,
        "submitted_at": None, "stage_history": [], "created_at": now, "updated_at": now}
    result = await get_database().applications.insert_one(record); record["_id"] = result.inserted_id
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
    return public(await owned_application(application_id, user))

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
    now = utc_now(); history = item.get("stage_history", []) + [{"stage": "submitted", "status": "completed", "completed_at": now}]
    await get_database().applications.update_one({"_id": item["_id"], "business_id": tenant(user)}, {"$set": {"status": "submitted", "current_stage": "document_verification", "submitted_at": now, "stage_history": history, "updated_at": now}})
    await record_audit("application_submitted", actor_id=user["_id"], business_id=tenant(user), target_type="application", target_id=item["_id"])
    return public(await owned_application(application_id, user))

@router.get("/applications/{application_id}/timeline")
async def application_timeline(application_id: str, user=Depends(require_business_user)):
    item = await owned_application(application_id, user)
    if item.get("status") == "draft": return {"application_id": str(item["_id"]), "current_stage": "draft", "timeline": []}
    stages = ["submitted", "document_verification", "department_review", "inspection", "approval"]
    current = item.get("current_stage"); history = {x.get("stage"): x for x in item.get("stage_history", [])}
    current_index = stages.index(current) if current in stages else 0
    timeline = [{"stage": stage, "status": "completed" if stage in history else "current" if stage == current else "pending", "completed_at": history.get(stage, {}).get("completed_at")} for i, stage in enumerate(stages) if i <= current_index or i > current_index]
    return {"application_id": str(item["_id"]), "current_stage": current, "timeline": timeline}

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
    if item.get("status") == "open": await db.application_actions.update_one({"_id": item["_id"], "business_id": tenant(user)}, {"$set": {"status": "resolved", "resolved_at": utc_now()}})
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
