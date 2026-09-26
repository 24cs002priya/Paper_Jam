from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user, require_applicant
from app.db.mongodb import get_database
from app.models.common import utc_now
from app.schemas.business import BusinessCreate, BusinessResponse, BusinessUpdate
from app.services.audit_service import record_audit

router = APIRouter(prefix="/businesses", tags=["Businesses"])


def serialize_business(record: dict) -> BusinessResponse:
    return BusinessResponse(
        id=str(record["_id"]), business_name=record["business_name"], legal_name=record.get("legal_name"),
        business_type=record["business_type"], industry=record["industry"], description=record.get("description", ""),
        entity_type=record["entity_type"], registration_number=record.get("registration_number"),
        contact=record["contact"], address=record["address"], status=record["status"],
        created_at=record["created_at"], updated_at=record["updated_at"],
    )


def business_filter_for(user: dict) -> dict:
    if user.get("role") == "admin":
        return {}
    if user.get("role") == "applicant":
        return {"owner_user_id": user["_id"]}
    return {"authorized_user_ids": user["_id"]}


@router.post("", response_model=BusinessResponse, status_code=201)
async def create_business(payload: BusinessCreate, user: dict = Depends(require_applicant)):
    now = utc_now()
    business = payload.model_dump(mode="json")
    business.update({"owner_user_id": user["_id"], "status": "draft", "created_at": now, "updated_at": now})
    result = await get_database().businesses.insert_one(business)
    business["_id"] = result.inserted_id
    from app.services.passport_service import ensure_passport
    await ensure_passport(result.inserted_id, db=get_database())
    await get_database().users.update_one({"_id": user["_id"]}, {"$addToSet": {"business_ids": result.inserted_id}, "$set": {"updated_at": now}})
    await record_audit("BUSINESS_CREATED", actor_id=user["_id"], target_type="business", target_id=result.inserted_id)
    return serialize_business(business)


@router.get("")
async def list_businesses(user: dict = Depends(get_current_user)):
    records = await get_database().businesses.find(business_filter_for(user)).sort("updated_at", -1).to_list(500)
    return {"items": [serialize_business(item).model_dump(mode="json") for item in records], "total": len(records)}


@router.get("/{business_id}", response_model=BusinessResponse)
async def get_business(business_id: str, user: dict = Depends(get_current_user)):
    if not ObjectId.is_valid(business_id):
        raise HTTPException(status_code=404, detail="Business not found")
    record = await get_database().businesses.find_one({"_id": ObjectId(business_id), **business_filter_for(user)})
    if not record:
        raise HTTPException(status_code=404, detail="Business not found")
    return serialize_business(record)


@router.patch("/{business_id}", response_model=BusinessResponse)
async def update_business(business_id: str, payload: BusinessUpdate, user: dict = Depends(require_applicant)):
    if not ObjectId.is_valid(business_id):
        raise HTTPException(status_code=404, detail="Business not found")
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise HTTPException(status_code=400, detail="No business fields were provided")
    changes["updated_at"] = utc_now()
    record = await get_database().businesses.find_one_and_update(
        {"_id": ObjectId(business_id), "owner_user_id": user["_id"]}, {"$set": changes}, return_document=True,
    )
    if not record:
        raise HTTPException(status_code=404, detail="Business not found")
    await record_audit("BUSINESS_UPDATED", actor_id=user["_id"], target_type="business", target_id=record["_id"], details={"fields": sorted(changes)})
    return serialize_business(record)
