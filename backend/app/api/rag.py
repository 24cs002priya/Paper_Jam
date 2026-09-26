from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_admin
from app.schemas.rag import RAGRequest
from app.services.rag_service import answer_question
from app.services.vector_search_service import search_chunks

router = APIRouter(prefix="/rag", tags=["Regulatory RAG"])


def public_result(item: dict) -> dict:
    return {
        "chunk_id": str(item["_id"]), "regulation_id": str(item["regulation_id"]),
        "regulation_name": item["regulation_name"], "version": item["version"],
        "text": item["text"], "score": item["score"],
        "page_start": item.get("page_start"), "page_end": item.get("page_end"),
        "section": item.get("section"),
        "source": {"file_name": item["file_name"], "source_url": item.get("source_url")},
    }


@router.post("/search", summary="Retrieve regulatory evidence without an LLM call")
async def search(payload: RAGRequest) -> dict:
    try:
        items = await search_chunks(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Regulatory vector search is unavailable; verify the Atlas vector index") from exc
    return {"query": payload.query, "results": [public_result(item) for item in items]}


@router.post("/answer", summary="Explain a regulatory requirement with citations")
async def answer(payload: RAGRequest, admin: dict = Depends(require_admin)) -> dict:
    try:
        return await answer_question(payload, admin)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Regulatory answer generation is unavailable; verify embedding, Atlas, and Groq configuration") from exc
