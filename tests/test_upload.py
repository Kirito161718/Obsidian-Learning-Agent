from pathlib import Path

from app.db.models import Document
from app.db.database import get_db
from app.main import app
from app.routers import documents
from app.services.chunk_service import Chunk
from app.services.parser_service import ParsedSection


class FakeParser:
    def parse(self, file_path, file_type):
        return [ParsedSection(text="企业知识库支持上传文档并进行问答。", page_no=1)]


class FakeChunker:
    def split(self, sections):
        return [Chunk(text="企业知识库支持上传文档并进行问答。", page_no=1, section_title=None, chunk_index=0)]


class FakeEmbedding:
    vector_size = 3

    def embed_texts(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeVector:
    def __init__(self, vector_size):
        self.vector_size = vector_size

    def upsert_chunks(self, **kwargs):
        return None


def test_upload_document(client, monkeypatch, db_session):
    monkeypatch.setattr(documents.get_settings(), "upload_dir", Path("uploads"))
    monkeypatch.setattr(documents, "ParserService", lambda: FakeParser())
    monkeypatch.setattr(documents, "ChunkService", lambda: FakeChunker())
    monkeypatch.setattr(documents, "get_embedding_service", lambda: FakeEmbedding())
    monkeypatch.setattr(documents, "VectorService", FakeVector)

    response = client.post(
        "/documents/upload",
        files={"file": ("demo.txt", b"hello rag", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "demo.txt"
    assert body["status"] == "processing"
    assert body["chunk_count"] == 0
    assert body["document_id"]

    document = db_session.get(Document, body["document_id"])
    assert document is not None
    assert document.status == "completed"
    assert document.chunk_count == 1
    assert document.file_hash


def test_upload_duplicate_document_returns_existing_document(client, monkeypatch, db_session):
    monkeypatch.setattr(documents.get_settings(), "upload_dir", Path("uploads"))
    monkeypatch.setattr(documents, "ParserService", lambda: FakeParser())
    monkeypatch.setattr(documents, "ChunkService", lambda: FakeChunker())
    monkeypatch.setattr(documents, "get_embedding_service", lambda: FakeEmbedding())
    monkeypatch.setattr(documents, "VectorService", FakeVector)

    first = client.post(
        "/documents/upload",
        files={"file": ("demo.txt", b"same file", "text/plain")},
    )
    second = client.post(
        "/documents/upload",
        files={"file": ("copy.txt", b"same file", "text/plain")},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert second_body["status"] == "duplicate"
    assert second_body["document_id"] == first_body["document_id"]
    assert second_body["message"] == "same file already exists"
    assert db_session.query(Document).count() == 1


def test_upload_allows_same_file_after_soft_delete(client, monkeypatch, db_session):
    monkeypatch.setattr(documents.get_settings(), "upload_dir", Path("uploads"))
    monkeypatch.setattr(documents, "ParserService", lambda: FakeParser())
    monkeypatch.setattr(documents, "ChunkService", lambda: FakeChunker())
    monkeypatch.setattr(documents, "get_embedding_service", lambda: FakeEmbedding())
    monkeypatch.setattr(documents, "VectorService", FakeVector)
    monkeypatch.setattr(documents, "delete_document_vectors", lambda document_id: None)

    first = client.post(
        "/documents/upload",
        files={"file": ("demo.txt", b"same deleted file", "text/plain")},
    )
    first_id = first.json()["document_id"]
    delete_response = client.delete(f"/documents/{first_id}")
    second = client.post(
        "/documents/upload",
        files={"file": ("demo.txt", b"same deleted file", "text/plain")},
    )

    assert delete_response.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "processing"
    assert second.json()["document_id"] != first_id
    assert db_session.query(Document).count() == 2


def test_upload_document_returns_503_when_database_is_unavailable(client, monkeypatch):
    class FailingSession:
        def query(self, model):
            from sqlalchemy.exc import OperationalError

            raise OperationalError("select", {}, Exception("database unavailable"))

        def add(self, document):
            return None

        def commit(self):
            from sqlalchemy.exc import OperationalError

            raise OperationalError("insert", {}, Exception("database unavailable"))

        def rollback(self):
            return None

        def refresh(self, document):
            return None

        def close(self):
            return None

    def override_get_db():
        session = FailingSession()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(documents.get_settings(), "upload_dir", Path("uploads"))
    app.dependency_overrides[get_db] = override_get_db

    response = client.post(
        "/documents/upload",
        files={"file": ("demo.txt", b"hello rag", "text/plain")},
    )

    assert response.status_code == 503
    assert "数据库暂时不可用" in response.json()["detail"]
