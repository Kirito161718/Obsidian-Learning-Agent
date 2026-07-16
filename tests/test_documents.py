from pathlib import Path
from uuid import uuid4

from app.db.models import Document
from app.routers import documents


def make_test_dir(name: str) -> Path:
    path = Path("test_artifacts") / f"{name}_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_list_documents(client, db_session):
    db_session.add(
        Document(
            filename="done.txt",
            file_type="txt",
            file_path="uploads/done.txt",
            chunk_count=2,
            status="completed",
        )
    )
    db_session.add(
        Document(
            filename="pending.txt",
            file_type="txt",
            file_path="uploads/pending.txt",
            chunk_count=0,
            status="processing",
        )
    )
    db_session.add(
        Document(
            filename="deleted.txt",
            file_type="txt",
            file_path="uploads/deleted.txt",
            chunk_count=0,
            status="deleted",
        )
    )
    db_session.commit()

    response = client.get("/documents")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {item["filename"] for item in body} == {"done.txt", "pending.txt"}


def test_list_documents_filters_by_status(client, db_session):
    db_session.add(
        Document(
            filename="done.txt",
            file_type="txt",
            file_path="uploads/done.txt",
            chunk_count=2,
            status="completed",
        )
    )
    db_session.add(
        Document(
            filename="failed.txt",
            file_type="txt",
            file_path="uploads/failed.txt",
            chunk_count=0,
            status="failed",
        )
    )
    db_session.commit()

    response = client.get("/documents", params={"status": "completed"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["filename"] == "done.txt"
    assert body[0]["status"] == "completed"


def test_list_documents_can_filter_deleted(client, db_session):
    db_session.add(
        Document(
            filename="deleted.txt",
            file_type="txt",
            file_path="uploads/deleted.txt",
            chunk_count=0,
            status="deleted",
        )
    )
    db_session.commit()

    response = client.get("/documents", params={"status": "deleted"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["filename"] == "deleted.txt"
    assert body[0]["status"] == "deleted"


def test_get_document(client, db_session):
    document = Document(
        filename="demo.txt",
        file_type="txt",
        file_path="uploads/demo.txt",
        chunk_count=1,
        status="completed",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    response = client.get(f"/documents/{document.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == document.id
    assert body["filename"] == "demo.txt"
    assert body["status"] == "completed"


def test_get_document_not_found(client):
    response = client.get("/documents/missing-id")

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_list_document_chunks_not_found(client):
    response = client.get("/documents/missing-id/chunks")

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_list_document_chunks_returns_qdrant_payloads(client, db_session, monkeypatch):
    document = Document(
        filename="demo.txt",
        file_type="txt",
        file_path="uploads/demo.txt",
        chunk_count=2,
        status="completed",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    class FakeVectorService:
        def __init__(self, ensure_collection=True, vector_size=None):
            return None

        def list_chunks_by_document_id(self, document_id):
            assert document_id == document.id
            return [
                {
                    "chunk_id": f"{document.id}_0",
                    "chunk_index": 0,
                    "page_no": 1,
                    "section_title": "Intro",
                    "text": "first chunk",
                    "file_path": "should-not-leak",
                },
                {
                    "chunk_id": f"{document.id}_1",
                    "chunk_index": 1,
                    "page_no": 2,
                    "section_title": "Body",
                    "text": "second chunk",
                },
            ]

    monkeypatch.setattr(documents, "VectorService", FakeVectorService)

    response = client.get(f"/documents/{document.id}/chunks")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == document.id
    assert body["filename"] == "demo.txt"
    assert body["status"] == "completed"
    assert body["chunk_count"] == 2
    assert body["chunks"][0] == {
        "chunk_id": f"{document.id}_0",
        "chunk_index": 0,
        "page_no": 1,
        "section_title": "Intro",
        "text": "first chunk",
    }
    assert "file_path" not in body["chunks"][0]


def test_list_document_chunks_deleted_returns_400(client, db_session):
    document = Document(
        filename="deleted.txt",
        file_type="txt",
        file_path="uploads/deleted.txt",
        chunk_count=0,
        status="deleted",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    response = client.get(f"/documents/{document.id}/chunks")

    assert response.status_code == 400
    assert response.json()["detail"] == "Inactive document chunks are not available"


def test_delete_document_not_found(client):
    response = client.delete("/documents/missing-id")

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_delete_document_marks_status_deleted(client, db_session, monkeypatch):
    deleted_vectors: list[str] = []
    document = Document(
        filename="demo.txt",
        file_type="txt",
        file_path="uploads/demo.txt",
        chunk_count=1,
        status="completed",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    monkeypatch.setattr(documents, "delete_document_vectors", lambda document_id: deleted_vectors.append(document_id))

    response = client.delete(f"/documents/{document.id}")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": document.id,
        "status": "deleted",
        "message": "document soft deleted successfully",
    }
    assert deleted_vectors == [document.id]
    db_session.refresh(document)
    assert document.status == "deleted"
    assert document.deleted_at is not None


def test_delete_document_soft_delete_keeps_uploaded_file(client, db_session, monkeypatch):
    deleted_vectors: list[str] = []
    base_dir = make_test_dir("delete_uploaded")
    upload_dir = base_dir / "uploads"
    upload_dir.mkdir()
    saved_file = upload_dir / "stored-demo.txt"
    saved_file.write_text("demo", encoding="utf-8")
    document = Document(
        filename="demo.txt",
        file_type="txt",
        file_path=str(saved_file),
        chunk_count=1,
        status="completed",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    monkeypatch.setattr(documents.get_settings(), "upload_dir", upload_dir)
    monkeypatch.setattr(documents, "delete_document_vectors", lambda document_id: deleted_vectors.append(document_id))

    response = client.delete(f"/documents/{document.id}")

    assert response.status_code == 200
    assert deleted_vectors == [document.id]
    assert saved_file.exists()
    db_session.refresh(document)
    assert document.status == "deleted"
    assert document.deleted_at is not None


def test_delete_document_does_not_remove_file_outside_upload_dir(client, db_session, monkeypatch):
    base_dir = make_test_dir("outside_upload")
    upload_dir = base_dir / "uploads"
    outside_dir = base_dir / "outside"
    upload_dir.mkdir()
    outside_dir.mkdir()
    outside_file = outside_dir / "do-not-delete.txt"
    outside_file.write_text("keep", encoding="utf-8")
    document = Document(
        filename="do-not-delete.txt",
        file_type="txt",
        file_path=str(outside_file),
        chunk_count=1,
        status="completed",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    monkeypatch.setattr(documents.get_settings(), "upload_dir", upload_dir)
    monkeypatch.setattr(documents, "delete_document_vectors", lambda document_id: None)

    response = client.delete(f"/documents/{document.id}")

    assert response.status_code == 200
    assert outside_file.exists()


def test_purge_document_not_found(client):
    response = client.delete("/documents/missing-id/purge")

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_purge_document_marks_purged_and_removes_file(client, db_session, monkeypatch):
    deleted_vectors: list[str] = []
    base_dir = make_test_dir("purge_uploaded")
    upload_dir = base_dir / "uploads"
    upload_dir.mkdir()
    saved_file = upload_dir / "stored-demo.txt"
    saved_file.write_text("demo", encoding="utf-8")
    document = Document(
        filename="demo.txt",
        file_type="txt",
        file_path=str(saved_file),
        chunk_count=1,
        status="deleted",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    monkeypatch.setattr(documents.get_settings(), "upload_dir", upload_dir)
    monkeypatch.setattr(documents, "delete_document_vectors", lambda document_id: deleted_vectors.append(document_id))

    response = client.delete(f"/documents/{document.id}/purge")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": document.id,
        "status": "purged",
        "message": "document purged successfully",
    }
    assert deleted_vectors == [document.id]
    assert not saved_file.exists()
    db_session.refresh(document)
    assert document.status == "purged"
    assert document.purged_at is not None


def test_purge_missing_source_file_does_not_crash(client, db_session, monkeypatch):
    deleted_vectors: list[str] = []
    base_dir = make_test_dir("purge_missing")
    upload_dir = base_dir / "uploads"
    upload_dir.mkdir()
    missing_file = upload_dir / "missing.txt"
    document = Document(
        filename="missing.txt",
        file_type="txt",
        file_path=str(missing_file),
        chunk_count=1,
        status="deleted",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    monkeypatch.setattr(documents.get_settings(), "upload_dir", upload_dir)
    monkeypatch.setattr(documents, "delete_document_vectors", lambda document_id: deleted_vectors.append(document_id))

    response = client.delete(f"/documents/{document.id}/purge")

    assert response.status_code == 200
    assert deleted_vectors == [document.id]
    db_session.refresh(document)
    assert document.status == "purged"


def test_reindex_document_not_found(client):
    response = client.post("/documents/missing-id/reindex")

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_reindex_deleted_document_returns_400(client, db_session):
    document = Document(
        filename="deleted.txt",
        file_type="txt",
        file_path="uploads/deleted.txt",
        chunk_count=0,
        status="deleted",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    response = client.post(f"/documents/{document.id}/reindex")

    assert response.status_code == 400
    assert response.json()["detail"] == "Deleted document cannot be reindexed"


def test_reindex_document_marks_processing(client, db_session, monkeypatch):
    deleted_vectors: list[str] = []
    processed_documents: list[str] = []
    document = Document(
        filename="demo.txt",
        file_type="txt",
        file_path="uploads/demo.txt",
        chunk_count=2,
        status="completed",
        error_message="old error",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    monkeypatch.setattr(documents, "delete_document_vectors", lambda document_id: deleted_vectors.append(document_id))
    monkeypatch.setattr(
        documents,
        "process_document",
        lambda document_id, saved_path=None, file_type=None, db=None: processed_documents.append(document_id),
    )

    response = client.post(f"/documents/{document.id}/reindex")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": document.id,
        "status": "processing",
        "message": "document reindex started",
    }
    assert deleted_vectors == [document.id]
    assert processed_documents == [document.id]
    db_session.refresh(document)
    assert document.status == "processing"
    assert document.chunk_count == 0
    assert document.error_message is None


def test_process_document_marks_failed_when_parser_fails(db_session, monkeypatch):
    document = Document(
        filename="broken.txt",
        file_type="txt",
        file_path="uploads/broken.txt",
        chunk_count=0,
        status="processing",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    class FailingParser:
        def parse(self, path, file_type):
            raise RuntimeError("parse failed")

    monkeypatch.setattr(documents, "ParserService", lambda: FailingParser())

    documents.process_document(document.id, db=db_session)

    db_session.refresh(document)
    assert document.status == "failed"
    assert "parse failed" in document.error_message
