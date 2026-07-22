from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, Settings, get_settings
from app.db.models import (
    AgentRun,
    ApprovalRequest,
    NoteVersion,
    ObsidianNote,
    UpdateProposal,
    UpdateProposalItem,
)
from app.obsidian.scanner import ObsidianVaultScanner, VaultConfigurationError
from app.official_sources.errors import OfficialSourceError, official_error
from app.official_sources.note_patcher import NotePatchService
from app.official_sources.version_store import NoteVersionStore
from app.security.redaction import safe_text_preview
from app.services.approval_service import ApprovalService
from app.services.obsidian_index_service import ObsidianIndexService


class UpdateProposalService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        index_service: ObsidianIndexService | None = None,
        replace_file: Callable[[str | Path, str | Path], None] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.approvals = ApprovalService(self.settings)
        self.versions = NoteVersionStore(self.settings)
        self.patcher = NotePatchService()
        self.index_service = index_service
        self.replace_file = replace_file or os.replace

    def list(self, db: Session) -> list[UpdateProposal]:
        return list(db.scalars(select(UpdateProposal).order_by(UpdateProposal.created_at.desc())).all())

    def get(self, db: Session, proposal_id: str) -> UpdateProposal:
        proposal = db.get(UpdateProposal, proposal_id)
        if proposal is None:
            raise official_error("official_proposal_invalid", "Update proposal was not found", status_code=404)
        return proposal

    def items(self, db: Session, proposal_id: str) -> list[UpdateProposalItem]:
        self.get(db, proposal_id)
        return list(
            db.scalars(
                select(UpdateProposalItem)
                .where(UpdateProposalItem.proposal_id == proposal_id)
                .order_by(UpdateProposalItem.relative_path)
            ).all()
        )

    def read_diff(self, db: Session, proposal_id: str) -> list[dict]:
        return [
            {
                "item_id": item.id,
                "relative_path": item.relative_path,
                "change_ratio": item.change_ratio,
                "diff": self._artifact_path(proposal_id, item.diff_path, ".diff").read_text(encoding="utf-8"),
            }
            for item in self.items(db, proposal_id)
        ]

    def apply(self, db: Session, *, proposal_id: str, approval_id: str, payload_hash: str) -> dict:
        proposal = db.scalar(select(UpdateProposal).where(UpdateProposal.id == proposal_id).with_for_update())
        approval = db.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id).with_for_update())
        if proposal is None or approval is None or proposal.approval_request_id != approval.id:
            raise official_error("official_approval_required", "Approved proposal is unavailable", status_code=404)
        if proposal.status in {"applied", "applied_with_index_error"} and approval.status == "consumed":
            return {"proposal_id": proposal.id, "status": proposal.status, "applied_count": proposal.item_count}
        if proposal.payload_hash != payload_hash or approval.payload_hash != payload_hash:
            raise official_error("official_approval_hash_mismatch", "Approval payload hash does not match", status_code=409)
        if approval.status != "approved":
            raise official_error("official_approval_required", "Proposal requires an approved request", status_code=409)
        if proposal.status not in {"approved", "applying"}:
            raise official_error("official_proposal_invalid", "Proposal is not ready to apply", status_code=409)

        items = self.items(db, proposal_id)
        if not items or len(items) != proposal.item_count:
            raise official_error("official_proposal_invalid", "Proposal items are incomplete", status_code=409)
        targets = self._load_targets(db, proposal, items)
        current_hashes = {item.id: hashlib.sha256(document.raw).hexdigest() for item, _, document, _ in targets}
        all_base = all(current_hashes[item.id] == item.base_note_hash for item, *_ in targets)
        all_proposed = all(current_hashes[item.id] == item.proposed_note_hash for item, *_ in targets)
        if proposal.status == "approved" and not all_base:
            self._mark_stale(db, proposal, approval, items)
            return {"proposal_id": proposal.id, "status": "stale", "applied_count": 0}
        if proposal.status == "applying" and not (all_base or all_proposed):
            rollback_ok = self._recover_partial(db, proposal, targets, current_hashes)
            proposal.status = "failed"
            proposal.error_code = "official_atomic_write_failed" if rollback_ok else "official_rollback_failed"
            proposal.error_preview = "Interrupted apply was rolled back" if rollback_ok else "Interrupted apply rollback failed"
            self.approvals.consume(db, approval)
            db.commit()
            return {"proposal_id": proposal.id, "status": "failed", "applied_count": 0}

        run = AgentRun(
            run_type="official_apply",
            status="running",
            official_source_id=proposal.source_id,
            official_proposal_id=proposal.id,
            approval_status=approval.status,
            mode="manual",
            started_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        db.add(run)
        proposal.status = "applying"
        db.commit()
        staged: dict[str, Path] = {}
        versions: dict[str, NoteVersion] = {}
        replaced: list[tuple[UpdateProposalItem, Path]] = []
        try:
            if not all_proposed:
                for item, target, document, proposed in targets:
                    staged[item.id] = self._stage_same_directory(target, proposed)
                for item, target, document, proposed in targets:
                    diff = self._artifact_path(proposal.id, item.diff_path, ".diff").read_text(encoding="utf-8")
                    version_id = hashlib.sha256(f"{proposal.id}:{item.obsidian_note_id}".encode()).hexdigest()[:36]
                    versions[item.id] = self.versions.create(
                        db,
                        version_id=version_id,
                        note_id=item.obsidian_note_id,
                        proposal_id=proposal.id,
                        snapshot_id=proposal.snapshot_id,
                        relative_path=item.relative_path,
                        before_content=document.raw,
                        before_hash=item.base_note_hash,
                        after_hash=item.proposed_note_hash,
                        diff=diff,
                    )
                for item, target, _, _ in targets:
                    self.replace_file(staged[item.id], target)
                    replaced.append((item, target))
                    staged.pop(item.id, None)
            else:
                versions = {
                    row.proposal_id + ":" + row.obsidian_note_id: row
                    for row in db.scalars(select(NoteVersion).where(NoteVersion.proposal_id == proposal.id)).all()
                }
            for item in items:
                item.status = "applied"
            proposal.status = "applied"
            proposal.applied_at = datetime.now(UTC)
            self.approvals.consume(db, approval)
            run.approval_status = approval.status
            run.applied_count = len(items)
            db.commit()
        except Exception as exc:
            rollback_ok = self._rollback_replaced(db, proposal, replaced)
            db.rollback()
            proposal = db.get(UpdateProposal, proposal.id)
            approval = db.get(ApprovalRequest, approval.id)
            run = db.get(AgentRun, run.id)
            if proposal:
                proposal.status = "failed"
                proposal.error_code = "official_atomic_write_failed" if rollback_ok else "official_rollback_failed"
                proposal.error_preview = safe_text_preview(type(exc).__name__, 500)
            if approval and approval.status == "approved":
                self.approvals.consume(db, approval)
            if run:
                self._finish_run(run, "failed", error_code=proposal.error_code if proposal else "official_atomic_write_failed")
            db.commit()
            code = "official_atomic_write_failed" if rollback_ok else "official_rollback_failed"
            raise official_error(code, "Atomic note batch apply failed", status_code=500) from exc
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)

        reindex = self._reindex(db, proposal, items)
        run = db.get(AgentRun, run.id)
        if reindex["failed"]:
            proposal.status = "applied_with_index_error"
            proposal.error_code = "official_reindex_failed"
            proposal.error_preview = "One or more applied notes require reindex retry"
            if run:
                run.reindex_status = "failed"
                self._finish_run(run, "succeeded", error_code="official_reindex_failed")
        else:
            proposal.status = "applied"
            proposal.error_code = None
            proposal.error_preview = None
            if run:
                run.reindex_status = "succeeded"
                self._finish_run(run, "succeeded")
        db.commit()
        return {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "applied_count": len(items),
            "reindex": reindex,
        }

    def retry_reindex(self, db: Session, proposal_id: str) -> dict:
        proposal = self.get(db, proposal_id)
        if proposal.status != "applied_with_index_error":
            raise official_error("official_reindex_failed", "Proposal does not require reindex retry", status_code=409)
        items = self.items(db, proposal_id)
        result = self._reindex(db, proposal, items)
        if not result["failed"]:
            proposal.status = "applied"
            proposal.error_code = None
            proposal.error_preview = None
            db.commit()
        return {"proposal_id": proposal.id, "status": proposal.status, "reindex": result}

    def _load_targets(self, db, proposal, items):
        targets = []
        for item in items:
            note = db.get(ObsidianNote, item.obsidian_note_id)
            if note is None or note.relative_path != item.relative_path:
                raise official_error("official_note_missing", "Proposal note is unavailable", status_code=404)
            target = self._note_path(item.relative_path)
            document = self.patcher.read(target)
            proposed_path = self._artifact_path(proposal.id, item.proposed_content_path, ".md")
            proposed = proposed_path.read_bytes()
            if hashlib.sha256(proposed).hexdigest() != item.proposed_note_hash:
                raise official_error("official_proposal_invalid", "Proposed content hash does not match", status_code=409)
            proposed_document = self.patcher.read(proposed_path)
            if document.content_hash == item.base_note_hash and proposed_document.frontmatter != document.frontmatter:
                raise official_error("official_proposal_invalid", "Proposal attempts to modify Frontmatter", status_code=409)
            targets.append((item, target, document, proposed))
        return targets

    def _mark_stale(self, db, proposal, approval, items) -> None:
        proposal.status = "stale"
        proposal.error_code = "official_proposal_stale"
        proposal.error_preview = "One or more target notes changed after proposal generation"
        for item in items:
            item.status = "stale"
        self.approvals.consume(db, approval)
        db.commit()

    def _recover_partial(self, db, proposal, targets, current_hashes) -> bool:
        replaced = [(item, target) for item, target, _, _ in targets if current_hashes[item.id] == item.proposed_note_hash]
        return self._rollback_replaced(db, proposal, replaced)

    def _rollback_replaced(self, db, proposal, replaced) -> bool:
        ok = True
        for item, target in reversed(replaced):
            version = db.scalar(
                select(NoteVersion).where(
                    NoteVersion.proposal_id == proposal.id,
                    NoteVersion.obsidian_note_id == item.obsidian_note_id,
                )
            )
            if version is None:
                ok = False
                continue
            try:
                temporary = self._stage_same_directory(target, self.versions.load_backup(version))
                try:
                    self.replace_file(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            except Exception:
                ok = False
        return ok

    def _reindex(self, db: Session, proposal: UpdateProposal, items: list[UpdateProposalItem]) -> dict:
        service = self.index_service or ObsidianIndexService(settings=self.settings)
        results: list[dict] = []
        failed = False
        for item in items:
            try:
                result = service.reindex_note(db, item.obsidian_note_id)
                item_failed = bool((result.get("counts") or {}).get("error")) or any(
                    entry.get("kind") == "error" for entry in (result.get("items") or [])
                )
            except Exception as exc:
                result = {"error_code": "official_reindex_failed", "error": type(exc).__name__}
                item_failed = True
            failed = failed or item_failed
            results.append({"note_id": item.obsidian_note_id, "failed": item_failed, "result": result})
        return {"failed": failed, "items": results}

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
        if ".obsidian" in {part.casefold() for part in Path(relative_path).parts} or path.suffix.casefold() != ".md":
            raise official_error("official_note_outside_vault", "Obsidian note path is unsafe")
        return path

    def _artifact_path(self, proposal_id: str, relative_path: str, suffix: str) -> Path:
        candidate = (BASE_DIR / relative_path).resolve(strict=False)
        root = (self.settings.update_proposal_dir / proposal_id).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise official_error("official_proposal_invalid", "Proposal artifact path is invalid", status_code=500) from exc
        if candidate.suffix != suffix or not candidate.is_file():
            raise official_error("official_proposal_invalid", "Proposal artifact is unavailable", status_code=500)
        return candidate

    @staticmethod
    def _stage_same_directory(target: Path, content: bytes) -> Path:
        fd, raw_path = tempfile.mkstemp(prefix=f".{target.name}-", suffix=".tmp", dir=target.parent)
        temporary = Path(raw_path)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            return temporary
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _finish_run(run: AgentRun, status: str, error_code: str | None = None) -> None:
        finished = datetime.now(UTC)
        run.status = status
        run.error_code = error_code
        run.finished_at = finished
        started = run.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        run.duration_ms = max(0, int((finished - started).total_seconds() * 1000))
