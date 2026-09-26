from pydantic import BaseModel, Field


class RAGRequest(BaseModel):
    query: str = Field(min_length=3, max_length=4000)
    business_type: str | None = None
    department: str | None = None
    jurisdiction: str | None = None
    regulation_id: str | None = None
    version_id: str | None = None
    document_type: str | None = None
    issuing_authority: str | None = None
    include_archived: bool = False
    top_k: int | None = Field(default=None, ge=1, le=50)
