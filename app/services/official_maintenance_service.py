from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import NoteVersion, OfficialSourceSnapshot, UpdateProposal, UpdateProposalItem
from app.official_sources.snapshot_store import SnapshotStore
from app.official_sources.version_store import NoteVersionStore


class OfficialMaintenanceService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.snapshots = SnapshotStore(self.settings)
        self.versions = NoteVersionStore(self.settings)

    def cleanup(self, db: Session, *, dry_run: bool) -> dict:
        snapshot_ids = self._snapshot_candidates(db)
        version_ids = self._version_candidates(db)
        proposal_ids = self._proposal_artifact_candidates(db)
        temp_files = [path for path in self.settings.official_temp_dir.glob("*.tmp") if path.is_file()] if self.settings.official_temp_dir.exists() else []
        result = {
            "dry_run": dry_run,
            "snapshot_ids": snapshot_ids,
            "version_ids": version_ids,
            "proposal_artifact_ids": proposal_ids,
            "temporary_files": [path.name for path in temp_files],
        }
        if dry_run:
            return result
        for snapshot_id in snapshot_ids:
            row = db.get(OfficialSourceSnapshot, snapshot_id)
            if row:
                self.snapshots.delete(row.storage_path)
                db.delete(row)
        for version_id in version_ids:
            row = db.get(NoteVersion, version_id)
            if row:
                self.versions.resolve(row.backup_path, row.obsidian_note_id, ".md").unlink(missing_ok=True)
                self.versions.resolve(row.diff_path, row.obsidian_note_id, ".diff").unlink(missing_ok=True)
                db.delete(row)
        for proposal_id in proposal_ids:
            root = (self.settings.update_proposal_dir / proposal_id).resolve(strict=False)
            try:
                root.relative_to(self.settings.update_proposal_dir.resolve(strict=False))
            except ValueError:
                continue
            shutil.rmtree(root, ignore_errors=True)
            for item in db.scalars(select(UpdateProposalItem).where(UpdateProposalItem.proposal_id == proposal_id)).all():
                item.error_code = "artifacts_cleaned"
                item.error_preview = "Retained metadata; proposal artifacts were removed by manual cleanup"
        for path in temp_files:
            path.unlink(missing_ok=True)
        db.commit()
        return result

    def _snapshot_candidates(self, db: Session) -> list[str]:
        referenced = set(
            value
            for row in db.execute(select(UpdateProposal.snapshot_id, UpdateProposal.previous_snapshot_id)).all()
            for value in row
            if value
        )
        referenced.update(value for value in db.scalars(select(NoteVersion.snapshot_id)).all() if value)
        candidates: list[str] = []
        source_ids = list(db.scalars(select(OfficialSourceSnapshot.source_id).distinct()).all())
        for source_id in source_ids:
            rows = list(
                db.scalars(
                    select(OfficialSourceSnapshot)
                    .where(OfficialSourceSnapshot.source_id == source_id)
                    .order_by(OfficialSourceSnapshot.created_at.desc())
                ).all()
            )
            candidates.extend(row.id for row in rows[self.settings.official_snapshot_keep :] if row.id not in referenced)
        return candidates

    def _version_candidates(self, db: Session) -> list[str]:
        candidates: list[str] = []
        note_ids = list(db.scalars(select(NoteVersion.obsidian_note_id).distinct()).all())
        for note_id in note_ids:
            rows = list(
                db.scalars(
                    select(NoteVersion)
                    .where(NoteVersion.obsidian_note_id == note_id)
                    .order_by(NoteVersion.created_at.desc())
                ).all()
            )
            unpinned = [row for row in rows if not row.pinned]
            candidates.extend(row.id for row in unpinned[self.settings.note_version_keep :])
        return candidates

    def _proposal_artifact_candidates(self, db: Session) -> list[str]:
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.update_proposal_retention_days)
        return list(
            db.scalars(
                select(UpdateProposal.id).where(
                    UpdateProposal.created_at < cutoff,
                    UpdateProposal.status.in_({"applied", "applied_with_index_error", "rejected", "failed", "stale"}),
                )
            ).all()
        )
