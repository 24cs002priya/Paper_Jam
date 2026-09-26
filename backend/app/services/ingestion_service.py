from datetime import datetime

from bson import ObjectId

from app.core.config import get_settings
from app.db.mongodb import get_database
from app.models.common import utc_now
from app.services.chunking_service import chunk_pages
from app.services.embedding_service import embedding_service
from app.services.pdf_service import extract_pdf
from app.services.storage_service import storage


async def ingest_version(regulation: dict, *, version: str, filename: str, content: bytes, uploaded_by: ObjectId, effective_date: datetime | None = None, published_date: datetime | None = None) -> dict:
    if len(content) > get_settings().max_upload_bytes:
        raise ValueError("PDF exceeds the configured upload limit")
    key = await storage.save(filename, content)
    db = get_database()
    record = {
        "regulation_id": regulation["_id"], "version": version, "file_name": filename,
        "storage_key": key, "file_size": len(content), "mime_type": "application/pdf",
        "status": "processing", "processing_error": None, "page_count": 0,
        "chunk_count": 0, "effective_date": effective_date, "published_date": published_date,
        "uploaded_by": uploaded_by, "uploaded_at": utc_now(), "processed_at": None,
    }
    try:
        result = await db.regulation_versions.insert_one(record)
    except Exception:
        await storage.delete(key)
        raise
    version_id = result.inserted_id
    try:
        pages = await extract_pdf(content)
        if not pages:
            raise ValueError("PDF contains no pages")
        chunks = chunk_pages(pages)
        if not chunks:
            raise ValueError("PDF contains no extractable text")
        vectors = await embedding_service.embed_documents([chunk.text for chunk in chunks])
        if len(vectors) != len(chunks) or any(len(vector) != embedding_service.dimension for vector in vectors):
            raise RuntimeError("Embedding model returned an unexpected vector shape")
        metadata = {
            "department": regulation.get("department_name"),
            "business_types": regulation.get("business_types", []),
            "jurisdiction": regulation.get("jurisdiction"),
            "document_type": regulation.get("document_type"),
            "issuing_authority": regulation.get("issuing_authority"),
            "active": False,
        }
        docs = [{
            "regulation_id": regulation["_id"], "version_id": version_id,
            "chunk_index": index, "text": chunk.text, "page_start": chunk.page_start,
            "page_end": chunk.page_end, "section": chunk.section, "subsection": chunk.subsection,
            "metadata": metadata, "embedding": vector, "created_at": utc_now(),
        } for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))]
        await db.regulation_chunks.insert_many(docs, ordered=True)
        await db.regulation_versions.update_one({"_id": version_id}, {"$set": {
            "status": "ready", "page_count": len(pages), "chunk_count": len(docs), "processed_at": utc_now(),
        }})
    except Exception as exc:
        await db.regulation_chunks.delete_many({"version_id": version_id})
        await db.regulation_versions.update_one({"_id": version_id}, {"$set": {
            "status": "failed", "processing_error": str(exc)[:1000], "processed_at": utc_now(),
        }})
        raise
    return await db.regulation_versions.find_one({"_id": version_id})
