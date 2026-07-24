from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import LearningSession, ObsidianNote, QAHistory
from app.obsidian.scanner import ObsidianVaultScanner
from app.services.vector_service import VectorService


class SafetyAuditService:
    """Read-only aggregate stability snapshot without note paths or text."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def snapshot(
        self,
        db: Session,
        *,
        vector_service: VectorService | None = None,
    ) -> dict[str, Any]:
        scanner = ObsidianVaultScanner(
            self.settings.obsidian_vault_path,
            include_patterns=self.settings.obsidian_include_pattern_list,
            exclude_dirs=self.settings.obsidian_exclude_dir_list,
            exclude_paths=self.settings.obsidian_exclude_path_list,
            max_file_bytes=self.settings.obsidian_max_file_bytes,
        )
        report = scanner.scan()
        files = sorted(
            (
                item.relative_path,
                item.observed_hash,
                item.vault_file.size_bytes,
            )
            for item in report.items
            if item.vault_file is not None and item.observed_hash
        )
        digest = hashlib.sha256()
        for relative_path, observed_hash, _ in files:
            digest.update(relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(observed_hash).encode("ascii"))
            digest.update(b"\n")
        vector = vector_service or VectorService(
            ensure_collection=False,
            collection_name=self.settings.qdrant_collection,
        )
        point_count = int(
            vector.client.count(
                collection_name=self.settings.qdrant_collection,
                exact=True,
            ).count
        )
        return {
            "vault_markdown_count": len(files),
            "vault_total_bytes": sum(size for _, _, size in files),
            "vault_aggregate_hash": digest.hexdigest(),
            "obsidian_notes_count": db.query(ObsidianNote).count(),
            "qdrant_collection": self.settings.qdrant_collection,
            "qdrant_point_count": point_count,
            "qa_history_count": db.query(QAHistory).count(),
            "learning_session_count": db.query(LearningSession).count(),
        }
