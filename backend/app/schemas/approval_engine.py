from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ConditionOperator = Literal[
    "equals", "not_equals", "contains", "not_contains", "in", "not_in",
    "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal",
    "exists", "not_exists",
]
ConditionValue = str | float | bool | list[str | float | bool] | None


class ConditionNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["condition", "and", "or", "not"] = "condition"
    field: str | None = None
    operator: ConditionOperator | None = None
    value: ConditionValue = None
    children: list["ConditionNode"] = Field(default_factory=list)
    evidence: str | None = Field(default=None, max_length=1000)


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_name: str = Field(min_length=1, max_length=300)
    requirement_type: Literal["mandatory", "conditional", "information"]
    source_chunk_id: str
    evidence: str = Field(min_length=8, max_length=1000)


class ExtractedDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_name: str = Field(min_length=1, max_length=300)
    source_chunk_id: str
    evidence: str = Field(min_length=8, max_length=1000)


class ExtractedApprovalRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_name: str = Field(min_length=2, max_length=300)
    authority: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=120)
    rule_type: Literal["mandatory", "conditional"]
    reason: str = Field(min_length=8, max_length=1500)
    conditions: list[ConditionNode] = Field(default_factory=list, max_length=20)
    required_documents: list[ExtractedDocument] = Field(default_factory=list, max_length=50)
    dependencies: list[ExtractedDependency] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)
    source_chunk_ids: list[str] = Field(min_length=1, max_length=10)
    evidence: str = Field(min_length=8, max_length=1500)

    @field_validator("approval_name", "reason", "evidence")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Field cannot be blank")
        return value


class RuleExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[ExtractedApprovalRule] = Field(default_factory=list, max_length=100)
    missing_information: list[str] = Field(default_factory=list, max_length=100)


class GenerateApprovalsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_id: str = Field(min_length=24, max_length=24, pattern=r"^[0-9a-fA-F]{24}$")


class ConditionResult(BaseModel):
    condition: str
    result: bool | None
    evidence: str | None = None
    field: str | None = None


class ApprovalSource(BaseModel):
    document_id: str | None = None
    document_title: str | None = None
    regulation_name: str | None = None
    section: str | None = None
    authority: str | None = None
    source_url: str | None = None
    jurisdiction: str | None = None
    state: str | None = None
    domain: str | None = None
    effective_date: Any = None
    page_number: int | None = None
    chunk_id: str


class ApprovalDocumentRequirement(BaseModel):
    document_name: str
    requirement_type: Literal["mandatory", "conditional", "information"]
    source: ApprovalSource
    evidence: str


class ApprovalResult(BaseModel):
    approval_id: str
    business_id: str
    generation_id: str
    approval_name: str
    authority: str | None = None
    category: str | None = None
    status: Literal["mandatory", "conditional", "not_applicable", "needs_information"]
    confidence: float
    reason: str
    conditions: list[ConditionResult] = Field(default_factory=list)
    required_documents: list[ApprovalDocumentRequirement] = Field(default_factory=list)
    regulation_sources: list[ApprovalSource] = Field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    created_at: Any
    updated_at: Any


class ApprovalEngineResponse(BaseModel):
    business_id: str
    generation_id: str | None = None
    engine_status: str
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    roadmap: dict[str, Any] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    retrieved_chunk_count: int = 0
    generated_at: Any = None


class ApprovalRoadmap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["generated", "empty", "not_generated"]
    ordering_basis: Literal["explicit_regulatory_dependencies", "status_groups_only"] | None = None
    groups: dict[str, list[str]] = Field(default_factory=dict)
    dependency_edges: list[dict[str, str]] = Field(default_factory=list)
    approval_names: dict[str, str] = Field(default_factory=dict)


class ApprovalReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str = Field(min_length=24, max_length=24, pattern=r"^[0-9a-fA-F]{24}$")
