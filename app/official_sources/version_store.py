from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, Settings
from app.db.models import NoteVersion
from app.official_sources.errors import official_error


class NoteVersionStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create(
        self,
        db: Session,
        *,
        version_id: str,
        note_id: str,
        proposal_id: str,
        snapshot_id: str,
        relative_path: str,
        before_content: bytes,
        before_hash: str,
        after_hash: str,
        diff: str,
    ) -> NoteVersion:
        existing = db.scalar(
            select(NoteVersion).where(
                NoteVersion.obsidian_note_id == note_id,
                NoteVersion.proposal_id == proposal_id,
            )
        )
        if existing:
            return existing
        root = self.settings.note_version_dir / note_id
        root.mkdir(parents=True, exist_ok=True)
        backup = root / f"{version_id}.md"
        diff_path = root / f"{version_id}.diff"
        self._atomic_write(backup, before_content)
        self._atomic_write(diff_path, diff.encode("utf-8"))
        row = NoteVersion(
            id=version_id,
            obsidian_note_id=note_id,
            proposal_id=proposal_id,
            snapshot_id=snapshot_id,
            relative_path=relative_path,
            before_hash=before_hash,
            after_hash=after_hash,
            backup_path=backup.relative_to(BASE_DIR).as_posix(),
            diff_path=diff_path.relative_to(BASE_DIR).as_posix(),
            pinned=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def load_backup(self, version: NoteVersion) -> bytes:
        return self.resolve(version.backup_path, version.obsidian_note_id, ".md").read_bytes()

    def resolve(self, relative_path: str, note_id: str, suffix: str) -> Path:
        candidate = (BASE_DIR / relative_path).resolve(strict=False)
        root = (self.settings.note_version_dir / note_id).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise official_error("official_atomic_write_failed", "Note version path is invalid", status_code=500) from exc
        if candidate.suffix != suffix:
            raise official_error("official_atomic_write_failed", "Note version path is invalid", status_code=500)
        return candidate

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        fd, temporary = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
