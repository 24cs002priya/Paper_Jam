import asyncio
import os
import uuid
from pathlib import Path

from app.core.config import get_settings


class FileStorage:
    """Local development storage; interface can be replaced by object storage."""

    async def save(self, filename: str, content: bytes) -> str:
        settings = get_settings()
        if settings.storage_backend != "local":
            raise RuntimeError(f"Unsupported storage backend: {settings.storage_backend}")
        suffix = Path(filename).suffix.lower() or ".pdf"
        key = f"{uuid.uuid4().hex}{suffix}"
        path = Path(settings.local_storage_path) / key
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, content)
        return key

    async def read(self, key: str) -> bytes:
        path = (Path(get_settings().local_storage_path) / key).resolve()
        root = Path(get_settings().local_storage_path).resolve()
        if root not in path.parents:
            raise ValueError("Invalid storage key")
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = Path(get_settings().local_storage_path) / key
        if path.exists():
            await asyncio.to_thread(os.remove, path)


storage = FileStorage()
