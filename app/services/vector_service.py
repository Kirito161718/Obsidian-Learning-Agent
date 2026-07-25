import time
import logging
from collections.abc import Callable
from functools import lru_cache
from threading import Lock
from typing import TypeVar
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from app.core.config import get_settings
from app.obsidian.models import ObsidianChunk
from app.services.chunk_service import Chunk

T = TypeVar("T")
logger = logging.getLogger(__name__)
_client_lock = Lock()
_created_clients: list[QdrantClient] = []
_client_registry: dict[tuple, QdrantClient] = {}


def qdrant_transport_kwargs(url: str) -> dict[str, bool]:
    """Keep loopback Qdrant traffic out of OS-level HTTP proxies."""
    host = (urlparse(url).hostname or "").casefold()
    return {"trust_env": False} if host in {"127.0.0.1", "::1", "localhost"} else {}


@lru_cache
def _cached_qdrant_client(
    client_type: type,
    url: str,
    api_key: str | None,
    prefer_grpc: bool,
    grpc_port: int,
) -> QdrantClient:
    key = (client_type, url, api_key, prefer_grpc, grpc_port)
    with _client_lock:
        existing = _client_registry.get(key)
        if existing is not None:
            return existing
        client = client_type(
            url=url,
            api_key=api_key or None,
            timeout=30,
            prefer_grpc=prefer_grpc,
            grpc_port=grpc_port,
            **qdrant_transport_kwargs(url),
        )
        _client_registry[key] = client
        _created_clients.append(client)
    return client


def get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    return _cached_qdrant_client(
        QdrantClient,
        settings.qdrant_url,
        settings.qdrant_api_key or None,
        settings.qdrant_prefer_grpc,
        settings.qdrant_grpc_port,
    )


def qdrant_client_cache_size() -> int:
    return _cached_qdrant_client.cache_info().currsize


def close_qdrant_clients() -> None:
    with _client_lock:
        clients = list(_created_clients)
        _created_clients.clear()
        _client_registry.clear()
    for client in clients:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.warning("Failed to close Qdrant client", exc_info=True)
    _cached_qdrant_client.cache_clear()


