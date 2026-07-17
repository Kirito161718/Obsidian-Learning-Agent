from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


ScanKind = Literal["new", "changed", "unchanged", "missing", "skipped", "error"]


@dataclass(slots=True)
class VaultFile:
    relative_path: str
    absolute_path: Path = field(repr=False)
    file_name: str = ""
    size_bytes: int = 0
    modified_at: datetime | None = None
    observed_hash: str = ""


@dataclass(slots=True)
class ScanItem:
    kind: ScanKind
    relative_path: str
    reason: str | None = None
    observed_hash: str | None = None
    indexed_hash: str | None = None
    vault_file: VaultFile | None = field(default=None, repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "relative_path": self.relative_path,
            "reason": self.reason,
            "observed_hash": self.observed_hash,
            "indexed_hash": self.indexed_hash,
        }


@dataclass(slots=True)
class ScanReport:
    items: list[ScanItem]

    @property
    def counts(self) -> dict[str, int]:
        keys = ("new", "changed", "unchanged", "missing", "skipped", "error")
        return {key: sum(item.kind == key for item in self.items) for key in keys}


@dataclass(slots=True)
class MarkdownBlock:
    block_type: str
    text: str
    line_start: int
    line_end: int
    heading_path: tuple[str, ...] = ()
    heading_level: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedMarkdownNote:
    relative_path: str
    file_name: str
    title: str
    frontmatter: dict[str, Any]
    tags: list[str]
    aliases: list[str]
    outbound_links: list[dict[str, Any]]
    blocks: list[MarkdownBlock]
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ObsidianChunk:
    chunk_id: str
    parent_section_id: str
    note_id: str
    note_hash: str
    relative_path: str
    file_name: str
    title: str
    text: str
    chunk_index: int
    heading_path: tuple[str, ...]
    heading_level: int
    section_title: str | None
    line_start: int
    line_end: int
    block_types: tuple[str, ...]
    content_hash: str
    tags: tuple[str, ...] = ()
    links: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
