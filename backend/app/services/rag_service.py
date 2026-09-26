import json

from groq import AsyncGroq

from app.core.config import get_settings
from app.db.mongodb import get_database
from app.models.common import utc_now
from app.prompts.regulatory_rag import SYSTEM_PROMPT
from app.schemas.rag import RAGRequest
from app.services.vector_search_service import search_chunks


def _source(chunk: dict) -> dict:
    return {
        "regulation_name": chunk["regulation_name"],
        "version": chunk["version"],
        "section": chunk.get("section"),
        "page": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "file_name": chunk["file_name"],
        "chunk_id": str(chunk["_id"]),
        "source_url": chunk.get("source_url"),
    }


async def answer_question(request: RAGRequest, admin: dict) -> dict:
    chunks = await search_chunks(request)
    sources = [_source(item) for item in chunks]
    context = "\n\n".join(
        f"[Source {index}] Regulation: {item['regulation_name']}; version: {item['version']}; section: {item.get('section') or 'Not identified'}; page: {item.get('page_start')}\n{item['text']}"
        for index, item in enumerate(chunks, start=1)
    )
    settings = get_settings()
    if not settings.groq_api_key.get_secret_value():
        raise RuntimeError("GROQ_API_KEY is not configured")
    client = AsyncGroq(api_key=settings.groq_api_key.get_secret_value())
    completion = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"CONTEXT:\n{context or '[No regulatory context was retrieved.]'}\n\nQUESTION:\n{request.query}"}],
        temperature=0.1,
    )
    answer = completion.choices[0].message.content or ""
    await get_database().rag_queries.insert_one({
        "query": request.query,
        "filters": request.model_dump(exclude={"query", "top_k"}, exclude_none=True),
        "retrieved_chunk_ids": [item["_id"] for item in chunks],
        "answer_generated": True,
        "created_by": admin["_id"],
        "created_at": utc_now(),
    })
    return {"answer": answer, "sources": sources}
