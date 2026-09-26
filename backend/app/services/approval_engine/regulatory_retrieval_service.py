from app.core.config import get_settings
from app.schemas.rag import RAGRequest
from app.services.approval_engine.context_builder import build_regulatory_queries
from app.services.vector_search_service import search_chunks


async def retrieve_regulatory_chunks(context: dict) -> tuple[str, list[dict]]:
    queries = build_regulatory_queries(context)
    settings = get_settings()
    broad_top_k = min(max(settings.rag_top_k, 12), 15)
    focused_top_k = 30
    query_results = []
    query_limits = []
    for index, query in enumerate(queries):
        # Apply only the global active-regulation filter inside search_chunks.
        # Business-type/jurisdiction filters hid cross-cutting environmental,
        # packaging and premises rules when one food-specific hit existed.
        query_top_k = broad_top_k if index < 2 else focused_top_k
        matches = await search_chunks(RAGRequest(query=query, top_k=query_top_k))
        query_limits.append(query_top_k)
        # A draft version is not an operative approval source, even if it is
        # mistakenly marked active in the regulation catalog.
        query_results.append([chunk for chunk in matches
                              if "draft" not in str(chunk.get("regulation_name", "")).casefold()])
    # Preserve hits from each topic search. Global score sorting had allowed
    # food-license passages to crowd out the separate pollution, premises and
    # packaged-goods clauses even though those searches returned them.
    selected = []
    seen: set[str] = set()
    for rank in range(max(query_limits, default=0)):
        for matches in query_results:
            if rank >= len(matches):
                continue
            chunk = matches[rank]
            key = str(chunk.get("_id"))
            if key not in seen:
                seen.add(key)
                selected.append(chunk)
    return "\n".join(queries), selected
