from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import ObsidianNote
from app.obsidian.markdown_chunker import StructuredMarkdownChunker
from app.obsidian.markdown_parser import MarkdownParser
from app.obsidian.models import ScanItem, ScanReport
from app.obsidian.scanner import ObsidianVaultScanner, VaultConfigurationError
from app.services.embedding_service import EmbeddingService, get_embedding_service
from app.services.vector_service import VectorService


class ObsidianDisabledError(RuntimeError):
    pass


class ObsidianIndexService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        scanner: ObsidianVaultScanner | None = None,
        parser: MarkdownParser | None = None,
        chunker: StructuredMarkdownChunker | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_service: VectorService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._scanner = scanner
        self.parser = parser or MarkdownParser()
        self.chunker = chunker or StructuredMarkdownChunker(
            min_chars=self.settings.obsidian_chunk_min_chars,
            target_chars=self.settings.obsidian_chunk_target_chars,
            max_chars=self.settings.obsidian_chunk_max_chars,
            hard_max_chars=self.settings.obsidian_atomic_block_hard_max_chars,
        )
        self._embedding_service = embedding_service
        self._vector_service = vector_service

    @property
    def scanner(self) -> ObsidianVaultScanner:
        if self._scanner is None:
            self._scanner = ObsidianVaultScanner(
                self.settings.obsidian_vault_path,
                include_patterns=self.settings.obsidian_include_pattern_list,
                exclude_dirs=self.settings.obsidian_exclude_dir_list,
                exclude_paths=self.settings.obsidian_exclude_path_list,
                max_file_bytes=self.settings.obsidian_max_file_bytes,
            )
        return self._scanner

    @property
    def embedding_service(self) -> EmbeddingService:
        if self._embedding_service is None:
            self._embedding_service = get_embedding_service()
        return self._embedding_service

    @property
    def vector_service(self) -> VectorService:
        if self._vector_service is None:
            self._vector_service = VectorService(vector_size=self.embedding_service.vector_size)
        return self._vector_service

    def status(self, db: Session) -> dict:
        counts = dict(
            db.execute(
                select(ObsidianNote.index_status, func.count(ObsidianNote.id)).group_by(ObsidianNote.index_status)
            ).all()
        )
        last_scanned_at, last_indexed_at = db.execute(
            select(func.max(ObsidianNote.last_scanned_at), func.max(ObsidianNote.last_indexed_at))
        ).one()
        configured = bool(self.settings.obsidian_vault_path and self.settings.obsidian_vault_path.is_absolute())
        available = bool(configured and self.settings.obsidian_vault_path and self.settings.obsidian_vault_path.is_dir())
        return {
            "enabled": self.settings.obsidian_enabled,
            "configured": configured,
            "available": available,
            "vault_name": self.settings.obsidian_vault_path.name if available and self.settings.obsidian_vault_path else None,
            "note_count": sum(counts.values()),
            "status_counts": counts,
            "last_scanned_at": last_scanned_at,
            "last_indexed_at": last_indexed_at,
        }

    def scan(self, db: Session) -> dict:
        self._ensure_enabled()
        existing = {note.relative_path: note for note in db.scalars(select(ObsidianNote)).all()}
        report = self.scanner.scan(existing)
        return self._public_report(report)

    def index(
        self,
        db: Session,
        *,
        relative_paths: list[str] | None = None,
        force: bool = False,
    ) -> dict:
        self._ensure_enabled()
        existing = {note.relative_path: note for note in db.scalars(select(ObsidianNote)).all()}
        existing_by_key = {path.casefold(): note for path, note in existing.items()}
        report = self.scanner.scan(existing)
        selected = {path.replace("\\", "/").strip("/").casefold() for path in relative_paths or []}
        outcome_items: list[ScanItem] = []
        for item in report.items:
            if selected and item.relative_path.casefold() not in selected:
                continue
            if force and item.kind == "unchanged":
                item.kind = "changed"
                item.reason = "forced_reindex"
            if item.kind in {"new", "changed"}:
                outcome_items.append(self._index_item(db, item, existing_by_key.get(item.relative_path.casefold())))
            elif item.kind == "missing":
                outcome_items.append(self._mark_missing(db, item, existing_by_key.get(item.relative_path.casefold())))
            elif item.kind == "unchanged":
                note = existing_by_key.get(item.relative_path.casefold())
                if note is not None:
                    note.observed_hash = item.observed_hash
                    note.last_scanned_at = datetime.now(UTC)
                    note.last_error = None
                    db.commit()
                outcome_items.append(item)
            else:
                outcome_items.append(item)
        return self._public_report(ScanReport(outcome_items))

    def reindex_note(self, db: Session, note_id: str) -> dict:
        note = db.get(ObsidianNote, note_id)
        if note is None:
            raise LookupError("Obsidian note not found")
        return self.index(db, relative_paths=[note.relative_path], force=True)

    def _index_item(self, db: Session, item: ScanItem, note: ObsidianNote | None) -> ScanItem:
        if item.vault_file is None or item.observed_hash is None:
            return ScanItem(kind="error", relative_path=item.relative_path, reason="scan_item_has_no_file")
        is_new = note is None
        if note is None:
            note = ObsidianNote(relative_path=item.relative_path, file_name=item.vault_file.file_name)
            db.add(note)
            db.flush()
        old_hash = note.indexed_hash
        new_hash = item.observed_hash
        new_vectors_written = False
        now = datetime.now(UTC)
        try:
            parsed = self.parser.parse_file(item.vault_file.absolute_path, item.relative_path)
            chunks = self.chunker.chunk(parsed, note_id=note.id, note_hash=new_hash)
            embeddings = self.embedding_service.embed_texts([chunk.text for chunk in chunks])
            self.vector_service.upsert_obsidian_chunks(chunks=chunks, embeddings=embeddings)
            new_vectors_written = bool(chunks)
            if old_hash and old_hash != new_hash:
                try:
                    self.vector_service.delete_obsidian_version(note.id, old_hash)
                except Exception:
                    if new_vectors_written:
                        self.vector_service.delete_obsidian_version(note.id, new_hash)
                    raise
            note.file_name = item.vault_file.file_name
            note.title = parsed.title
            note.observed_hash = new_hash
            note.indexed_hash = new_hash
            note.size_bytes = item.vault_file.size_bytes
            note.file_modified_at = item.vault_file.modified_at
            note.frontmatter = parsed.frontmatter
            note.tags = parsed.tags
            note.aliases = parsed.aliases
            note.outbound_links = parsed.outbound_links
            note.index_status = "indexed"
            note.chunk_count = len(chunks)
            note.last_error = "; ".join(parsed.warnings) or None
            note.last_scanned_at = now
            note.last_indexed_at = now
            try:
                db.commit()
            except Exception:
                db.rollback()
                if new_vectors_written:
                    self.vector_service.delete_obsidian_version(note.id, new_hash)
                raise
            return ScanItem(
                kind=item.kind,
                relative_path=item.relative_path,
                reason=item.reason,
                observed_hash=new_hash,
                indexed_hash=new_hash,
            )
        except Exception as exc:
            db.rollback()
            if new_vectors_written:
                try:
                    self.vector_service.delete_obsidian_version(note.id, new_hash)
                except Exception:
                    pass
            if is_new:
                note = db.scalar(select(ObsidianNote).where(ObsidianNote.relative_path == item.relative_path))
                if note is None:
                    note = ObsidianNote(relative_path=item.relative_path, file_name=item.vault_file.file_name)
                    db.add(note)
            else:
                note = db.get(ObsidianNote, note.id)
            if note is not None:
                note.observed_hash = new_hash
                note.size_bytes = item.vault_file.size_bytes
                note.file_modified_at = item.vault_file.modified_at
                note.index_status = "error"
                note.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                note.last_scanned_at = now
                db.commit()
            return ScanItem(
                kind="error",
                relative_path=item.relative_path,
                reason=type(exc).__name__,
                observed_hash=new_hash,
                indexed_hash=old_hash,
            )

    def _mark_missing(self, db: Session, item: ScanItem, note: ObsidianNote | None) -> ScanItem:
        if note is None:
            return item
        try:
            self.vector_service.delete_obsidian_version(note.id, note.indexed_hash)
            note.index_status = "missing"
            note.observed_hash = None
            note.chunk_count = 0
            note.last_error = None
            note.last_scanned_at = datetime.now(UTC)
            db.commit()
            return item
        except Exception as exc:
            db.rollback()
            note = db.get(ObsidianNote, note.id)
            if note is not None:
                note.index_status = "error"
                note.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                note.last_scanned_at = datetime.now(UTC)
                db.commit()
            return ScanItem(kind="error", relative_path=item.relative_path, reason=type(exc).__name__)

    def _ensure_enabled(self) -> None:
        if not self.settings.obsidian_enabled:
            raise ObsidianDisabledError("Obsidian integration is disabled")
        try:
            self.scanner
        except VaultConfigurationError:
            raise

    @staticmethod
    def _public_report(report: ScanReport) -> dict:
        return {"counts": report.counts, "items": [item.public_dict() for item in report.items]}
