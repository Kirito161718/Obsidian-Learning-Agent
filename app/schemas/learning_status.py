from datetime import datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.goal import normalize_string_list


class LearningStatusValue(str, Enum):
    not_started = "not_started"
    learning = "learning"
    practicing = "practicing"
    mastered = "mastered"
    paused = "paused"


class LearningStatusCreate(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    status: LearningStatusValue = LearningStatusValue.not_started
    mastery_level: int = Field(default=0, ge=0, le=5)
    manual_importance: int = Field(default=3, ge=1, le=5)
    current_problem: str | None = None
    related_goal_ids: list[str] = Field(default_factory=list)
    related_note_refs: list[str] = Field(default_factory=list)
    project_relevance: bool = False

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("topic must not be blank")
        return normalized

    @field_validator("current_problem")
    @classmethod
    def normalize_problem(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("related_goal_ids", "related_note_refs")
    @classmethod
    def normalize_references(cls, value: list[str]) -> list[str]:
        return normalize_string_list(value)


class LearningStatusUpdate(BaseModel):
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    status: LearningStatusValue | None = None
    mastery_level: int | None = Field(default=None, ge=0, le=5)
    manual_importance: int | None = Field(default=None, ge=1, le=5)
    current_problem: str | None = None
    related_goal_ids: list[str] | None = None
    related_note_refs: list[str] | None = None
    project_relevance: bool | None = None

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("topic must not be blank")
        return normalized

    @field_validator("current_problem")
    @classmethod
    def normalize_problem(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("related_goal_ids", "related_note_refs")
    @classmethod
    def normalize_references(cls, value: list[str] | None) -> list[str] | None:
        return normalize_string_list(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_non_nullable_fields(self) -> Self:
        for field_name in (
            "topic",
            "status",
            "mastery_level",
            "manual_importance",
            "related_goal_ids",
            "related_note_refs",
            "project_relevance",
        ):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} must not be null")
        return self


class ImportanceBreakdown(BaseModel):
    goal_relevance: float
    mastery_gap: float
    manual_importance: float
    project_relevance: float


class LearningStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    topic: str
    status: LearningStatusValue
    mastery_level: int
    manual_importance: int
    current_problem: str | None
    related_goal_ids: list[str]
    related_note_refs: list[str]
    project_relevance: bool
    importance_score: float
    importance_stars: int
    importance_breakdown: ImportanceBreakdown
    created_at: datetime
    updated_at: datetime


class LearningStatusListResponse(BaseModel):
    total: int
    items: list[LearningStatusOut]
