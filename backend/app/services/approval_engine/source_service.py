from typing import Any


def source_from_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata = chunk.get("metadata") or {}
    effective = chunk.get("effective_date")
    return {
        "document_id": str(chunk.get("version_id")) if chunk.get("version_id") else None,
        "document_title": chunk.get("file_name") or chunk.get("regulation_name"),
        "regulation_name": chunk.get("regulation_name"),
        "section": chunk.get("section") or chunk.get("subsection"),
        "authority": chunk.get("authority") or metadata.get("issuing_authority"),
        "source_url": chunk.get("source_url"),
        "jurisdiction": chunk.get("jurisdiction") or metadata.get("jurisdiction"),
        "state": metadata.get("state"),
        "domain": chunk.get("document_type") or metadata.get("document_type"),
        "effective_date": effective,
        "page_number": chunk.get("page_start"),
        "chunk_id": str(chunk.get("_id")),
    }


def compact_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {"chunk_id": str(chunk.get("_id")), "text": chunk.get("text", ""),
            "score": chunk.get("score"), "source": source_from_chunk(chunk)}
