from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.db.models import ObsidianNote
from app.domain.retrieved_chunk import RetrievedChunk
from app.main import app
from app.routers import obsidian
from app.schemas.rag import AskRequest
from app.services.context_builder import ContextBuilder
from app.services.vector_service import VectorService
from app.routers.qa import sanitize_source_chunk


class FakeIndexService:
    def __init__(self, *, disabled=False):
        self.disabled = disabled
        self.calls = []

    def status(self, db):
        return {
            "enabled": True,
            "configured": True,
            "available": True,
            "vault_name": "note",
            "note_count": 1,
            "status_counts": {"indexed": 1},
            "last_scanned_at": None,
            "last_indexed_at": None,
        }

    def scan(self, db):
        from app.services.obsidian_index_service import ObsidianDisabledError

        if self.disabled:
            raise ObsidianDisabledError("disabled")
        self.calls.append("scan")
        return {"counts": {key: int(key == "new") for key in ("new", "changed", "unchanged", "missing", "skipped", "error")}, "items": [{"kind": "new", "relative_path": "Note.md"}]}

    def index(self, db, *, relative_paths=None, force=False):
        self.calls.append((relative_paths, force))
        return {"counts": {key: int(key == "unchanged") for key in ("new", "changed", "unchanged", "missing", "skipped", "error")}, "items": [{"kind": "unchanged", "relative_path": "Note.md"}]}

    def reindex_note(self, db, note_id):
        if note_id == "missing":
            raise LookupError("not found")
        self.calls.append(("reindex", note_id))
        return {"counts": {key: int(key == "changed") for key in ("new", "changed", "unchanged", "missing", "skipped", "error")}, "items": [{"kind": "changed", "relative_path": "Note.md"}]}


def override_index(fake):
    app.dependency_overrides[obsidian.get_obsidian_index_service] = lambda: fake


def test_obsidian_status_api_returns_safe_summary(client):
    override_index(FakeIndexService())
    data = client.get("/obsidian/status").json()
    assert data["available"] is True
    assert data["vault_name"] == "note"
    assert "C:\\" not in str(data)


def test_obsidian_scan_api_is_dry_run_operation(client):
    fake = FakeIndexService()
    override_index(fake)
    response = client.post("/obsidian/scan")
    assert response.status_code == 200
    assert response.json()["counts"]["new"] == 1
    assert fake.calls == ["scan"]


def test_obsidian_scan_api_reports_disabled_as_400(client):
    override_index(FakeIndexService(disabled=True))
    assert client.post("/obsidian/scan").status_code == 400


def test_obsidian_index_api_passes_paths_and_force(client):
    fake = FakeIndexService()
    override_index(fake)
    response = client.post("/obsidian/index", json={"relative_paths": ["Folder/Note.md"], "force": True})
    assert response.status_code == 200
    assert fake.calls == [(["Folder/Note.md"], True)]


@pytest.mark.parametrize("path", ["../Note.md", "C:/Note.md", "/Note.md"])
def test_obsidian_index_api_rejects_unsafe_relative_paths(client, path):
    override_index(FakeIndexService())
    assert client.post("/obsidian/index", json={"relative_paths": [path]}).status_code == 422


