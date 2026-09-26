import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin_regulations, approval_engine, auth, businesses, business_workspace, rag, users
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.indexes import ensure_indexes
from app.db.mongodb import close, connect, get_database, ping
from app.services.embedding_service import embedding_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("paper_jam")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    connect()
    if await ping():
        await ensure_indexes()
        if settings.admin_email and settings.admin_password.get_secret_value():
            admins = get_database().admins
            await admins.update_one(
                {"email": settings.admin_email},
                {"$setOnInsert": {
                    "email": settings.admin_email,
                    "hashed_password": hash_password(settings.admin_password.get_secret_value()),
                    "disabled": False,
                }},
                upsert=True,
            )
            # Migrate a pre-existing account that has no secure hash yet. Keep
            # an existing hash unchanged so restarts do not reset passwords.
            await admins.update_one(
                {"email": settings.admin_email, "$or": [
                    {"hashed_password": {"$exists": False}},
                    {"hashed_password": None},
                    {"hashed_password": ""},
                ]},
                {
                    "$set": {"hashed_password": hash_password(settings.admin_password.get_secret_value())},
                    "$unset": {"password": ""},
                },
            )
    else:
        logger.warning("MongoDB unavailable at startup; API health will be degraded")
    try:
        if settings.embedding_model:
            dimension = await __import__("asyncio").to_thread(lambda: embedding_service.dimension)
            logger.info("Configured embedding dimension: %s", dimension)
            logger.info("Atlas vector index definition: %s", atlas_index_definition(dimension))
    except Exception:
        logger.exception("Unable to initialize configured embedding model")
    yield
    close()


def atlas_index_definition(dimensions: int) -> dict:
    return {
        "name": settings.vector_index_name,
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {"type": "vector", "path": "embedding", "numDimensions": dimensions, "similarity": "cosine"},
                {"type": "filter", "path": "metadata.active"},
                {"type": "filter", "path": "metadata.department"},
                {"type": "filter", "path": "metadata.business_types"},
                {"type": "filter", "path": "metadata.jurisdiction"},
                {"type": "filter", "path": "metadata.document_type"},
                {"type": "filter", "path": "metadata.issuing_authority"},
                {"type": "filter", "path": "regulation_id"},
                {"type": "filter", "path": "version_id"},
            ]
        },
    }


app = FastAPI(title=settings.app_name, version="1.0.0", description="Dynamic regulatory document ingestion, retrieval, and grounded explanations.", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(users.admin_router, prefix=settings.api_prefix)
app.include_router(users.audit_router, prefix=settings.api_prefix)
app.include_router(businesses.router, prefix=settings.api_prefix)
app.include_router(business_workspace.router, prefix=settings.api_prefix)
app.include_router(business_workspace.admin_router, prefix=settings.api_prefix)
app.include_router(approval_engine.router, prefix=settings.api_prefix)
app.include_router(approval_engine.admin_router, prefix=settings.api_prefix)
app.include_router(admin_regulations.router, prefix=settings.api_prefix)
app.include_router(rag.router, prefix=settings.api_prefix)


@app.get(f"{settings.api_prefix}/health", tags=["Health"])
async def health() -> JSONResponse:
    connected = await ping()
    return JSONResponse(status_code=200 if connected else 503, content={"status": "ok" if connected else "degraded", "database": "connected" if connected else "unavailable"})
