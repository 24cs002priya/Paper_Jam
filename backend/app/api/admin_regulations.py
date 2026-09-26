import asyncio
from datetime import date, datetime, time, timezone
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pymongo.errors import DuplicateKeyError

from app.core.security import require_admin
from app.db.mongodb import get_database
from app.models.common import utc_now
from app.schemas.regulation import RegulationCreate, RegulationUpdate
from app.services.ingestion_service import ingest_version
from app.services.storage_service import storage

router = APIRouter(prefix="/admin/regulations", tags=["Admin regulations"], dependencies=[Depends(require_admin)])


def oid(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=404, detail="Regulation or version not found")
    return ObjectId(value)


def serialize(value: dict | None) -> dict | None:
    if value is None:
        return None
    def convert(item):
        if isinstance(item, ObjectId):
            return str(item)
        if isinstance(item, list):
            return [convert(part) for part in item]
        if isinstance(item, dict):
            return {("id" if key == "_id" else key): convert(val) for key, val in item.items() if key not in {"embedding", "storage_key"}}
        return item
    return convert(value)


async def upload_pdf(file: UploadFile) -> bytes:
    if file.content_type != "application/pdf" or not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload must be a PDF file")
    data = await file.read()
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF")
    return data


@router.get("")
async def list_regulations() -> dict:
    db = get_database()
    records = await db.regulations.find({}).sort("updated_at", -1).to_list(length=500)
    for regulation in records:
        active_id = regulation.get("active_version_id")
        regulation["active_version"] = await db.regulation_versions.find_one({"_id": active_id}) if active_id else None
    return {"items": [serialize(item) for item in records], "total": len(records)}


@router.get("/metrics")
async def metrics() -> dict:
    db = get_database()
    active, versions, pending = await asyncio.gather(
        db.regulations.count_documents({"status": "active"}),
        db.regulation_versions.count_documents({}),
        db.rag_queries.count_documents({"answer_generated": False}),
    )
    return {"active_regulations": active, "regulation_versions": versions, "pending_rag_queries": pending}


@router.get("/{regulation_id}")
async def get_regulation(regulation_id: str) -> dict:
    db = get_database()
    regulation = await db.regulations.find_one({"_id": oid(regulation_id)})
    if not regulation:
        raise HTTPException(status_code=404, detail="Regulation not found")
    regulation["active_version"] = await db.regulation_versions.find_one({"_id": regulation.get("active_version_id")}) if regulation.get("active_version_id") else None
    return serialize(regulation)


@router.post("", status_code=201)
async def create_regulation(payload: RegulationCreate, admin: dict = Depends(require_admin)) -> dict:
    now = utc_now()
    doc = payload.model_dump(mode="json")
    doc.update({"source_url": str(payload.source_url) if payload.source_url else None, "active_version_id": None, "status": "draft", "created_at": now, "updated_at": now, "created_by": admin["_id"]})
    try:
        result = await get_database().regulations.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A regulation with this slug already exists") from exc
    doc["_id"] = result.inserted_id
    return serialize(doc)


@router.patch("/{regulation_id}")
async def update_regulation(regulation_id: str, payload: RegulationUpdate) -> dict:
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if "source_url" in changes and payload.source_url:
        changes["source_url"] = str(payload.source_url)
    if changes:
        changes["updated_at"] = utc_now()
        result = await get_database().regulations.update_one({"_id": oid(regulation_id)}, {"$set": changes})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Regulation not found")
        metadata_fields = {
            "department_name": "metadata.department",
            "business_types": "metadata.business_types",
            "jurisdiction": "metadata.jurisdiction",
            "document_type": "metadata.document_type",
            "issuing_authority": "metadata.issuing_authority",
        }
        chunk_updates = {metadata_fields[key]: value for key, value in changes.items() if key in metadata_fields}
        if chunk_updates:
            await get_database().regulation_chunks.update_many({"regulation_id": oid(regulation_id)}, {"$set": chunk_updates})
    return await get_regulation(regulation_id)


@router.post("/{regulation_id}/versions", status_code=201)
async def create_version(regulation_id: str, file: Annotated[UploadFile, File()], version: Annotated[str, Form()], effective_date: Annotated[date | None, Form()] = None, published_date: Annotated[date | None, Form()] = None, admin: dict = Depends(require_admin)) -> dict:
    db = get_database()
    rid = oid(regulation_id)
    regulation = await db.regulations.find_one({"_id": rid})
    if not regulation:
        raise HTTPException(status_code=404, detail="Regulation not found")
    data = await upload_pdf(file)
    try:
        result = await ingest_version(regulation, version=version, filename=file.filename or "document.pdf", content=data, uploaded_by=admin["_id"], effective_date=datetime.combine(effective_date, time.min, timezone.utc) if effective_date else None, published_date=datetime.combine(published_date, time.min, timezone.utc) if published_date else None)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="This version already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # The failed version record remains available to inspect and reprocess.
        raise HTTPException(status_code=500, detail="PDF processing failed; check the version status for processing details") from exc
    await db.regulations.update_one({"_id": rid}, {"$set": {"updated_at": utc_now()}})
    return serialize(result) or {}


