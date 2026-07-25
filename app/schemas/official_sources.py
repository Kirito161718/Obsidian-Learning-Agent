from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OfficialSourceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    source_type: Literal["auto", "html", "markdown", "text", "pdf"] = "auto"
    content_selector: str | None = Field(default=None, max_length=500)


class OfficialSourcePatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    content_selector: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class OfficialSourceReminderPatch(StrictModel):
    reminder_enabled: bool
    check_interval_days: int = Field(ge=1, le=365)


class OfficialSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    url: str
    canonical_url: str
    allowed_host: str
    source_type: str
    content_selector: str | None
    enabled: bool
    status: str
    reminder_enabled: bool
    check_interval_days: int
    last_checked_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None
    last_error_preview: str | None
    created_at: datetime
    updated_at: datetime


class NoteLinkCreate(StrictModel):
    obsidian_note_id: str = Field(min_length=1, max_length=36)
    target_heading_paths: list[list[str]] = Field(default_factory=list, max_length=20)


class NoteLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    obsidian_note_id: str
    target_heading_paths: list[list[str]]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    content_hash: str
    normalized_format: str
    normalizer_version: str
    http_status: int
    content_type: str
    size_bytes: int
    etag: str | None
    last_modified: str | None
    section_manifest: list[dict]
    created_at: datetime


class CleanupRequest(StrictModel):
    dry_run: bool = True


ReminderStatus = Literal[
    "disabled",
    "never_checked",
    "current",
    "due",
    "overdue",
    "error",
]


class OfficialSourceReminderOut(BaseModel):
    id: str
    name: str
    reminder_status: ReminderStatus
    check_interval_days: int
    last_checked_at: datetime | None
    next_suggested_check_at: datetime | None
    last_error_code: str | None


class OfficialReminderSummaryOut(BaseModel):
    total_sources: int
    due_count: int
    overdue_count: int
    never_checked_count: int
    error_count: int
    pending_proposal_count: int
    batch_limit: int
    sources: list[OfficialSourceReminderOut]
