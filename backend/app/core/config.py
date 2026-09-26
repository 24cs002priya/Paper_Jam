from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Paper Jam Regulatory API"
    environment: str = "development"
    api_prefix: str = "/api"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    mongodb_uri: str = ""
    mongodb_database: str = "paper_jam"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "openai/gpt-oss-20b"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rag_top_k: int = 5
    admin_email: str = ""
    admin_password: SecretStr = SecretStr("")
    jwt_secret: SecretStr = SecretStr("")
    jwt_expire_minutes: int = 60
    chunk_size: int = 1000
    chunk_overlap: int = 150
    max_upload_bytes: int = 52_428_800
    storage_backend: str = "local"
    local_storage_path: str = "./data/regulations"
    vector_index_name: str = "regulation_chunks_vector"
    embedding_batch_size: int = 32

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("jwt_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value() and len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
