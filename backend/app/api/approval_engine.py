import logging

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.core.security import require_admin, require_business_user
from app.db.mongodb import get_database
from app.schemas.approval_engine import ApprovalEngineResponse, GenerateApprovalsRequest
from app.services.approval_engine.approval_engine_service import current_approvals, generate_for_business

logger = logging.getLogger("paper_jam.approval_engine.api")
router = APIRouter(prefix="/approval-engine", tags=["Approval Engine"])
admin_router = APIRouter(prefix="/admin/approval-engine", tags=["Approval Engine Debug"])


def _owned_business_id(user: dict, business_id: str) -> ObjectId:
    if not ObjectId.is_valid(business_id) or str(user.get("business_id")) != business_id.lower():
        raise HTTPException(status_code=404, detail="Business profile not found")
    return ObjectId(business_id)


async def run_generation_task(business_id: str, request_id: str) -> None:
    try:
        await generate_for_business(business_id, force=True, request_id=request_id)
    except Exception as exc:
        logger.exception("Automatic approval generation failed")
        if ObjectId.is_valid(business_id):
            await get_database().businesses.update_one({"_id": ObjectId(business_id), "approval_engine_request_id": ObjectId(request_id)}, {"$set": {"approval_engine_status": "generation_failed"}})


@router.post("/generate", response_model=ApprovalEngineResponse)
async def generate(payload: GenerateApprovalsRequest, user: dict = Depends(require_business_user)):
    business_id = _owned_business_id(user, payload.business_id)
    try:
        return await generate_for_business(business_id, force=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Business profile not found") from exc
    except Exception as exc:
        logger.exception("Approval generation failed")
        raise HTTPException(status_code=503, detail="Approval evaluation is temporarily unavailable") from exc


@router.get("/{business_id}", response_model=ApprovalEngineResponse)
async def get_approvals(business_id: str, user: dict = Depends(require_business_user)):
    owned_id = _owned_business_id(user, business_id)
    return await current_approvals(owned_id)


@router.post("/{business_id}/refresh", response_model=ApprovalEngineResponse)
async def refresh_approvals(business_id: str, user: dict = Depends(require_business_user)):
    owned_id = _owned_business_id(user, business_id)
    try:
        return await generate_for_business(owned_id, force=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Business profile not found") from exc
    except Exception as exc:
        logger.exception("Approval refresh failed")
        raise HTTPException(status_code=503, detail="Approval evaluation is temporarily unavailable") from exc


@router.get("/{business_id}/approvals/{approval_id}")
async def get_approval_details(business_id: str, approval_id: str, user: dict = Depends(require_business_user)):
    owned_id = _owned_business_id(user, business_id)
    if not ObjectId.is_valid(approval_id):
        raise HTTPException(status_code=404, detail="Approval not found")
    business = await get_database().businesses.find_one({"_id": owned_id}, {"approval_generation_id": 1})
    if not business or not business.get("approval_generation_id"):
        raise HTTPException(status_code=404, detail="Approval not found")
    approval = await get_database().approval_results.find_one({
        "_id": ObjectId(approval_id), "business_id": owned_id,
        "generation_id": business["approval_generation_id"],
    })
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    from app.services.approval_engine.approval_engine_service import _serialize
    return _serialize(approval)


@admin_router.get("/{business_id}/trace")
async def approval_trace(business_id: str, admin: dict = Depends(require_admin)):
    if not ObjectId.is_valid(business_id):
        raise HTTPException(status_code=404, detail="Business profile not found")
    db = get_database(); oid = ObjectId(business_id)
    business = await db.businesses.find_one({"_id": oid}, {"approval_generation_id": 1})
    if not business or not business.get("approval_generation_id"):
        raise HTTPException(status_code=404, detail="Approval trace not found")
    run = await db.approval_engine_runs.find_one({"_id": business["approval_generation_id"], "business_id": oid})
    if not run:
        raise HTTPException(status_code=404, detail="Approval trace not found")
    from app.services.approval_engine.approval_engine_service import _serialize
    return _serialize(run, id_field="generation_id")
