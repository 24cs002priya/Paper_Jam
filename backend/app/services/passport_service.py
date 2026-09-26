from __future__ import annotations

import re
from bson import ObjectId

from app.db.mongodb import get_database
from app.models.common import utc_now


def normalize_document_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


async def ensure_passport(business_id: ObjectId, db=None) -> dict:
    """Create one database-backed passport per business; business data stays canonical in businesses."""
    if db is None:
        db = get_database()
    now = utc_now()
    await db.approval_passports.update_one(
        {"business_id": business_id},
        {"$setOnInsert": {"business_id": business_id, "status": "active", "created_at": now,
                           "updated_at": now, "document_ids": []}},
        upsert=True,
    )
    return await db.approval_passports.find_one({"business_id": business_id})


async def passport_view(business_id: ObjectId, db=None) -> dict:
    if db is None:
        db = get_database()
    passport = await ensure_passport(business_id, db=db)
    business = await db.businesses.find_one({"_id": business_id}) or {}
    documents = await db.business_documents.find({"business_id": business_id}).sort(
        [("document_type_key", 1), ("version", -1)]
    ).to_list(1000)
    statuses = [item.get("verification_status", "pending") for item in documents
                if item.get("lifecycle_status", "active") == "active"]
    verified = sum(status == "verified" for status in statuses)
    aggregate = "not_verified" if not verified else "verified" if verified == len(statuses) and statuses else "partially_verified"
    approvals = await db.approval_results.find({"business_id": business_id,
                                                 "generation_id": business.get("approval_generation_id"),
                                                 "status": {"$ne": "not_applicable"}}).to_list(200)
    app_items = await db.applications.find({"business_id": business_id}).to_list(500)
    links = []
    by_approval: dict[str, dict] = {}
    for approval in approvals:
        key = str(approval["_id"])
        by_approval[key] = {"approval_id": key, "approval_name": approval.get("approval_name"),
                            "status": approval.get("status"), "application_ids": []}
    for item in app_items:
        key = str(item.get("approval_id", ""))
        if key in by_approval:
            by_approval[key]["application_ids"].append(str(item["_id"]))
    links = list(by_approval.values())
    return {"id": str(passport["_id"]), "business_id": str(business_id), "status": passport.get("status", "active"),
            "business_information": {key: value for key, value in business.items()
                                     if key not in {"_id", "user_id", "owner_user_id", "password_hash", "storage_key",
                                                    "approval_engine_request_id", "approval_context_hash",
                                                    "approval_generation_id", "approval_engine_status"}},
            "verification": {"status": aggregate, "verified_documents": verified, "total_documents": len(statuses)},
            "documents": [_public_document(item) for item in documents], "approval_links": links,
            "created_at": passport.get("created_at"), "updated_at": passport.get("updated_at")}


def _public_document(document: dict) -> dict:
    result = {key: value for key, value in document.items() if key not in {"_id", "storage_key", "uploaded_by"}}
    result["id"] = str(document["_id"])
    result["passport_id"] = str(document.get("passport_id", ""))
    result["business_id"] = str(document.get("business_id", ""))
    result["used_for_approvals"] = [str(item) for item in document.get("used_for_approvals", [])]
    result["file_url"] = f"/api/business/documents/{document['_id']}/content"
    result.setdefault("lifecycle_status", "active")
    result.setdefault("version", 1)
    result.setdefault("verification_status", "pending")
    result.setdefault("extraction_status", "unavailable")
    result.setdefault("validation_status", "needs_review")
    result.setdefault("validation_errors", [])
    result.setdefault("extracted_data", {})
    return result
