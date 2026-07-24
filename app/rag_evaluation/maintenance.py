from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, Settings, get_settings
from app.db.models import RagEvalExperiment, RagRetrievalProfile
from app.services.vector_service import qdrant_transport_kwargs


class EvaluationMaintenanceService:
    def __init__(
        self,
        settings: Settings | None = None,
        qdrant_client: QdrantClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = qdrant_client

    def cleanup(self, db: Session, *, dry_run: bool) -> dict[str, Any]:
        protected_collections = {self.settings.qdrant_collection}
        protected_collections.update(
            value
            for (value,) in db.query(RagRetrievalProfile.collection_name)
            .filter(
                (RagRetrievalProfile.is_active.is_(True))
                | (RagRetrievalProfile.is_baseline.is_(True))
            )
            .all()
            if value
        )
        client = self.client or QdrantClient(
            url=self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key or None,
            timeout=30,
            prefer_grpc=self.settings.qdrant_prefer_grpc,
            grpc_port=self.settings.qdrant_grpc_port,
            **qdrant_transport_kwargs(self.settings.qdrant_url),
        )
        collections = sorted(
            collection.name
            for collection in client.get_collections().collections
            if collection.name.startswith(
                self.settings.rag_evaluation_collection_prefix
            )
            and collection.name not in protected_collections
        )
        removable_collections = collections[
            : max(0, len(collections) - self.settings.rag_evaluation_keep_collections)
        ]
        referenced_experiments = {
            value
            for (value,) in db.query(RagEvalExperiment.id)
            .filter(RagEvalExperiment.status.in_(["running", "cancel_requested"]))
            .all()
        }
        artifact_roots = []
        for base in (
            self.settings.rag_evaluation_artifact_dir,
            self.settings.temp_dir / "rag_evaluation",
            self.settings.cache_dir / "rag_evaluation",
        ):
            if base.exists():
                artifact_roots.extend(
                    child
                    for child in base.iterdir()
                    if child.is_dir() and child.name not in referenced_experiments
                )
        result = {
            "dry_run": dry_run,
            "collections": removable_collections,
            "artifact_directories": len(artifact_roots),
        }
        if dry_run:
            return result
        for collection in removable_collections:
            client.delete_collection(collection_name=collection)
        for path in artifact_roots:
            self._safe_remove(path)
        return result

    @staticmethod
    def _safe_remove(path: Path) -> None:
        resolved = path.resolve(strict=False)
        resolved.relative_to(BASE_DIR)
        shutil.rmtree(resolved)
