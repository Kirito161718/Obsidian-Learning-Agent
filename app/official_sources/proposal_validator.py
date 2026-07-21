from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings
from app.official_sources.errors import official_error

UNTRUSTED_INSTRUCTION_PATTERN = re.compile(
    r"(?i)(ignore\s+(all\s+)?previous\s+instructions|<\|/?system\|>|javascript:|file://|powershell\s+-|cmd\.exe)"
)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_section_id: str = Field(min_length=1, max_length=100)
    evidence_excerpt: str = Field(min_length=1, max_length=5000)


class EditOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_type: Literal["replace", "insert_before", "insert_after", "append_section"]
    target_heading_path: list[str] = Field(default_factory=list, max_length=10)
    target_excerpt: str = Field(default="", max_length=20_000)
    replacement_markdown: str = Field(min_length=1, max_length=100_000)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=20)

    @field_validator("target_heading_path")
    @classmethod
    def valid_heading_path(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 200 for item in value):
            raise ValueError("Target heading path is invalid")
        return [item.strip() for item in value]


class ProposalPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=36)
    operations: list[EditOperation]


class ProposalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=10_000)
    items: list[ProposalPlanItem] = Field(default_factory=list)


class ProposalValidator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate_operations(
        self,
        operations: list[EditOperation],
        *,
        sections: dict[str, str],
        allowed_heading_paths: list[list[str]],
    ) -> list[dict]:
        if len(operations) > self.settings.official_proposal_max_operations_per_note:
            raise official_error("official_proposal_invalid", "Proposal operation limit exceeded")
        output: list[dict] = []
        for operation in operations:
            if operation.operation_type != "append_section" and not operation.target_excerpt:
                raise official_error("official_proposal_invalid", "Edit target excerpt is required")
            if allowed_heading_paths and not any(
                operation.target_heading_path[: len(scope)] == scope for scope in allowed_heading_paths
            ):
                raise official_error("official_proposal_invalid", "Edit operation is outside the linked heading scope")
            if UNTRUSTED_INSTRUCTION_PATTERN.search(operation.replacement_markdown):
                raise official_error("official_proposal_invalid", "Replacement contains a prohibited instruction pattern")
            evidence: list[dict] = []
            for reference in operation.evidence_refs:
                section = sections.get(reference.snapshot_section_id)
                if section is None:
                    raise official_error("official_evidence_invalid", "Proposal evidence section does not exist")
                excerpt = reference.evidence_excerpt.strip()
                if len(excerpt) > self.settings.official_evidence_excerpt_max_chars:
                    raise official_error("official_evidence_invalid", "Proposal evidence excerpt is too long")
                if excerpt not in section:
                    raise official_error("official_evidence_invalid", "Proposal evidence excerpt does not match the snapshot")
                evidence.append(
                    {"snapshot_section_id": reference.snapshot_section_id, "evidence_excerpt": excerpt}
                )
            item = operation.model_dump(mode="json")
            item["evidence_refs"] = evidence
            output.append(item)
        return output