class VectorService:
    def __init__(
        self,
        vector_size: int | None = None,
        ensure_collection: bool = True,
        collection_name: str | None = None,
        client: QdrantClient | None = None,
    ) -> None:
        settings = get_settings()
        self.collection_name = collection_name or settings.qdrant_collection
        self.client = client or get_qdrant_client()
        self.vector_size = vector_size
        self._obsidian_payload_cache: list[dict] | None = None
        if ensure_collection:
            if vector_size is None:
                raise ValueError("vector_size is required when ensuring a Qdrant collection")
            self.ensure_collection()

    def _with_retry(self, operation: Callable[[], T], attempts: int = 3) -> T:
        last_exc: Exception | None = None
        for index in range(attempts):
            try:
                return operation()
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Qdrant operation failed: attempt=%s/%s, error_type=%s, message=%s",
                    index + 1,
                    attempts,
                    type(exc).__name__,
                    str(exc)[:300],
                )
                if index == attempts - 1:
                    break
                time.sleep(1.5 * (index + 1))
        raise last_exc or RuntimeError("Qdrant operation failed")

    def ensure_collection(self) -> None:
        collections = self._with_retry(lambda: self.client.get_collections()).collections
        exists = any(collection.name == self.collection_name for collection in collections)
        if not exists:
            self._with_retry(
                lambda: self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
                )
            )

    def delete_by_document_id(self, document_id: str) -> None:
        query_filter = Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        )
        self._with_retry(
            lambda: self.client.delete(
                collection_name=self.collection_name,
                points_selector=query_filter,
                wait=True,
            )
        )

    def delete_obsidian_version(self, note_id: str, note_hash: str | None = None) -> None:
        conditions = [
            FieldCondition(key="source_type", match=MatchValue(value="obsidian_note")),
            FieldCondition(key="source_id", match=MatchValue(value=note_id)),
        ]
        if note_hash:
            conditions.append(FieldCondition(key="note_hash", match=MatchValue(value=note_hash)))
        query_filter = Filter(must=conditions)
        self._with_retry(
            lambda: self.client.delete(
                collection_name=self.collection_name,
                points_selector=query_filter,
                wait=True,
            )
        )
        self._obsidian_payload_cache = None

    def list_obsidian_chunks(self, note_id: str, note_hash: str | None = None) -> list[dict]:
        conditions = [
            FieldCondition(key="source_type", match=MatchValue(value="obsidian_note")),
            FieldCondition(key="source_id", match=MatchValue(value=note_id)),
        ]
        if note_hash:
            conditions.append(FieldCondition(key="note_hash", match=MatchValue(value=note_hash)))
        query_filter = Filter(must=conditions)
        chunks: list[dict] = []
        offset = None
        while True:
            points, offset = self._with_retry(
                lambda: self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=query_filter,
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            )
            chunks.extend(point.payload or {} for point in points)
            if offset is None:
                break
        chunks.sort(key=lambda payload: int(payload.get("chunk_index", 0)))
        return chunks

    def list_all_obsidian_chunks(self, *, refresh: bool = False) -> list[dict]:
        """Return a collection-local payload catalog used by bounded expansions."""
        if self._obsidian_payload_cache is not None and not refresh:
            return [dict(item) for item in self._obsidian_payload_cache]
        query_filter = Filter(
            must=[FieldCondition(key="source_type", match=MatchValue(value="obsidian_note"))]
        )
        chunks: list[dict] = []
        offset = None
        while True:
            points, offset = self._with_retry(
                lambda: self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=query_filter,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            )
            chunks.extend(dict(point.payload or {}) for point in points)
            if offset is None:
                break
        chunks.sort(
            key=lambda payload: (
                str(payload.get("source_id") or ""),
                int(payload.get("chunk_index") or 0),
            )
        )
        self._obsidian_payload_cache = chunks
        return [dict(item) for item in chunks]

    def list_chunks_by_document_id(self, document_id: str) -> list[dict]:
        query_filter = Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        )
        chunks: list[dict] = []
        offset = None
        while True:
            points, offset = self._with_retry(
                lambda: self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=query_filter,
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            )
            chunks.extend(point.payload or {} for point in points)
            if offset is None:
                break

        chunks.sort(key=lambda payload: int(payload.get("chunk_index", 0)))
        logger.info("Qdrant chunks listed: document_id=%s, returned_count=%s", document_id, len(chunks))
        return chunks

    def upsert_chunks(
        self,
        *,
        document_id: str,
        filename: str,
        file_type: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> None:
        points: list[PointStruct] = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            chunk_id = f"{document_id}_{chunk.chunk_index}"
            points.append(
                PointStruct(
                    id=str(uuid5(NAMESPACE_URL, chunk_id)),
                    vector=embedding,
                    payload={
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "source_id": document_id,
                        "source_type": "uploaded_document",
                        "filename": filename,
                        "source_name": filename,
                        "file_type": file_type,
                        "page_no": chunk.page_no,
                        "section_title": chunk.section_title,
                        "chunk_index": chunk.chunk_index,
                        "text": chunk.text,
                    },
                )
            )
        if points:
            logger.info("Qdrant upsert requested: document_id=%s, point_count=%s", document_id, len(points))
            self._with_retry(lambda: self.client.upsert(collection_name=self.collection_name, points=points))

    def upsert_obsidian_chunks(
        self,
        *,
        chunks: list[ObsidianChunk],
        embeddings: list[list[float]],
    ) -> None:
        points: list[PointStruct] = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            points.append(
                PointStruct(
                    id=chunk.chunk_id,
                    vector=embedding,
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "document_id": None,
                        "source_id": chunk.note_id,
                        "source_type": "obsidian_note",
                        "filename": chunk.file_name,
                        "source_name": chunk.title,
                        "source_path": chunk.relative_path,
                        "file_type": "md",
                        "note_hash": chunk.note_hash,
                        "heading_path": list(chunk.heading_path),
                        "heading_level": chunk.heading_level,
                        "section_title": chunk.section_title,
                        "line_start": chunk.line_start,
                        "line_end": chunk.line_end,
                        "block_types": list(chunk.block_types),
                        "content_hash": chunk.content_hash,
                        "parent_section_id": chunk.parent_section_id,
                        "chunking_version": chunk.chunking_version,
                        "structural_id": chunk.structural_id,
                        "previous_sibling_id": chunk.previous_sibling_id,
                        "next_sibling_id": chunk.next_sibling_id,
                        "chunk_metadata": dict(chunk.metadata),
                        "tags": list(chunk.tags),
                        "links": list(chunk.links),
                        "chunk_index": chunk.chunk_index,
                        "text": chunk.text,
                    },
                )
            )
        if points:
            logger.info("Qdrant Obsidian upsert requested: note_id=%s, point_count=%s", chunks[0].note_id, len(points))
            self._with_retry(
                lambda: self.client.upsert(collection_name=self.collection_name, points=points, wait=True)
            )
            self._obsidian_payload_cache = None

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        document_id: str | None = None,
        source_types: list[str] | None = None,
        source_path_prefixes: list[str] | None = None,
    ) -> list[dict]:
        query_filter = None
        if document_id:
            query_filter = Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            )
        elif source_types == ["obsidian_note"]:
            query_filter = Filter(
                must=[FieldCondition(key="source_type", match=MatchValue(value="obsidian_note"))]
            )

        needs_post_filter = bool(source_types or source_path_prefixes)
        query_limit = min(max(top_k * 10, 50), 500) if needs_post_filter else top_k

        if hasattr(self.client, "query_points"):
            result = self._with_retry(
                lambda: self.client.query_points(
                    collection_name=self.collection_name,
                    query=query_vector,
                    limit=query_limit,
                    query_filter=query_filter,
                    with_payload=True,
                )
            )
            hits = result.points
        else:
            # 需要根据当前官方文档核验：旧版 qdrant-client 使用 search，新版使用 query_points。
            hits = self._with_retry(
                lambda: self.client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    limit=query_limit,
                    query_filter=query_filter,
                    with_payload=True,
                )
            )

        results = [
            {
                "score": float(hit.score),
                "payload": hit.payload or {},
            }
            for hit in hits
        ]
        if needs_post_filter:
            results = [
                item
                for item in results
                if self._payload_matches_scope(item["payload"], source_types, source_path_prefixes)
            ][:top_k]
        logger.info("Qdrant search completed: requested_top_k=%s, returned_count=%s", top_k, len(results))
        return results

    @staticmethod
    def _payload_matches_scope(
        payload: dict,
        source_types: list[str] | None,
        source_path_prefixes: list[str] | None,
    ) -> bool:
        source_type = str(payload.get("source_type") or "uploaded_document")
        if source_types and source_type not in source_types:
            return False
        if source_path_prefixes:
            if source_type != "obsidian_note":
                return False
            source_path = str(payload.get("source_path") or "").replace("\\", "/").strip("/")
            if not any(
                source_path == prefix or source_path.startswith(f"{prefix}/")
                for prefix in source_path_prefixes
            ):
                return False
        return True
