from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user, require_user_admin
from app.db.mongodb import get_database
from app.models.common import utc_now
from app.schemas.auth import PublicUser
from app.schemas.user import AdminUserCreate, AdminUserUpdate, UserProfileUpdate
from app.services.audit_service import record_audit
from app.core.security import hash_password
from pymongo.errors import DuplicateKeyError

router = APIRouter(prefix="/users", tags=["Users"])
admin_router = APIRouter(prefix="/admin/users", tags=["Admin users"], dependencies=[Depends(require_user_admin)])
audit_router = APIRouter(prefix="/admin/audit", tags=["Admin audit"], dependencies=[Depends(require_user_admin)])


def public_user(record: dict) -> PublicUser:
    return PublicUser(
        id=str(record["_id"]), full_name=record.get("full_name", ""), email=record["email"],
        phone=record.get("phone"), role=record["role"], status=record.get("status", "active"),
        business_ids=[str(value) for value in record.get("business_ids", [])],
        department_id=str(record["department_id"]) if record.get("department_id") else None,
        last_login_at=record["last_login_at"].isoformat() if record.get("last_login_at") else None,
    )


@router.get("/me", response_model=PublicUser)
async def my_profile(user: dict = Depends(get_current_user)):
    return PublicUser(
        id=str(user.get("_id", user.get("id"))), full_name=user.get("full_name", "Paper Jam admin"),
        email=user["email"], phone=user.get("phone"), role=user["role"], status=user.get("status", "active"),
        business_ids=[str(value) for value in user.get("business_ids", [])],
        department_id=str(user["department_id"]) if user.get("department_id") else None,
        last_login_at=user["last_login_at"].isoformat() if user.get("last_login_at") else None,
    )


@router.patch("/me", response_model=PublicUser)
async def update_my_profile(payload: UserProfileUpdate, user: dict = Depends(get_current_user)):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No profile fields were provided")
    changes["updated_at"] = utc_now()
    if user.get("_admin"):
        updated = await get_database().admins.find_one_and_update({"_id": user["_admin"]["_id"]}, {"$set": {**changes, "updated_at": changes["updated_at"]}}, return_document=True)
        user = {**user, **updated}
        return PublicUser(id=str(updated["_id"]), full_name=updated.get("full_name", "Paper Jam admin"), email=updated["email"], role="admin", status="active")
    else:
        updated = await get_database().users.find_one_and_update({"_id": user["_id"]}, {"$set": changes}, return_document=True)
    return public_user(updated)


@admin_router.post("", status_code=201)
async def create_officer(payload: AdminUserCreate, actor: dict = Depends(require_user_admin)):
    email = str(payload.email).lower()
    user = {
        "full_name": payload.full_name.strip(), "email": email, "email_normalized": email,
        "phone": payload.phone.strip(), "password_hash": hash_password(payload.password),
        "role": "officer", "status": "active", "business_ids": [],
        "department_id": payload.department_id, "last_login_at": None,
        "created_at": utc_now(), "updated_at": utc_now(),
    }
    try:
        result = await get_database().users.insert_one(user)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="An account with this email already exists") from exc
    user["_id"] = result.inserted_id
    await record_audit("USER_CREATED", actor_id=actor.get("_id", actor.get("_admin", {}).get("_id")), target_id=result.inserted_id, details={"role": "officer"})
    return public_user(user)


@admin_router.get("")
async def list_users():
    records = await get_database().users.find({}, {"password_hash": 0}).sort("created_at", -1).to_list(1000)
    return {"items": [public_user(record).model_dump(mode="json") for record in records], "total": len(records)}


@audit_router.get("")
async def list_audit_events():
    records = await get_database().audit_logs.find({}).sort("timestamp", -1).to_list(500)
    for record in records:
        record["id"] = str(record.pop("_id"))
        for key in ("actor_user_id", "target_id"):
            if record.get(key) is not None:
                record[key] = str(record[key])
        if record.get("timestamp"):
            record["timestamp"] = record["timestamp"].isoformat()
    return {"items": records, "total": len(records)}


@admin_router.get("/{user_id}")
async def get_user(user_id: str):
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    record = await get_database().users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0})
    if not record:
        raise HTTPException(status_code=404, detail="User not found")
    return public_user(record)


@admin_router.patch("/{user_id}")
async def update_user(user_id: str, payload: AdminUserUpdate, actor: dict = Depends(require_user_admin)):
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No user fields were provided")
    if changes.get("role") == "admin" and not actor.get("_admin") and actor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only an administrator can assign the admin role")
    changes["updated_at"] = utc_now()
    record = await get_database().users.find_one_and_update({"_id": ObjectId(user_id)}, {"$set": changes}, return_document=True)
    if not record:
        raise HTTPException(status_code=404, detail="User not found")
    actor_id = actor.get("_id", actor.get("_admin", {}).get("_id"))
    details = {key: changes[key] for key in changes if key != "updated_at"}
    if "role" in changes:
        await record_audit("USER_ROLE_CHANGED", actor_id=actor_id, target_id=record["_id"], details={"role": changes["role"]})
    if "status" in changes:
        await record_audit("USER_STATUS_CHANGED", actor_id=actor_id, target_id=record["_id"], details={"status": changes["status"]})
    if not ({"role", "status"} & changes.keys()):
        await record_audit("USER_UPDATED", actor_id=actor_id, target_id=record["_id"], details=details)
    return public_user(record)
