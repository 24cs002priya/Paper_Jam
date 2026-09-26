from bson import ObjectId

from app.core.config import get_settings
from app.db.mongodb import get_database
from app.schemas.rag import RAGRequest
from app.services.embedding_service import embedding_service


def _oid(value: str | None) -> ObjectId | None:
    if value is None:
        return None
    if not ObjectId.is_valid(value):
        raise ValueError("Invalid MongoDB identifier")
    return ObjectId(value)


async def search_chunks(request: RAGRequest) -> list[dict]:
    vector = await embedding_service.embed_text(request.query)
    filters: dict = {}
    clauses: list[dict] = []
    if not request.include_archived:
        clauses.append({"metadata.active": True})
    mapping = {
        "business_type": "metadata.business_types",
        "department": "metadata.department",
        "jurisdiction": "metadata.jurisdiction",
        "document_type": "metadata.document_type",
        "issuing_authority": "metadata.issuing_authority",
    }
    for field, path in mapping.items():
        value = getattr(request, field)
        if value:
            clauses.append({path: value})
    for field, path in (("regulation_id", "regulation_id"), ("version_id", "version_id")):
        value = _oid(getattr(request, field))
        if value:
            clauses.append({path: value})
    if clauses:
        filters = {"$and": clauses} if len(clauses) > 1 else clauses[0]
    k = request.top_k or get_settings().rag_top_k
    pipeline = [
        {"$vectorSearch": {
            "index": get_settings().vector_index_name,
            "path": "embedding",
            "queryVector": vector,
            "numCandidates": max(k * 20, 100),
            "limit": k,
            **({"filter": filters} if filters else {}),
        }},
        {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
        {"$lookup": {"from": "regulations", "localField": "regulation_id", "foreignField": "_id", "as": "regulation"}},
        {"$unwind": {"path": "$regulation", "preserveNullAndEmptyArrays": False}},
        {"$lookup": {"from": "regulation_versions", "localField": "version_id", "foreignField": "_id", "as": "version_doc"}},
        {"$unwind": {"path": "$version_doc", "preserveNullAndEmptyArrays": False}},
        {"$project": {
            "_id": 1, "regulation_id": 1, "version_id": 1, "text": 1,
            "page_start": 1, "page_end": 1, "section": 1, "score": 1,
            "subsection": 1, "chunk_index": 1, "metadata": 1,
            "regulation_name": "$regulation.name", "source_url": "$regulation.source_url",
            "authority": "$regulation.issuing_authority",
            "department_name": "$regulation.department_name",
            "jurisdiction": "$regulation.jurisdiction",
            "document_type": "$regulation.document_type",
            "version": "$version_doc.version", "file_name": "$version_doc.file_name",
            "effective_date": "$version_doc.effective_date",
            "published_date": "$version_doc.published_date",
        }},
    ]
    return await get_database().regulation_chunks.aggregate(pipeline).to_list(length=k)
