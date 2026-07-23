from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, Settings
from app.db.models import (
    ObsidianNote,
    OfficialSource,
    OfficialSourceNoteLink,
    OfficialSourceSnapshot,
    UpdateProposal,
    UpdateProposalItem,
)
from app.obsidian.scanner import ObsidianVaultScanner, VaultConfigurationError
from app.official_sources.diff_engine import OfficialDiffEngine
from app.official_sources.errors import official_error
from app.official_sources.normalizer import sections_with_content
from app.official_sources.note_patcher import NotePatchService
from app.official_sources.proposal_validator import ProposalPlan, ProposalValidator
from app.official_sources.snapshot_store import SnapshotStore
from app.security.redaction import safe_text_preview, stable_hash
from app.services.llm_service import DeepSeekClient
from app.observability import get_observability


class ProposalBuilder:
    def __init__(
        self,
        settings: Settings,
        *,
        llm_client=None,
        snapshot_store: SnapshotStore | None = None,
        patcher: NotePatchService | None = None,
    ) -> None:
        self.settings = settings
        self.llm_client = llm_client
        self.snapshot_store = snapshot_store or SnapshotStore(settings)
        self.patcher = patcher or NotePatchService()
        self.validator = ProposalValidator(settings)
        self.diff_engine = OfficialDiffEngine()

    def build(self, db: Session, *, proposal_id: str, source_id: str, snapshot_id: str) -> UpdateProposal | None:
        existing_proposal = db.get(UpdateProposal, proposal_id)
        if existing_proposal is not None:
            return existing_proposal
        source = db.get(OfficialSource, source_id)
        snapshot = db.get(OfficialSourceSnapshot, snapshot_id)
        if source is None or snapshot is None or snapshot.source_id != source_id:
            raise official_error("official_source_not_found", "Official source snapshot was not found", status_code=404)
        links = list(
            db.scalars(
                select(OfficialSourceNoteLink).where(
                    OfficialSourceNoteLink.source_id == source_id,
                    OfficialSourceNoteLink.enabled.is_(True),
                )
            ).all()
        )
        if not links:
            raise official_error("official_note_link_missing", "Official source has no linked notes")
        if len(links) > self.settings.official_proposal_max_items:
            raise official_error("official_proposal_invalid", "Proposal item limit exceeded")
        snapshots = list(
            db.scalars(
                select(OfficialSourceSnapshot)
                .where(OfficialSourceSnapshot.source_id == source_id)
                .order_by(OfficialSourceSnapshot.created_at.desc(), OfficialSourceSnapshot.id.desc())
            ).all()
        )
        current_index = next((index for index, item in enumerate(snapshots) if item.id == snapshot_id), None)
        previous = snapshots[current_index + 1] if current_index is not None and current_index + 1 < len(snapshots) else None
        current_text = self.snapshot_store.load(snapshot.storage_path)
        previous_text = self.snapshot_store.load(previous.storage_path) if previous else None
        with get_observability().trace(
            name="ola.official.snapshot.compare",
            run_type="chain",
            metadata={
                "flow_type": "official_snapshot_compare",
                "internal_run_id": proposal_id,
                "official_source_id": source_id,
                "snapshot_id": snapshot_id,
                "proposal_id": proposal_id,
                "previous_snapshot_id": previous.id if previous else None,
                "status": "running",
            },
            tags=["ola", "official", "snapshot-compare"],
        ) as span:
            comparison = self.diff_engine.compare(previous_text, current_text)
            span.update(
                {
                    "status": "succeeded",
                    "baseline": bool(comparison["baseline"]),
                    "added_line_count": len(comparison["added_lines"]),
                    "removed_line_count": len(comparison["removed_lines"]),
                }
            )
        section_content = sections_with_content(current_text, snapshot.section_manifest)

        notes: dict[str, tuple[ObsidianNote, OfficialSourceNoteLink, object]] = {}
        for link in links:
            note = db.get(ObsidianNote, link.obsidian_note_id)
            if note is None or note.index_status == "missing":
                raise official_error("official_note_missing", "Linked Obsidian note is unavailable", status_code=404)
            path = self._note_path(note.relative_path)
            document = self.patcher.read(path)
            notes[note.id] = (note, link, document)

        meaningful_additions = [line for line in comparison["added_lines"] if len(line.strip()) >= 20]
        combined_notes = "\n".join(document.normalized_text for _, _, document in notes.values())
        if meaningful_additions and all(line in combined_notes for line in meaningful_additions):
            return None

        client = self.llm_client or DeepSeekClient()
        plan_payload = self._prompt_payload(source, snapshot, comparison, section_content, notes)
        raw_plan = client.generate_official_update_plan(plan_payload)
        try:
            plan = ProposalPlan.model_validate(raw_plan)
        except Exception as exc:
            raise official_error("official_proposal_invalid", "LLM returned an invalid update plan", status_code=502) from exc
        if not plan.items:
            return None
        if len(plan.items) > self.settings.official_proposal_max_items:
            raise official_error("official_proposal_invalid", "Proposal item limit exceeded")
        if len({item.note_id for item in plan.items}) != len(plan.items):
            raise official_error("official_proposal_invalid", "Proposal contains duplicate note items")

        artifact_root = self.settings.update_proposal_dir / proposal_id
        if artifact_root.exists():
            shutil.rmtree(artifact_root)
        artifact_root.mkdir(parents=True, exist_ok=False)
        item_rows: list[UpdateProposalItem] = []
        payload_items: list[dict] = []
        try:
            for plan_item in plan.items:
                note_entry = notes.get(plan_item.note_id)
                if note_entry is None:
                    raise official_error("official_proposal_invalid", "Proposal targets an unlinked note")
                note, link, document = note_entry
                operations = self.validator.validate_operations(
                    plan_item.operations,
                    sections=section_content,
                    allowed_heading_paths=link.target_heading_paths or [],
                )
                result = self.patcher.apply(document, operations)
                if result.content_hash == document.content_hash:
                    continue
                if result.change_ratio > self.settings.official_proposal_max_change_ratio:
                    raise official_error("official_change_ratio_exceeded", "Proposal change ratio exceeds the configured limit")
                item_id = hashlib.sha256(f"{proposal_id}:{note.id}".encode()).hexdigest()[:36]
                content_path, diff_path = self._write_artifacts(
                    artifact_root, item_id, result.content, result.unified_diff
                )
                evidence = [
                    reference
                    for operation in operations
                    for reference in operation["evidence_refs"]
                ]
                row = UpdateProposalItem(
                    id=item_id,
                    proposal_id=proposal_id,
                    obsidian_note_id=note.id,
                    relative_path=note.relative_path,
                    base_note_hash=document.content_hash,
                    proposed_note_hash=result.content_hash,
                    proposed_content_path=content_path,
                    diff_path=diff_path,
                    change_ratio=result.change_ratio,
                    operation_count=len(operations),
                    operation_manifest=operations,
                    evidence_manifest=evidence,
                    status="ready",
                )
                item_rows.append(row)
                payload_items.append(
                    {
                        "note_id": note.id,
                        "relative_path": note.relative_path,
                        "base_note_hash": document.content_hash,
                        "proposed_note_hash": result.content_hash,
                        "change_ratio": round(result.change_ratio, 8),
                        "operations": operations,
                        "diff_hash": hashlib.sha256(result.unified_diff.encode("utf-8")).hexdigest(),
                    }
                )
            if not item_rows:
                shutil.rmtree(artifact_root, ignore_errors=True)
                return None
            summary = safe_text_preview(plan.summary or "Official source update proposal", self.settings.official_proposal_summary_max_chars)
            payload_hash = stable_hash(
                {
                    "proposal_id": proposal_id,
                    "source_id": source_id,
                    "snapshot_id": snapshot_id,
                    "previous_snapshot_id": previous.id if previous else None,
                    "summary": summary,
                    "items": payload_items,
                    "approval_scope": "all_or_nothing",
                    "risk_level": "HIGH_RISK",
                }
            )
            now = datetime.now(UTC)
            proposal = UpdateProposal(
                id=proposal_id,
                source_id=source_id,
                snapshot_id=snapshot_id,
                previous_snapshot_id=previous.id if previous else None,
                status="ready_for_review",
                payload_hash=payload_hash,
                summary=summary,
                item_count=len(item_rows),
                graph_thread_id=proposal_id,
                model_name=getattr(client, "model", None),
                model_call_count=1,
                ready_at=now,
                created_at=now,
            )
            db.add(proposal)
            db.add_all(item_rows)
            db.commit()
            db.refresh(proposal)
            return proposal
        except Exception:
            db.rollback()
            shutil.rmtree(artifact_root, ignore_errors=True)
            raise

    def _prompt_payload(self, source, snapshot, comparison, sections, notes) -> dict:
        budget = self.settings.rag_total_prompt_max_chars
        section_budget = max(1000, budget // 2)
        section_rows: list[dict] = []
        used = 0
        for item in snapshot.section_manifest:
            value = sections.get(item["section_id"], "")
            remaining = section_budget - used
            if remaining <= 0:
                break
            value = value[:remaining]
            section_rows.append(
                {"section_id": item["section_id"], "heading_path": item.get("heading_path") or [], "text": value}
            )
            used += len(value)
        note_budget = max(1000, budget - used)
        each = max(500, note_budget // max(1, len(notes)))
        note_rows = [
            {
                "note_id": note.id,
                "relative_path": note.relative_path,
                "target_heading_paths": link.target_heading_paths or [],
                "content": document.normalized_text[:each],
            }
            for note, link, document in notes.values()
        ]
        return {
            "source": {"id": source.id, "name": source.name},
            "snapshot": {"id": snapshot.id, "content_hash": snapshot.content_hash, "sections": section_rows},
            "change": {
                "baseline": bool(comparison["baseline"]),
                "added_lines": comparison["added_lines"][:200],
                "removed_lines": comparison["removed_lines"][:200],
            },
            "notes": note_rows,
            "rules": {"approval_scope": "all_or_nothing", "frontmatter_mutation": False, "whole_file_replace": False},
        }

    def _note_path(self, relative_path: str) -> Path:
        if not self.settings.obsidian_vault_path:
            raise official_error("official_note_outside_vault", "Obsidian Vault is not configured")
        try:
            scanner = ObsidianVaultScanner(
                self.settings.obsidian_vault_path,
                include_patterns=self.settings.obsidian_include_pattern_list,
                exclude_dirs=self.settings.obsidian_exclude_dir_list,
                exclude_paths=self.settings.obsidian_exclude_path_list,
                max_file_bytes=self.settings.obsidian_max_file_bytes,
            )
            path = scanner.resolve_note_path(relative_path)
        except VaultConfigurationError as exc:
            raise official_error("official_note_outside_vault", "Obsidian note path is unsafe") from exc
        if not path.is_file():
            raise official_error("official_note_missing", "Obsidian note is unavailable", status_code=404)
        return path

    @staticmethod
    def _write_artifacts(root: Path, item_id: str, content: bytes, diff: str) -> tuple[str, str]:
        content_target = root / f"{item_id}.md"
        diff_target = root / f"{item_id}.diff"
        for target, value in ((content_target, content), (diff_target, diff.encode("utf-8"))):
            fd, temporary = tempfile.mkstemp(prefix=f".{item_id}-", suffix=".tmp", dir=root)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(value)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return content_target.relative_to(BASE_DIR).as_posix(), diff_target.relative_to(BASE_DIR).as_posix()
