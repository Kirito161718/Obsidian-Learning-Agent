from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ObsidianIndexRequest(BaseModel):
    relative_paths: list[str] | None = None
    force: bool = False

    @field_validator("relative_paths")
    @classmethod
    def validate_relative_paths(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        for raw in value:
            path = raw.replace("\\", "/").strip("/")
            if (
                not path
                or raw.startswith(("/", "\\"))
                or ":" in path.split("/", 1)[0]
                or ".." in path.split("/")
            ):
                raise ValueError("relative_paths must contain safe Vault-relative paths")
            normalized.append(path)
        return list(dict.fromkeys(normalized))


class ObsidianStatusResponse(BaseModel):
    enabled: bool
    configured: bool
    available: bool
    vault_name: str | None = None
    note_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    last_scanned_at: datetime | None = None
    last_indexed_at: datetime | None = None


class ObsidianScanItemOut(BaseModel):
    kind: Literal["new", "changed", "unchanged", "missing", "skipped", "error"]
    relative_path: str
    reason: str | None = None
    observed_hash: str | None = None
    indexed_hash: str | None = None


class ObsidianOperationResponse(BaseModel):
    counts: dict[str, int]
    items: list[ObsidianScanItemOut]


class ObsidianNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    relative_path: str
    file_name: str
    title: str | None
    observed_hash: str | None
    indexed_hash: str | None
    size_bytes: int
    file_modified_at: datetime | None
    frontmatter: dict[str, Any]
    tags: list[str]
    aliases: list[str]
    outbound_links: list[dict[str, Any]]
    index_status: str
    chunk_count: int
    last_error: str | None
    last_scanned_at: datetime | None
    last_indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ObsidianNoteListResponse(BaseModel):
    total: int
    items: list[ObsidianNoteOut]


class ObsidianChunkOut(BaseModel):
    chunk_id: str
    source_id: str
    source_type: str
    source_name: str
    source_path: str
    note_hash: str
    chunk_index: int
    heading_path: list[str] = Field(default_factory=list)
    heading_level: int | None = None
    section_title: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    block_types: list[str] = Field(default_factory=list)
    content_hash: str | None = None
    parent_section_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    text: str


class ObsidianChunkListResponse(BaseModel):
    total: int
    items: list[ObsidianChunkOut]
