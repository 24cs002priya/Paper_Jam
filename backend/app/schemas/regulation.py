from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class RegulationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    slug: str = Field(min_length=1, max_length=300, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = ""
    department_id: str | None = None
    department_name: str | None = None
    business_types: list[str] = Field(default_factory=list)
    jurisdiction: str | None = None
    issuing_authority: str | None = None
    source_url: HttpUrl | None = None
    document_type: str = "regulation"


class RegulationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    department_id: str | None = None
    department_name: str | None = None
    business_types: list[str] | None = None
    jurisdiction: str | None = None
    issuing_authority: str | None = None
    source_url: HttpUrl | None = None
    document_type: str | None = None


class RegulationSummary(BaseModel):
    id: str
    name: str
    slug: str
    description: str = ""
    department_id: str | None = None
    department_name: str | None = None
    business_types: list[str] = Field(default_factory=list)
    jurisdiction: str | None = None
    issuing_authority: str | None = None
    source_url: str | None = None
    document_type: str
    status: str
    active_version_id: str | None = None
    created_at: datetime
    updated_at: datetime
    active_version: dict[str, Any] | None = None
