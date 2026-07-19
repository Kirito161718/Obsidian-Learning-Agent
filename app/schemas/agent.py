from datetime import datetime
from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PreferredLanguage(str, Enum):
    auto = "auto"
    zh_cn = "zh-CN"
    en = "en"


class AnswerDepth(str, Enum):
    concise = "concise"
    standard = "standard"
    detailed = "detailed"


class AnswerStyle(str, Enum):
    structured = "structured"
    plain = "plain"


class CitationMode(str, Enum):
    required = "required"
    when_available = "when_available"


class UserPreferenceUpdate(BaseModel):
    preferred_language: PreferredLanguage | None = None
    answer_depth: AnswerDepth | None = None
    answer_style: AnswerStyle | None = None
    citation_mode: CitationMode | None = None
    custom_instructions: str | None = None

    @field_validator("custom_instructions")
    @classmethod
    def normalize_custom_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def reject_null_enums(self) -> Self:
        for name in ("preferred_language", "answer_depth", "answer_style", "citation_mode"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        return self


class UserPreferenceOut(BaseModel):
    id: str = "default"
    preferred_language: PreferredLanguage = PreferredLanguage.zh_cn
    answer_depth: AnswerDepth = AnswerDepth.standard
    answer_style: AnswerStyle = AnswerStyle.structured
    citation_mode: CitationMode = CitationMode.required
    custom_instructions: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ContextPreviewRequest(BaseModel):
    session_id: str | None = None
    goal_id: str | None = None
    learning_status_id: str | None = None


class ContextPreviewResponse(BaseModel):
    user_preferences: dict[str, Any]
    explicit_learning_context: dict[str, Any]
    session_memory_turns: int
    section_chars: dict[str, int]
    truncated: dict[str, bool]
    context_manifest: dict[str, Any]


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_type: Literal["rag_ask", "rag_retrieve", "session_review"]
    session_id: str | None
    status: Literal["running", "succeeded", "failed", "cancelled"]
    question_hash: str | None
    question_preview: str | None
    mode: str | None
    source_types: list[str]
    source_path_prefixes: list[str]
    rewrite_applied: bool
    retrieved_count: int
    source_count: int
    model_call_count: int
    tool_call_count: int
    retry_count: int
    context_manifest: dict[str, Any]
    error_code: str | None
    error_message_preview: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    created_at: datetime


class AgentRunListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AgentRunOut]


class ToolExecutionRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=100)


class ToolMetadataOut(BaseModel):
    name: str
    description: str
    risk_level: Literal["READ_ONLY", "LOW_RISK", "HIGH_RISK"]
    enabled: bool
    idempotent: bool
    timeout_seconds: int
    allowed_callers: list[Literal["user", "system", "model"]]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ToolExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str | None
    tool_name: str
    risk_level: str | None
    caller_type: str
    decision: Literal["allowed", "denied", "approval_required"]
    status: Literal["pending", "running", "succeeded", "failed", "denied"]
    idempotency_key: str | None
    input_hash: str
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    error_code: str | None
    error_message_preview: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    created_at: datetime


class ToolExecutionResult(BaseModel):
    execution: ToolExecutionOut
    output: dict[str, Any] | None = None


class ToolExecutionListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ToolExecutionOut]


class CleanupHistoryRequest(BaseModel):
    dry_run: bool = True
    older_than_days: int = Field(default=10, ge=1)


class CleanupHistoryResponse(BaseModel):
    dry_run: bool
    older_than_days: int
    agent_runs: int
    tool_executions: int
    qa_history_links_cleared: int
