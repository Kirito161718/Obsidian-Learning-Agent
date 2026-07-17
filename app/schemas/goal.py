from datetime import datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GoalType(str, Enum):
    job = "job"
    learning = "learning"
    project = "project"
    skill = "skill"


class GoalStatus(str, Enum):
    active = "active"
    paused = "paused"
    completed = "completed"


def normalize_string_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if item and item not in seen:
            normalized.append(item)
            seen.add(item)
    return normalized


class LearningGoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    goal_type: GoalType
    description: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    status: GoalStatus = GoalStatus.active
    related_topics: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("related_topics")
    @classmethod
    def normalize_topics(cls, value: list[str]) -> list[str]:
        return normalize_string_list(value)


class LearningGoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    goal_type: GoalType | None = None
    description: str | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    status: GoalStatus | None = None
    related_topics: list[str] | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("related_topics")
    @classmethod
    def normalize_topics(cls, value: list[str] | None) -> list[str] | None:
        return normalize_string_list(value) if value is not None else None

    @model_validator(mode="after")
    def reject_null_non_nullable_fields(self) -> Self:
        for field_name in ("name", "goal_type", "priority", "status", "related_topics"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} must not be null")
        return self


class LearningGoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    goal_type: GoalType
    description: str | None
    priority: int
    status: GoalStatus
    related_topics: list[str]
    created_at: datetime
    updated_at: datetime


class LearningGoalListResponse(BaseModel):
    total: int
    items: list[LearningGoalOut]
