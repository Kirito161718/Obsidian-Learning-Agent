from datetime import datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.qa import QAHistorySourceChunk


DEFAULT_SESSION_TITLE = "新学习会话"


class LearningSessionStatus(str, Enum):
    active = "active"
    archived = "archived"


class LearningSourceType(str, Enum):
    uploaded_document = "uploaded_document"
    obsidian_note = "obsidian_note"


def normalize_source_prefixes(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        path = raw.replace("\\", "/").strip("/")
        first = path.split("/", 1)[0] if path else ""
        if not path or raw.startswith(("/", "\\")) or ":" in first or ".." in path.split("/"):
            raise ValueError("source_path_prefixes must be safe Vault-relative paths")
        if path not in normalized:
            normalized.append(path)
    return normalized


class LearningSessionCreate(BaseModel):
    title: str = Field(default=DEFAULT_SESSION_TITLE, min_length=1, max_length=200)
    goal_id: str | None = None
    learning_status_id: str | None = None
    source_types: list[LearningSourceType] = Field(
        default_factory=lambda: [LearningSourceType.obsidian_note, LearningSourceType.uploaded_document]
    )
    source_path_prefixes: list[str] = Field(default_factory=list)
    memory_enabled: bool = True

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("source_types")
    @classmethod
    def normalize_source_types(cls, value: list[LearningSourceType]) -> list[LearningSourceType]:
        if not value:
            raise ValueError("source_types must not be empty")
        return list(dict.fromkeys(value))

    @field_validator("source_path_prefixes")
    @classmethod
    def validate_prefixes(cls, value: list[str]) -> list[str]:
        return normalize_source_prefixes(value)

    @model_validator(mode="after")
    def prefixes_require_obsidian(self) -> Self:
        if self.source_path_prefixes and LearningSourceType.obsidian_note not in self.source_types:
            raise ValueError("source_path_prefixes require obsidian_note source type")
        return self


class LearningSessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    goal_id: str | None = None
    learning_status_id: str | None = None
    source_types: list[LearningSourceType] | None = None
    source_path_prefixes: list[str] | None = None
    memory_enabled: bool | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("source_types")
    @classmethod
    def normalize_source_types(
        cls, value: list[LearningSourceType] | None
    ) -> list[LearningSourceType] | None:
        if value is not None and not value:
            raise ValueError("source_types must not be empty")
        return list(dict.fromkeys(value)) if value is not None else None

    @field_validator("source_path_prefixes")
    @classmethod
    def validate_prefixes(cls, value: list[str] | None) -> list[str] | None:
        return normalize_source_prefixes(value) if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title must not be null")
        if "source_types" in self.model_fields_set and self.source_types is None:
            raise ValueError("source_types must not be null")
        if "source_path_prefixes" in self.model_fields_set and self.source_path_prefixes is None:
            raise ValueError("source_path_prefixes must not be null")
        if "memory_enabled" in self.model_fields_set and self.memory_enabled is None:
            raise ValueError("memory_enabled must not be null")
        if self.source_path_prefixes and self.source_types is not None:
            if LearningSourceType.obsidian_note not in self.source_types:
                raise ValueError("source_path_prefixes require obsidian_note source type")
        return self


class LearningSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    goal_id: str | None
    learning_status_id: str | None
    source_types: list[LearningSourceType]
    source_path_prefixes: list[str]
    memory_enabled: bool
    status: LearningSessionStatus
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime


class LearningSessionListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[LearningSessionOut]


class LearningSessionMessage(BaseModel):
    id: str
    question: str
    rewritten_query: str
    answer: str
    source_chunks: list[QAHistorySourceChunk]
    created_at: datetime


class LearningSessionMessagesResponse(BaseModel):
    session_id: str
    total: int
    items: list[LearningSessionMessage]


class LearningReviewContent(BaseModel):
    knowledge_summary: str = Field(min_length=1)
    covered_topics: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class LearningSessionReviewOut(LearningReviewContent):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    source_qa_ids: list[str]
    created_at: datetime
    updated_at: datetime
