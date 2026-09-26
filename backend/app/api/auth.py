from datetime import datetime, timezone
from bson import ObjectId
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from fastapi.security import OAuth2PasswordRequestForm
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token, get_current_user, hash_password, verify_password
from app.db.mongodb import get_database
from app.schemas.auth import AuthResponse, LoginRequest, PublicUser, RegisterRequest, TokenResponse
from app.services.audit_service import record_audit
from app.services.business_profile_service import calculate_profile_completion
from app.schemas.business import BusinessSignupRequest

router = APIRouter(prefix="/auth", tags=["Authentication"])
business_bearer = OAuth2PasswordBearer(tokenUrl="/api/auth/business/login")


def public_user(user: dict) -> PublicUser:
    return PublicUser(
        id=str(user.get("_id", user.get("id"))), full_name=user.get("full_name", "Paper Jam admin"),
        email=user["email"], phone=user.get("phone"), role=user["role"], status=user.get("status", "active"),
        business_ids=[str(value) for value in user.get("business_ids", [])],
        department_id=str(user["department_id"]) if user.get("department_id") else None,
        last_login_at=user.get("last_login_at").isoformat() if user.get("last_login_at") else None,
        business_id=str(user["business_id"]) if user.get("business_id") else None,
    )


@router.post("/register", response_model=PublicUser, status_code=201)
async def register(payload: RegisterRequest) -> PublicUser:
    db = get_database()
    normalized_email = payload.email.lower()
    if await db.users.find_one({"email_normalized": normalized_email}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    now = datetime.now(timezone.utc)
    user = {
        "full_name": payload.full_name, "email": normalized_email, "email_normalized": normalized_email,
        "phone": payload.phone, "password_hash": hash_password(payload.password), "role": "applicant",
        "status": "active", "business_ids": [], "department_id": None,
        "last_login_at": None, "created_at": now, "updated_at": now,
    }
    try:
        result = await db.users.insert_one(user)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="An account with this email already exists") from exc
    user["_id"] = result.inserted_id
    await record_audit("USER_REGISTERED", actor_id=result.inserted_id, target_id=result.inserted_id)
    return public_user(user)