def add_note(db_session, **overrides):
    now = datetime.now(UTC)
    values = {
        "relative_path": "Knowledge/Note.md",
        "file_name": "Note.md",
        "title": "Note",
        "observed_hash": "a" * 64,
        "indexed_hash": "a" * 64,
        "size_bytes": 10,
        "frontmatter": {},
        "tags": ["rag"],
        "aliases": [],
        "outbound_links": [],
        "index_status": "indexed",
        "chunk_count": 1,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    note = ObsidianNote(**values)
    db_session.add(note)
    db_session.commit()
    return note


def test_obsidian_notes_list_supports_status_path_and_tag_filters(client, db_session):
    add_note(db_session)
    response = client.get("/obsidian/notes?status=indexed&path_prefix=Knowledge&tag=rag")
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.parametrize("prefix", ["../", "C:/Vault", "/Vault"])
def test_obsidian_notes_list_rejects_unsafe_path_prefix(client, prefix):
    assert client.get("/obsidian/notes", params={"path_prefix": prefix}).status_code == 422


def test_obsidian_note_detail_returns_metadata_without_body_or_absolute_path(client, db_session):
    note = add_note(db_session)
    data = client.get(f"/obsidian/notes/{note.id}").json()
    assert data["relative_path"] == "Knowledge/Note.md"
    assert "body" not in data
    assert "absolute_path" not in data


def test_obsidian_note_detail_returns_404(client):
    assert client.get("/obsidian/notes/missing").status_code == 404


def test_obsidian_note_reindex_returns_404_for_unknown_note(client):
    override_index(FakeIndexService())
    assert client.post("/obsidian/notes/missing/reindex").status_code == 404


@pytest.mark.parametrize("prefix", ["C:/Vault", "../Vault", "/Vault"])
def test_rag_request_rejects_absolute_or_parent_path_prefix(prefix):
    with pytest.raises(ValidationError):
        AskRequest(question="test", source_path_prefixes=[prefix])


def test_rag_request_rejects_document_id_with_obsidian_scope():
    with pytest.raises(ValidationError):
        AskRequest(question="test", document_id="doc", source_types=["obsidian_note"])


def test_vector_scope_defaults_old_payload_to_uploaded_document():
    payload = {"document_id": "doc", "filename": "old.pdf"}
    assert VectorService._payload_matches_scope(payload, ["uploaded_document"], None)
    assert not VectorService._payload_matches_scope(payload, ["obsidian_note"], None)


def test_vector_scope_filters_obsidian_path_prefix():
    payload = {"source_type": "obsidian_note", "source_path": "Knowledge/RAG/Note.md"}
    assert VectorService._payload_matches_scope(payload, ["obsidian_note"], ["Knowledge/RAG"])
    assert not VectorService._payload_matches_scope(payload, ["obsidian_note"], ["Projects"])


def test_obsidian_payload_maps_to_domain_chunk_with_metadata():
    chunk = RetrievedChunk.from_search_result(
        {
            "score": 0.8,
            "payload": {
                "chunk_id": "chunk",
                "source_id": "note",
                "source_type": "obsidian_note",
                "source_name": "RAG Note",
                "source_path": "Knowledge/RAG.md",
                "heading_path": ["RAG", "Retrieval"],
                "line_start": 10,
                "line_end": 20,
                "tags": ["rag"],
                "chunk_index": 0,
                "text": "content",
            },
        }
    )
    assert chunk.source_path == "Knowledge/RAG.md"
    assert chunk.metadata["heading_path"] == ["RAG", "Retrieval"]
    assert chunk.metadata["line_start"] == 10


def test_obsidian_rerank_payload_has_null_document_id():
    chunk = RetrievedChunk(
        chunk_id="chunk",
        source_id="note",
        source_type="obsidian_note",
        source_name="Note",
        source_path="Note.md",
        text="text",
    )
    assert chunk.to_rerank_item()["payload"]["document_id"] is None


def test_context_builder_formats_obsidian_source_with_relative_heading_path():
    chunk = RetrievedChunk(
        chunk_id="chunk",
        source_id="note",
        source_type="obsidian_note",
        source_name="Note",
        source_path="Knowledge/Note.md",
        text="text",
        metadata={"heading_path": ["One", "Two"], "line_start": 4, "line_end": 5},
    )
    result = ContextBuilder(max_chars=1000, max_chunks_per_source=3).build([chunk], 1)
    assert "来源类型：Obsidian 笔记" in result.context_text
    assert "相对路径：Knowledge/Note.md" in result.context_text
    assert "标题路径：One > Two" in result.context_text
    assert result.context_sources[0]["line_start"] == 4


def test_qa_history_source_keeps_obsidian_metadata():
    source = sanitize_source_chunk(
        {
            "chunk_id": "chunk",
            "document_id": None,
            "filename": "Note",
            "source_id": "note",
            "source_type": "obsidian_note",
            "source_name": "Note",
            "source_path": "Knowledge/Note.md",
            "heading_path": ["One"],
            "line_start": 2,
            "line_end": 4,
            "tags": ["rag"],
        }
    )
    assert source.document_id is None
    assert source.source_type == "obsidian_note"
    assert source.source_path == "Knowledge/Note.md"
    assert source.heading_path == ["One"]
