from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import (
    AgentRun,
    ObsidianNote,
    OfficialSource,
    OfficialSourceNoteLink,
    OfficialSourceSnapshot,
)
from app.obsidian.scanner import ObsidianVaultScanner, VaultConfigurationError
from app.official_sources.errors import OfficialSourceError, official_error
from app.official_sources.fetcher import OfficialSourceFetcher
from app.official_sources.normalizer import NORMALIZER_VERSION, OfficialContentNormalizer
from app.official_sources.snapshot_store import SnapshotStore
from app.official_sources.url_security import URLSecurityValidator
from app.schemas.official_sources import NoteLinkCreate, OfficialSourceCreate, OfficialSourcePatch
from app.security.redaction import safe_text_preview


class OfficialSourceService:
    def __init__(self, settings: Settings | None = None, validator: URLSecurityValidator | None = None) -> None:
        self.settings = settings or get_settings()
        self.validator = validator or URLSecurityValidator(self.settings)

    def create(self, db: Session, payload: OfficialSourceCreate) -> OfficialSource:
        self._ensure_enabled()
        validated = self.validator.validate_registered(payload.url)
        source = OfficialSource(
            name=payload.name.strip(),
            url=validated.canonical_url,
            canonical_url=validated.canonical_url,
            allowed_host=validated.host,
            source_type=payload.source_type,
            content_selector=self._selector(payload.content_selector),
            enabled=True,
            status="active",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    def list(self, db: Session) -> list[OfficialSource]:
        self._ensure_enabled()
        return list(db.scalars(select(OfficialSource).order_by(OfficialSource.created_at.desc())).all())

    def get(self, db: Session, source_id: str) -> OfficialSource:
        self._ensure_enabled()
        source = db.get(OfficialSource, source_id)
        if source is None:
            raise official_error("official_source_not_found", "Official source was not found", status_code=404)
        return source

    def update(self, db: Session, source_id: str, payload: OfficialSourcePatch) -> OfficialSource:
        source = self.get(db, source_id)
        values = payload.model_dump(exclude_unset=True)
        if "name" in values:
            source.name = str(values["name"]).strip()
        if "content_selector" in values:
            source.content_selector = self._selector(values["content_selector"])
        if "enabled" in values:
            source.enabled = bool(values["enabled"])
            source.status = "active" if source.enabled else "disabled"
        db.commit()
        db.refresh(source)
        return source

    def set_enabled(self, db: Session, source_id: str, enabled: bool) -> OfficialSource:
        source = self.get(db, source_id)
        source.enabled = enabled
        source.status = "active" if enabled else "disabled"
        db.commit()
        db.refresh(source)
        return source

    def list_links(self, db: Session, source_id: str) -> list[OfficialSourceNoteLink]:
        self.get(db, source_id)
        return list(
            db.scalars(
                select(OfficialSourceNoteLink)
                .where(OfficialSourceNoteLink.source_id == source_id)
                .order_by(OfficialSourceNoteLink.created_at)
            ).all()
        )

    def add_link(self, db: Session, source_id: str, payload: NoteLinkCreate) -> OfficialSourceNoteLink:
        self.get(db, source_id)
        current_count = len(self.list_links(db, source_id))
        if current_count >= self.settings.official_audit_max_linked_notes:
            raise official_error("official_note_link_missing", "Official source note link limit exceeded")
        note = db.get(ObsidianNote, payload.obsidian_note_id)
        if note is None or note.index_status == "missing":
            raise official_error("official_note_missing", "Obsidian note is unavailable", status_code=404)
        self._validate_note_path(note)
        heading_paths = self._heading_paths(payload.target_heading_paths)
        link = OfficialSourceNoteLink(
            source_id=source_id,
            obsidian_note_id=note.id,
            target_heading_paths=heading_paths,
            enabled=True,
        )
        db.add(link)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise official_error("official_note_link_missing", "Official source note link already exists", status_code=409) from exc
        db.refresh(link)
        return link

    def delete_link(self, db: Session, source_id: str, link_id: str) -> None:
        link = db.get(OfficialSourceNoteLink, link_id)
        if link is None or link.source_id != source_id:
            raise official_error("official_note_link_missing", "Official source note link was not found", status_code=404)
        db.delete(link)
        db.commit()

    def _validate_note_path(self, note: ObsidianNote) -> None:
        if not self.settings.obsidian_vault_path:
            raise official_error("official_note_outside_vault", "Obsidian Vault is not configured")
        normalized = note.relative_path.replace("\\", "/").strip("/")
        if not normalized.casefold().endswith(".md"):
            raise official_error("official_note_outside_vault", "Only Markdown notes can be linked")
        parts = normalized.split("/")
        directory_parts = {part.casefold() for part in parts[:-1]}
        if directory_parts.intersection(value.casefold() for value in self.settings.obsidian_exclude_dir_list):
            raise official_error("official_note_outside_vault", "Excluded Obsidian notes cannot be linked")
        key = normalized.casefold()
        if any(key == prefix.casefold() or key.startswith(f"{prefix.casefold()}/") for prefix in self.settings.obsidian_exclude_path_list):
            raise official_error("official_note_outside_vault", "Excluded Obsidian notes cannot be linked")
        try:
            scanner = ObsidianVaultScanner(
                self.settings.obsidian_vault_path,
                include_patterns=self.settings.obsidian_include_pattern_list,
                exclude_dirs=self.settings.obsidian_exclude_dir_list,
                exclude_paths=self.settings.obsidian_exclude_path_list,
                max_file_bytes=self.settings.obsidian_max_file_bytes,
            )
            path = scanner.resolve_note_path(normalized)
        except VaultConfigurationError as exc:
            raise official_error("official_note_outside_vault", "Obsidian note path is unsafe") from exc
        if not path.is_file():
            raise official_error("official_note_missing", "Obsidian note file is unavailable", status_code=404)

    def _ensure_enabled(self) -> None:
        if not self.settings.official_sources_enabled:
            raise official_error("official_source_disabled", "Official source features are disabled", status_code=404)

    @staticmethod
    def _selector(value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @staticmethod
    def _heading_paths(value: list[list[str]]) -> list[list[str]]:
        result: list[list[str]] = []
        for path in value:
            if not path or len(path) > 10:
                raise official_error("official_note_link_missing", "Target heading path is invalid")
            cleaned = [part.strip() for part in path]
            if any(not part or len(part) > 200 for part in cleaned):
                raise official_error("official_note_link_missing", "Target heading path is invalid")
            result.append(cleaned)
        return result


class OfficialFetchService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fetcher: OfficialSourceFetcher | None = None,
        normalizer: OfficialContentNormalizer | None = None,
        store: SnapshotStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.sources = OfficialSourceService(self.settings)
        self.fetcher = fetcher or OfficialSourceFetcher(self.settings)
        self.normalizer = normalizer or OfficialContentNormalizer(self.settings)
        self.store = store or SnapshotStore(self.settings)

    def fetch_source(self, db: Session, source_id: str) -> dict:
        source = self.sources.get(db, source_id)
        if not source.enabled:
            raise official_error("official_source_disabled", "Official source is disabled")
        run = AgentRun(
            run_type="official_fetch",
            status="running",
            official_source_id=source.id,
            mode="manual",
            started_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        db.add(run)
        db.commit()
        started = datetime.now(UTC)
        latest = db.scalar(
            select(OfficialSourceSnapshot)
            .where(OfficialSourceSnapshot.source_id == source.id)
            .order_by(OfficialSourceSnapshot.created_at.desc())
            .limit(1)
        )
        try:
            fetched = self.fetcher.fetch(
                source.canonical_url,
                allowed_host=source.allowed_host,
                etag=latest.etag if latest else None,
                last_modified=latest.last_modified if latest else None,
            )
            source.last_checked_at = datetime.now(UTC)
            if fetched.not_modified:
                return self._finish_unchanged(db, source, run, latest, started, http_status=304)
            if fetched.body is None or fetched.content_type is None:
                raise official_error("official_content_empty", "Official source response is empty")
            normalized = self.normalizer.normalize(
                fetched.body,
                content_type=fetched.content_type,
                source_type=source.source_type,
                content_selector=source.content_selector,
            )
            existing = db.scalar(
                select(OfficialSourceSnapshot).where(
                    OfficialSourceSnapshot.source_id == source.id,
                    OfficialSourceSnapshot.content_hash == normalized.content_hash,
                )
            )
            if existing:
                return self._finish_unchanged(db, source, run, existing, started, http_status=fetched.status_code)
            snapshot_id = str(uuid4())
            storage_path = self.store.save(source.id, snapshot_id, normalized.text)
            snapshot = OfficialSourceSnapshot(
                id=snapshot_id,
                source_id=source.id,
                content_hash=normalized.content_hash,
                storage_path=storage_path,
                normalized_format=normalized.normalized_format,
                normalizer_version=NORMALIZER_VERSION,
                http_status=fetched.status_code,
                content_type=fetched.content_type,
                size_bytes=len(fetched.body),
                etag=fetched.etag,
                last_modified=fetched.last_modified,
                section_manifest=normalized.section_manifest,
            )
            db.add(snapshot)
            source.status = "active"
            source.last_success_at = datetime.now(UTC)
            source.last_error_code = None
            source.last_error_preview = None
            self._finish_run(run, started, changed=True)
            db.commit()
            return self._result(run, snapshot, changed=True, http_status=fetched.status_code, started=started)
        except OfficialSourceError as exc:
            db.rollback()
            source = db.get(OfficialSource, source_id)
            run = db.get(AgentRun, run.id)
            if source:
                source.last_checked_at = datetime.now(UTC)
                source.status = "unavailable" if exc.code in {"official_fetch_timeout", "official_fetch_failed"} else "error"
                source.last_error_code = exc.code
                source.last_error_preview = safe_text_preview(exc, 500)
            if run:
                run.status = "failed"
                run.error_code = exc.code
                run.error_message_preview = safe_text_preview(exc, 500)
                self._finish_run(run, started, changed=None)
            db.commit()
            raise
        except Exception as exc:
            db.rollback()
            source = db.get(OfficialSource, source_id)
            run = db.get(AgentRun, run.id)
            if source:
                source.last_checked_at = datetime.now(UTC)
                source.status = "error"
                source.last_error_code = "official_fetch_failed"
                source.last_error_preview = safe_text_preview(type(exc).__name__, 500)
            if run:
                run.status = "failed"
                run.error_code = "official_fetch_failed"
                run.error_message_preview = safe_text_preview(type(exc).__name__, 500)
                self._finish_run(run, started, changed=None)
            db.commit()
            raise official_error("official_fetch_failed", "Official source fetch failed", status_code=502) from exc

    def list_snapshots(self, db: Session, source_id: str) -> list[OfficialSourceSnapshot]:
        self.sources.get(db, source_id)
        return list(
            db.scalars(
                select(OfficialSourceSnapshot)
                .where(OfficialSourceSnapshot.source_id == source_id)
                .order_by(OfficialSourceSnapshot.created_at.desc())
            ).all()
        )

    def get_snapshot(self, db: Session, snapshot_id: str) -> OfficialSourceSnapshot:
        snapshot = db.get(OfficialSourceSnapshot, snapshot_id)
        if snapshot is None:
            raise official_error("official_source_not_found", "Official snapshot was not found", status_code=404)
        return snapshot

    def preview(self, db: Session, snapshot_id: str, max_chars: int = 4000) -> str:
        snapshot = self.get_snapshot(db, snapshot_id)
        return self.store.preview(snapshot.storage_path, min(max(1, max_chars), 10_000))

    def _finish_unchanged(
        self,
        db: Session,
        source: OfficialSource,
        run: AgentRun,
        snapshot: OfficialSourceSnapshot | None,
        started: datetime,
        *,
        http_status: int,
    ) -> dict:
        source.status = "active"
        source.last_success_at = datetime.now(UTC)
        source.last_error_code = None
        source.last_error_preview = None
        self._finish_run(run, started, changed=False)
        db.commit()
        return self._result(run, snapshot, changed=False, http_status=http_status, started=started)

    @staticmethod
    def _finish_run(run: AgentRun, started: datetime, *, changed: bool | None) -> None:
        finished = datetime.now(UTC)
        if run.status == "running":
            run.status = "succeeded"
        run.official_snapshot_changed = changed
        run.finished_at = finished
        run.duration_ms = max(0, int((finished - started).total_seconds() * 1000))

    @staticmethod
    def _result(
        run: AgentRun,
        snapshot: OfficialSourceSnapshot | None,
        *,
        changed: bool,
        http_status: int,
        started: datetime,
    ) -> dict:
        return {
            "run_id": run.id,
            "changed": changed,
            "snapshot_id": snapshot.id if snapshot else None,
            "content_hash": snapshot.content_hash if snapshot else None,
            "http_status": http_status,
            "duration": max(0, int((datetime.now(UTC) - started).total_seconds() * 1000)),
        }