async def _login(email: str, password: str) -> AuthResponse:
    normalized_email = email.strip().lower()
    db = get_database()
    user = await db.users.find_one({"email_normalized": normalized_email})
    if user:
        if user.get("status") != "active" or not verify_password(password, user.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
        now = datetime.now(timezone.utc)
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": now, "updated_at": now}})
        token = create_access_token(str(user["_id"]), role=user["role"], email=user["email"])
        await record_audit("USER_LOGIN", actor_id=user["_id"], target_id=user["_id"])
        settings = get_settings()
        return AuthResponse(access_token=token, expires_in=settings.jwt_expire_minutes * 60, user=public_user(user))

    # Keep Phase 3 administrator credentials in the existing admins collection.
    admin = await db.admins.find_one({"email": {"$regex": f"^{re.escape(normalized_email)}$", "$options": "i"}, "disabled": {"$ne": True}})
    if not admin or not admin.get("hashed_password") or not verify_password(password, admin["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    token = create_access_token(normalized_email, role="admin", email=normalized_email)
    now = datetime.now(timezone.utc)
    await db.admins.update_one({"_id": admin["_id"]}, {"$set": {"last_login_at": now, "updated_at": now}})
    await record_audit("USER_LOGIN", actor_id=admin["_id"], target_id=admin["_id"], details={"role": "admin"})
    settings = get_settings()
    return AuthResponse(
        access_token=token, expires_in=settings.jwt_expire_minutes * 60,
        user=PublicUser(id=str(admin["_id"]), full_name=admin.get("full_name", "Paper Jam admin"), email=normalized_email, role="admin", status="active", last_login_at=now.isoformat()),
    )


@router.post("/login", response_model=AuthResponse, summary="Authenticate an applicant, officer, or administrator")
async def login_json(payload: LoginRequest) -> AuthResponse:
    return await _login(payload.email, payload.password)


@router.post("/token", response_model=TokenResponse, include_in_schema=False)
async def login_form(form: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    result = await _login(form.username, form.password)
    return TokenResponse(access_token=result.access_token)


@router.get("/me", response_model=PublicUser)
async def me(user: dict = Depends(get_current_user)) -> PublicUser:
    return public_user(user)


@router.post("/business/signup", status_code=201)
async def business_signup(payload: BusinessSignupRequest, background_tasks: BackgroundTasks):
    db = get_database()
    email = str(payload.email).strip().lower()
    if (await db.business_users.find_one({"email_normalized": email}, {"_id": 1})
            or await db.users.find_one({"email_normalized": email}, {"_id": 1})):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    now = datetime.now(timezone.utc)
    business_id = ObjectId()
    approval_request_id = ObjectId()
    user = {"business_id": business_id, "name": payload.full_name, "full_name": payload.full_name,
            "email": email, "email_normalized": email, "phone": payload.phone,
            "password_hash": hash_password(payload.password), "role": "business_owner", "status": "active",
            "created_at": now, "updated_at": now}
    business = {"_id": business_id, "user_id": None, "owner_user_id": None,
                "legal_name": payload.legal_name or payload.business_name, "display_name": payload.business_name,
                "business_name": payload.business_name, "business_type": "", "industry": "", "entity_type": "",
                "registration_number": None, "pan": None, "gstin": None, "email": email, "phone": payload.phone,
                "registered_address": {}, "operating_address": {}, "contact_person": {"name": payload.full_name, "email": email, "phone": payload.phone},
                "employee_count": None, "annual_turnover": None, "business_activities": [], "location_details": {},
                "status": "active", "profile_completion": 0, "approval_engine_status": "queued", "approval_engine_request_id": approval_request_id, "created_at": now, "updated_at": now}
    business["profile_completion"] = calculate_profile_completion(business)["percentage"]
    try:
        inserted = await db.business_users.insert_one(user)
        user["_id"] = inserted.inserted_id
        business["user_id"] = inserted.inserted_id
        business["owner_user_id"] = inserted.inserted_id
        await db.businesses.insert_one(business)
        from app.services.passport_service import ensure_passport
        await ensure_passport(business_id, db=db)
    except DuplicateKeyError as exc:
        if user.get("_id"):
            await db.business_users.delete_one({"_id": user["_id"]})
        raise HTTPException(status_code=409, detail="An account with this email already exists") from exc
    except Exception:
        if user.get("_id"):
            await db.business_users.delete_one({"_id": user["_id"]})
        raise
    token = create_access_token(str(user["_id"]), role=user["role"], email=email, business_id=str(business_id))
    await record_audit("business_signup", actor_id=user["_id"], business_id=business_id, target_type="business", target_id=business_id)
    from app.api.approval_engine import run_generation_task
    background_tasks.add_task(run_generation_task, str(business_id), str(approval_request_id))
    return {"access_token": token, "token_type": "bearer", "user": public_user(user), "business": _business_public(business), "expires_in": get_settings().jwt_expire_minutes * 60}


@router.post("/business/login")
async def business_login(payload: LoginRequest):
    email = str(payload.email).strip().lower()
    user = await get_database().business_users.find_one({"email_normalized": email})
    if not user or user.get("status") != "active" or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    now = datetime.now(timezone.utc)
    await get_database().business_users.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": now, "updated_at": now}})
    business = await get_database().businesses.find_one({"_id": user["business_id"]})
    token = create_access_token(str(user["_id"]), role=user["role"], email=email, business_id=str(user["business_id"]))
    await record_audit("business_login", actor_id=user["_id"], business_id=user["business_id"], target_type="business", target_id=user["business_id"])
    return {"access_token": token, "token_type": "bearer", "expires_in": get_settings().jwt_expire_minutes * 60, "user": public_user(user), "business": _business_public(business) if business else None}


@router.get("/business/me")
async def business_account_me(user: dict = Depends(get_current_user)):
    if user.get("role") not in {"business_owner", "business_admin", "business_user"}:
        raise HTTPException(status_code=403, detail="Business account required")
    business = await get_database().businesses.find_one({"_id": user["business_id"]})
    if not business:
        raise HTTPException(status_code=404, detail="Business profile not found")
    return {"user": public_user(user), "business": _business_public(business)}


@router.get("/business/me")
async def business_me(user: dict = Depends(get_current_user)):
    if user.get("role") not in {"business_owner", "business_admin", "business_user"}:
        raise HTTPException(status_code=403, detail="Business account required")
    business = await get_database().businesses.find_one({"_id": user["business_id"]})
    if not business:
        raise HTTPException(status_code=404, detail="Business profile not found")
    return {"user": public_user(user), "business": _business_public(business)}


@router.post("/business/logout")
async def business_logout(token: str = Depends(business_bearer), user: dict = Depends(get_current_user)):
    payload = decode_access_token(token)
    if user.get("role") not in {"business_owner", "business_admin", "business_user"}:
        raise HTTPException(status_code=403, detail="Business account required")
    if payload.get("jti"):
        exp = datetime.fromtimestamp(payload["exp"], timezone.utc)
        await get_database().revoked_tokens.update_one({"_id": payload["jti"]}, {"$set": {"expires_at": exp}}, upsert=True)
    return {"status": "logged_out"}


def _business_public(record: dict | None):
    if not record:
        return None
    internal = {"user_id", "owner_user_id", "approval_engine_request_id", "approval_context_hash", "approval_generation_id"}
    return {key: (str(value) if key == "_id" else value) for key, value in record.items() if key not in internal}
