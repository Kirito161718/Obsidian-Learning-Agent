from types import SimpleNamespace

from app.services import vector_service
from app.services.vector_service import VectorService


def test_vector_service_prefers_qdrant_grpc(monkeypatch):
    captured: dict = {}

    class FakeQdrantClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get_collections(self):
            return SimpleNamespace(collections=[])

        def create_collection(self, **kwargs):
            return None

    monkeypatch.setattr(vector_service, "QdrantClient", FakeQdrantClient)
    monkeypatch.setattr(
        vector_service,
        "get_settings",
        lambda: SimpleNamespace(
            qdrant_collection="test_chunks",
            qdrant_url="http://127.0.0.1:6333",
            qdrant_api_key="",
            qdrant_prefer_grpc=True,
            qdrant_grpc_port=6334,
        ),
    )

    VectorService(vector_size=3)

    assert captured["prefer_grpc"] is True
    assert captured["grpc_port"] == 6334


def test_vector_service_deletes_by_document_id(monkeypatch):
    captured: dict = {}

    class FakeQdrantClient:
        def __init__(self, **kwargs):
            return None

        def delete(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(vector_service, "QdrantClient", FakeQdrantClient)
    monkeypatch.setattr(
        vector_service,
        "get_settings",
        lambda: SimpleNamespace(
            qdrant_collection="test_chunks",
            qdrant_url="http://127.0.0.1:6333",
            qdrant_api_key="",
            qdrant_prefer_grpc=True,
            qdrant_grpc_port=6334,
        ),
    )

    VectorService(ensure_collection=False).delete_by_document_id("doc-1")

    assert captured["collection_name"] == "test_chunks"
    assert captured["wait"] is True
    conditions = captured["points_selector"].must
    assert conditions[0].key == "document_id"
    assert conditions[0].match.value == "doc-1"


def test_vector_service_lists_chunks_by_document_id(monkeypatch):
    captured: dict = {}

    class FakeQdrantClient:
        def __init__(self, **kwargs):
            return None

        def scroll(self, **kwargs):
            captured.update(kwargs)
            return (
                [
                    SimpleNamespace(payload={"document_id": "doc-1", "chunk_index": 2, "text": "second"}),
                    SimpleNamespace(payload={"document_id": "doc-1", "chunk_index": 1, "text": "first"}),
                ],
                None,
            )

    monkeypatch.setattr(vector_service, "QdrantClient", FakeQdrantClient)
    monkeypatch.setattr(
        vector_service,
        "get_settings",
        lambda: SimpleNamespace(
            qdrant_collection="test_chunks",
            qdrant_url="http://127.0.0.1:6333",
            qdrant_api_key="",
            qdrant_prefer_grpc=True,
            qdrant_grpc_port=6334,
        ),
    )

    chunks = VectorService(ensure_collection=False).list_chunks_by_document_id("doc-1")

    assert captured["collection_name"] == "test_chunks"
    assert captured["with_payload"] is True
    assert captured["with_vectors"] is False
    conditions = captured["scroll_filter"].must
    assert conditions[0].key == "document_id"
    assert conditions[0].match.value == "doc-1"
    assert [chunk["chunk_index"] for chunk in chunks] == [1, 2]
