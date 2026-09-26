from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from bson import ObjectId
import re

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.db.mongodb import get_database

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_access_token(subject: str, *, role: str = "admin", email: str | None = None, business_id: str | None = None) -> str:
    settings = get_settings()
    if not settings.jwt_secret.get_secret_value():
        raise RuntimeError("JWT_SECRET is not configured")
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": subject, "role": role, "jti": uuid4().hex, "exp": expires}
    if email:
        payload["email"] = email
    if business_id:
        payload["business_id"] = business_id
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"])
    except (InvalidTokenError, RuntimeError):
        raise HTTPException(status_code=401, detail="Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    payload = decode_access_token(token)
    subject = payload.get("sub")
    role = payload.get("role")
    if not subject:
        raise HTTPException(status_code=401, detail="Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})
    db = get_database()
    revoked_tokens = getattr(db, "revoked_tokens", None)
    if payload.get("jti") and revoked_tokens is not None and await revoked_tokens.find_one({"_id": payload["jti"]}, {"_id": 1}):
        raise HTTPException(status_code=401, detail="Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})
    if role in {"business_owner", "business_admin", "business_user"} and ObjectId.is_valid(subject):
        user = await db.business_users.find_one({"_id": ObjectId(subject), "role": role, "status": "active"})
        business_id = payload.get("business_id")
        if user and ObjectId.is_valid(business_id or "") and str(user.get("business_id")) == business_id:
            user["business_id"] = ObjectId(business_id)
            return user
    if role == "admin":
        admin = await db.admins.find_one({"email": {"$regex": f"^{re.escape(str(subject))}$", "$options": "i"}, "disabled": {"$ne": True}})
        if admin:
            return {"id": str(admin["_id"]), "email": admin["email"], "full_name": admin.get("full_name", "Paper Jam admin"), "role": "admin", "status": "active", "business_ids": [], "_admin": admin}
        if ObjectId.is_valid(subject):
            user = await db.users.find_one({"_id": ObjectId(subject), "role": "admin", "status": "active"})
            if user:
                return user
    elif role is None:
        legacy_admin = await db.admins.find_one({"email": {"$regex": f"^{re.escape(str(subject))}$", "$options": "i"}, "disabled": {"$ne": True}})
        if legacy_admin:
            return {"id": str(legacy_admin["_id"]), "email": legacy_admin["email"], "full_name": legacy_admin.get("full_name", "Paper Jam admin"), "role": "admin", "status": "active", "business_ids": [], "_admin": legacy_admin}
    elif ObjectId.is_valid(subject):
        user = await db.users.find_one({"_id": ObjectId(subject), "status": "active"})
        if user and user.get("role") == role and user.get("role") in {"applicant", "officer"}:
            return user
    raise HTTPException(status_code=401, detail="Account is unavailable", headers={"WWW-Authenticate": "Bearer"})


def require_roles(*roles: str):
    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dependency


require_applicant = require_roles("applicant")
require_officer = require_roles("officer")
require_user_admin = require_roles("admin")
require_business_user = require_roles("business_owner", "business_admin", "business_user")


async def require_admin(token: str = Depends(oauth2_scheme)) -> dict:
    payload = decode_access_token(token)
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})
    if payload.get("role") == "admin" and ObjectId.is_valid(subject):
        user = await get_database().users.find_one({"_id": ObjectId(subject), "role": "admin", "status": "active"})
        if user:
            return user
    admin = await get_database().admins.find_one({"email": {"$regex": f"^{re.escape(str(subject))}$", "$options": "i"}, "disabled": {"$ne": True}})
    if not admin:
        raise HTTPException(status_code=403 if payload.get("role") else 401, detail="Administrator access required")
    return admin
