from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DatasetSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    version: str
    content_hash: str
    case_count: int
    stale_case_count: int
    safe_case_count: int
    status: str
    created_at: datetime
    updated_at: datetime


class DatasetValidationOut(BaseModel):
    dataset: DatasetSummaryOut
    valid: bool
    dataset_hash: str
    case_count: int
    stale_case_count: int
    safe_case_count: int
    category_counts: dict[str, int]
    issues: list[dict[str, Any]]


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    profile_key: str
    version: str
    description: str | None
    configuration: dict[str, Any]
    configuration_hash: str
    collection_name: str | None
    is_active: bool
    is_baseline: bool
    evaluation_status: str
    promotion_eligible: bool
    manual_exception: bool
    rollback_profile_id: str | None
    created_at: datetime
    activated_at: datetime | None


class ProfileActivationRequest(BaseModel):
    confirm: bool
    expected_profile_version: str = Field(min_length=1, max_length=80)


class ExperimentCreateRequest(BaseModel):
    dataset_id: str
    profile_id: str
    name: str = Field(min_length=1, max_length=200)
    variant_key: str = Field(min_length=1, max_length=120)
    experiment_type: Literal["retrieval", "full"] = "retrieval"
    categories: list[str] = Field(default_factory=list, max_length=20)
    case_ids: list[str] = Field(default_factory=list, max_length=100)
    repeat_count: int = Field(default=1, ge=1, le=20)
    seed: int = 7
    publish_langsmith: bool = True
    run_now: bool = True

    @field_validator("categories", "case_ids")
    @classmethod
    def dedupe_values(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    dataset_id: str
    profile_id: str | None
    name: str
    variant_key: str
    experiment_type: str
    retrieval_profile_version: str
    status: str
    configuration_hash: str
    metric_summary: dict[str, Any]
    failure_summary: dict[str, Any]
    langsmith_experiment_name: str | None
    langsmith_experiment_id: str | None
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    created_at: datetime


class CompareRequest(BaseModel):
    experiment_ids: list[str] = Field(min_length=2, max_length=6)

    @field_validator("experiment_ids")
    @classmethod
    def unique_experiments(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("experiment_ids must be unique")
        return value


class CleanupRequest(BaseModel):
    dry_run: bool = True


class SafetyCalibrationRequest(BaseModel):
    dataset_id: str
    baseline_experiment_id: str


class SafetyValidationRequest(BaseModel):
    dataset_id: str
    baseline_experiment_id: str
    candidate_experiment_id: str