@router.get("/{regulation_id}/versions")
async def list_versions(regulation_id: str) -> dict:
    db = get_database()
    rid = oid(regulation_id)
    if not await db.regulations.find_one({"_id": rid}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Regulation not found")
    records = await db.regulation_versions.find({"regulation_id": rid}).sort("uploaded_at", -1).to_list(length=500)
    return {"items": [serialize(item) for item in records], "total": len(records)}


async def activate(rid: ObjectId, vid: ObjectId) -> dict:
    db = get_database()
    regulation = await db.regulations.find_one({"_id": rid})
    version = await db.regulation_versions.find_one({"_id": vid, "regulation_id": rid})
    if not regulation or not version:
        raise HTTPException(status_code=404, detail="Regulation or version not found")
    if version["status"] != "ready":
        raise HTTPException(status_code=409, detail="Only a successfully processed version can be activated")
    previous = regulation.get("active_version_id")
    if previous and previous != vid:
        await db.regulation_versions.update_one({"_id": previous}, {"$set": {"status": "archived"}})
        await db.regulation_chunks.update_many({"version_id": previous}, {"$set": {"metadata.active": False}})
    await db.regulation_versions.update_one({"_id": vid}, {"$set": {"status": "ready"}})
    await db.regulation_chunks.update_many({"version_id": vid}, {"$set": {"metadata.active": True}})
    await db.regulations.update_one({"_id": rid}, {"$set": {"active_version_id": vid, "updated_at": utc_now(), "status": "active"}})
    return {"active_version_id": str(vid)}


@router.post("/{regulation_id}/versions/{version_id}/activate")
async def activate_version(regulation_id: str, version_id: str) -> dict:
    return await activate(oid(regulation_id), oid(version_id))


@router.post("/{regulation_id}/versions/{version_id}/reprocess", status_code=202)
async def reprocess_version(regulation_id: str, version_id: str, admin: dict = Depends(require_admin)) -> dict:
    db = get_database()
    rid, vid = oid(regulation_id), oid(version_id)
    regulation = await db.regulations.find_one({"_id": rid})
    version = await db.regulation_versions.find_one({"_id": vid, "regulation_id": rid})
    if not regulation or not version:
        raise HTTPException(status_code=404, detail="Regulation or version not found")
    content = await storage.read(version["storage_key"])
    await db.regulation_versions.update_one({"_id": vid}, {"$set": {"status": "processing", "processing_error": None}})
    try:
        from app.services.chunking_service import chunk_pages
        from app.services.embedding_service import embedding_service
        from app.services.pdf_service import extract_pdf

        pages = await extract_pdf(content)
        chunks = chunk_pages(pages)
        if not chunks:
            raise ValueError("PDF contains no extractable text")
        vectors = await embedding_service.embed_documents([chunk.text for chunk in chunks])
        metadata = {
            "department": regulation.get("department_name"),
            "business_types": regulation.get("business_types", []),
            "jurisdiction": regulation.get("jurisdiction"),
            "document_type": regulation.get("document_type"),
            "issuing_authority": regulation.get("issuing_authority"),
            "active": regulation.get("active_version_id") == vid,
        }
        new_chunks = [{
            "regulation_id": rid, "version_id": vid, "chunk_index": index,
            "text": chunk.text, "page_start": chunk.page_start, "page_end": chunk.page_end,
            "section": chunk.section, "subsection": chunk.subsection, "metadata": metadata,
            "embedding": vector, "created_at": utc_now(),
        } for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))]
        old_ids = await db.regulation_chunks.distinct("_id", {"version_id": vid})
        inserted = await db.regulation_chunks.insert_many(new_chunks)
        if old_ids:
            await db.regulation_chunks.delete_many({"_id": {"$in": old_ids}})
        await db.regulation_versions.update_one({"_id": vid}, {"$set": {
            "status": "ready", "processing_error": None, "page_count": len(pages),
            "chunk_count": len(chunks), "processed_at": utc_now(),
        }})
    except Exception as exc:
        # Keep the previous chunk set if reprocessing fails before replacement.
        await db.regulation_versions.update_one({"_id": vid}, {"$set": {"status": "failed", "processing_error": str(exc)[:1000], "processed_at": utc_now()}})
        raise HTTPException(status_code=500, detail="PDF reprocessing failed; check the version status") from exc
    return {"status": "ready", "version_id": version_id}


@router.get("/{regulation_id}/versions/{version_id}/status")
async def version_status(regulation_id: str, version_id: str) -> dict:
    item = await get_database().regulation_versions.find_one({"_id": oid(version_id), "regulation_id": oid(regulation_id)})
    if not item:
        raise HTTPException(status_code=404, detail="Version not found")
    return {"version_id": version_id, "status": item["status"], "processing_error": item.get("processing_error"), "page_count": item.get("page_count"), "chunk_count": item.get("chunk_count")}
