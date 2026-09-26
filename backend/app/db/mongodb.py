from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_settings

_client: AsyncIOMotorClient | None = None


def connect() -> None:
    global _client
    uri = get_settings().mongodb_uri
    if uri:
        _client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)


def close() -> None:
    global _client
    if _client:
        _client.close()
        _client = None


def get_database() -> AsyncIOMotorDatabase:
    if _client is None:
        raise RuntimeError("MongoDB is not configured")
    return _client[get_settings().mongodb_database]


async def ping() -> bool:
    if _client is None:
        return False
    try:
        await _client.admin.command("ping")
        return True
    except Exception:
        return False
