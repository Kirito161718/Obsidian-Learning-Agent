from __future__ import annotations

import re
import time
from typing import Any

from qdrant_client import QdrantClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import ObsidianNote
from app.obsidian.markdown_chunker_v2 import MarkdownStructuralV2Chunker
from app.obsidian.markdown_parser import MarkdownParser
from app.obsidian.scanner import ObsidianVaultScanner
from app.rag_evaluation.chunk_analysis import analyze_chunk_payloads
from app.services.embedding_service import EmbeddingService, get_embedding_service
from app.services.vector_service import VectorService, qdrant_transport_kwargs


class ShadowCollectionService:
    """Build a V2 index without mutating the Vault, MySQL notes, or production Qdrant."""

    def __init__(
        self,
        settings: Settings | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedding_service = embedding_service

    def default_name(self, dataset_hash: str) -> str:
        suffix = re.sub(r"[^a-z0-9_]+", "_", dataset_hash.casefold())[:12]
        return f"{self.settings.rag_evaluation_collection_prefix}7c_chunk_v2_{suffix}"

    def prepare(
        self,
        db: Session,
        *,
        collection_name: str,
        replace: bool = False,
    ) -> dict[str, Any]:
        self._validate_collection_name(collection_name)
        client = self._client()
        existing = {
            item.name for item in client.get_collections().collections
        }
        if collection_name in existing:
            if not replace:
                raise ValueError("Shadow collection already exists; use replace explicitly")
            client.delete_collection(collection_name=collection_name)
            for _ in range(40):
                names = {
                    item.name for item in client.get_collections().collections
                }
                if collection_name not in names:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Timed out waiting for shadow collection deletion")

        embedding = self.embedding_service or get_embedding_service()
        vector = VectorService(
            vector_size=embedding.vector_size,
            ensure_collection=True,
            collection_name=collection_name,
        )
        scanner = ObsidianVaultScanner(
            self.settings.obsidian_vault_path,
            include_patterns=self.settings.obsidian_include_pattern_list,
            exclude_dirs=self.settings.obsidian_exclude_dir_list,
            exclude_paths=self.settings.obsidian_exclude_path_list,
            max_file_bytes=self.settings.obsidian_max_file_bytes,
        )
        notes = (
            db.query(ObsidianNote)
            .filter(ObsidianNote.index_status == "indexed")
            .order_by(ObsidianNote.relative_path)
            .all()
        )
        scan = scanner.scan({note.relative_path: note for note in notes})
        safe_files = {
            item.relative_path.casefold(): item
            for item in scan.items
            if item.kind == "unchanged" and item.vault_file is not None
        }
        parser = MarkdownParser()
        chunker = MarkdownStructuralV2Chunker(
            min_chars=self.settings.obsidian_chunk_min_chars,
            target_chars=self.settings.obsidian_chunk_target_chars,
            max_chars=self.settings.obsidian_chunk_max_chars,
            hard_max_chars=self.settings.obsidian_atomic_block_hard_max_chars,
        )
        indexed_notes = 0
        try:
            for note in notes:
                item = safe_files.get(note.relative_path.casefold())
                if (
                    item is None
                    or item.observed_hash != note.indexed_hash
                    or item.vault_file is None
                ):
                    raise ValueError(
                        f"Vault/index drift prevents shadow build: {note.relative_path}"
                    )
                parsed = parser.parse_file(
                    item.vault_file.absolute_path,
                    note.relative_path,
                )
                chunks = chunker.chunk(
                    parsed,
                    note_id=note.id,
                    note_hash=note.indexed_hash or item.observed_hash or "",
                )
                embeddings = embedding.embed_texts(
                    [chunk.text for chunk in chunks]
                )
                vector.upsert_obsidian_chunks(
                    chunks=chunks,
                    embeddings=embeddings,
                )
                indexed_notes += 1
            payloads = vector.list_all_obsidian_chunks(refresh=True)
            return {
                "collection_name": collection_name,
                "indexed_notes": indexed_notes,
                "statistics": analyze_chunk_payloads(
                    payloads,
                    min_chars=self.settings.obsidian_chunk_min_chars,
                    max_chars=self.settings.obsidian_chunk_max_chars,
                ),
            }
        except Exception:
            if collection_name in {
                item.name for item in client.get_collections().collections
            }:
                client.delete_collection(collection_name=collection_name)
            raise

    def analyze(self, *, collection_name: str) -> dict[str, Any]:
        self._validate_read_collection_name(collection_name)
        vector = VectorService(
            ensure_collection=False,
            collection_name=collection_name,
        )
        return analyze_chunk_payloads(
            vector.list_all_obsidian_chunks(refresh=True),
            min_chars=self.settings.obsidian_chunk_min_chars,
            max_chars=self.settings.obsidian_chunk_max_chars,
        )

    def _validate_collection_name(self, collection_name: str) -> None:
        if (
            not collection_name.startswith(
                self.settings.rag_evaluation_collection_prefix
            )
            or collection_name == self.settings.qdrant_collection
        ):
            raise ValueError("Shadow collection must use the evaluation prefix")

    def _validate_read_collection_name(self, collection_name: str) -> None:
        if collection_name != self.settings.qdrant_collection:
            self._validate_collection_name(collection_name)

    def _client(self) -> QdrantClient:
        return QdrantClient(
            url=self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key or None,
            timeout=30,
            prefer_grpc=self.settings.qdrant_prefer_grpc,
            grpc_port=self.settings.qdrant_grpc_port,
            **qdrant_transport_kwargs(self.settings.qdrant_url),
        )
