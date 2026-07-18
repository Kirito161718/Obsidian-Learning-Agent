from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import ObsidianNote
from app.obsidian.markdown_parser import MarkdownParser
from app.services.obsidian_index_service import ObsidianDisabledError, ObsidianIndexService


class FakeEmbedding:
    vector_size = 2

    def __init__(self, error: Exception | None = None):
        self.calls = 0
        self.error = error

    def embed_texts(self, texts):
        self.calls += 1
        if self.error:
            raise self.error
        return [[1.0, 0.0] for _ in texts]


class FakeVector:
    def __init__(self):
        self.calls = []
        self.chunks = {}
        self.fail_upsert = False
        self.fail_delete_hash = None

    def upsert_obsidian_chunks(self, *, chunks, embeddings):
        self.calls.append(("upsert", chunks[0].note_hash if chunks else None))
        if self.fail_upsert:
            raise RuntimeError("upsert failed")
        for chunk in chunks:
            self.chunks[chunk.chunk_id] = chunk

    def delete_obsidian_version(self, note_id, note_hash=None):
        self.calls.append(("delete", note_hash))
        if note_hash == self.fail_delete_hash:
            raise RuntimeError("delete failed")
        self.chunks = {
            key: value
            for key, value in self.chunks.items()
            if not (value.note_id == note_id and (note_hash is None or value.note_hash == note_hash))
        }

    def list_obsidian_chunks(self, note_id, note_hash=None):
        return []


class FailingParser(MarkdownParser):
    def parse_file(self, path, relative_path):
        raise ValueError("bad markdown")


def settings(root: Path, *, enabled=True) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        obsidian_enabled=enabled,
        obsidian_vault_path=root,
        obsidian_exclude_dirs=".obsidian,Archive",
        obsidian_exclude_paths="Learning/_drafts",
        obsidian_chunk_min_chars=10,
        obsidian_chunk_target_chars=40,
        obsidian_chunk_max_chars=80,
        obsidian_atomic_block_hard_max_chars=120,
    )


def service(root: Path, *, embedding=None, vector=None, parser=None, enabled=True):
    embedding = embedding or FakeEmbedding()
    vector = vector or FakeVector()
    return (
        ObsidianIndexService(
            settings=settings(root, enabled=enabled),
            embedding_service=embedding,
            vector_service=vector,
            parser=parser,
        ),
        embedding,
        vector,
    )


def write_note(root: Path, text="# Title\nBody #rag") -> Path:
    path = root / "Knowledge" / "Note.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_dry_scan_reports_new_without_database_write(tmp_path, db_session):
    write_note(tmp_path)
    svc, embedding, vector = service(tmp_path)
    result = svc.scan(db_session)
    assert result["counts"]["new"] == 1
    assert db_session.scalar(select(ObsidianNote)) is None
    assert embedding.calls == 0
    assert vector.calls == []


def test_index_new_note_persists_metadata_and_vectors(tmp_path, db_session):
    write_note(tmp_path)
    svc, embedding, vector = service(tmp_path)
    result = svc.index(db_session)
    note = db_session.scalar(select(ObsidianNote))
    assert result["counts"]["new"] == 1
    assert note.title == "Title"
    assert note.tags == ["rag"]
    assert note.index_status == "indexed"
    assert note.chunk_count == len(vector.chunks) > 0
    assert embedding.calls == 1


def test_index_database_model_has_no_markdown_body_column():
    assert "body" not in ObsidianNote.__table__.columns
    assert "content" not in ObsidianNote.__table__.columns


def test_second_index_is_unchanged_without_embedding_or_qdrant_write(tmp_path, db_session):
    write_note(tmp_path)
    svc, embedding, vector = service(tmp_path)
    svc.index(db_session)
    first_calls = list(vector.calls)
    result = svc.index(db_session)
    assert result["counts"]["unchanged"] == 1
    assert embedding.calls == 1
    assert vector.calls == first_calls


def test_changed_note_upserts_new_before_deleting_old(tmp_path, db_session):
    path = write_note(tmp_path, "# Title\nold")
    svc, _, vector = service(tmp_path)
    svc.index(db_session)
    old_hash = db_session.scalar(select(ObsidianNote)).indexed_hash
    vector.calls.clear()
    path.write_text("# Title\nnew content", encoding="utf-8")
    svc.index(db_session)
    assert vector.calls[0][0] == "upsert"
    assert vector.calls[1] == ("delete", old_hash)


def test_changed_note_updates_indexed_hash(tmp_path, db_session):
    path = write_note(tmp_path, "old")
    svc, _, _ = service(tmp_path)
    svc.index(db_session)
    old_hash = db_session.scalar(select(ObsidianNote)).indexed_hash
    path.write_text("new", encoding="utf-8")
    svc.index(db_session)
    assert db_session.scalar(select(ObsidianNote)).indexed_hash != old_hash


def test_missing_note_deletes_vectors_marks_missing_and_retains_row(tmp_path, db_session):
    path = write_note(tmp_path)
    svc, _, vector = service(tmp_path)
    svc.index(db_session)
    note_id = db_session.scalar(select(ObsidianNote)).id
    path.unlink()
    result = svc.index(db_session)
    note = db_session.get(ObsidianNote, note_id)
    assert result["counts"]["missing"] == 1
    assert note.index_status == "missing"
    assert note.chunk_count == 0
    assert not vector.chunks


def test_parse_failure_on_new_note_records_error_without_vectors(tmp_path, db_session):
    write_note(tmp_path)
    svc, _, vector = service(tmp_path, parser=FailingParser())
    result = svc.index(db_session)
    note = db_session.scalar(select(ObsidianNote))
    assert result["counts"]["error"] == 1
    assert note.index_status == "error"
    assert note.indexed_hash is None
    assert vector.calls == []


def test_parse_failure_on_changed_note_preserves_old_hash_and_vectors(tmp_path, db_session):
    path = write_note(tmp_path, "old")
    svc, embedding, vector = service(tmp_path)
    svc.index(db_session)
    note = db_session.scalar(select(ObsidianNote))
    old_hash, old_vector_ids = note.indexed_hash, set(vector.chunks)
    path.write_text("new", encoding="utf-8")
    svc.parser = FailingParser()
    svc.index(db_session)
    db_session.expire_all()
    note = db_session.get(ObsidianNote, note.id)
    assert note.indexed_hash == old_hash
    assert set(vector.chunks) == old_vector_ids
    assert note.index_status == "error"


def test_embedding_failure_records_error_and_does_not_upsert(tmp_path, db_session):
    write_note(tmp_path)
    svc, _, vector = service(tmp_path, embedding=FakeEmbedding(RuntimeError("embed failed")))
    result = svc.index(db_session)
    assert result["counts"]["error"] == 1
    assert vector.calls == []


def test_upsert_failure_records_error(tmp_path, db_session):
    write_note(tmp_path)
    vector = FakeVector()
    vector.fail_upsert = True
    svc, _, _ = service(tmp_path, vector=vector)
    result = svc.index(db_session)
    assert result["counts"]["error"] == 1
    assert db_session.scalar(select(ObsidianNote)).indexed_hash is None


def test_old_version_delete_failure_cleans_new_and_preserves_old_hash(tmp_path, db_session):
    path = write_note(tmp_path, "old")
    svc, _, vector = service(tmp_path)
    svc.index(db_session)
    note = db_session.scalar(select(ObsidianNote))
    old_hash, old_ids = note.indexed_hash, set(vector.chunks)
    vector.fail_delete_hash = old_hash
    path.write_text("new version", encoding="utf-8")
    result = svc.index(db_session)
    db_session.expire_all()
    note = db_session.get(ObsidianNote, note.id)
    assert result["counts"]["error"] == 1
    assert note.indexed_hash == old_hash
    assert set(vector.chunks) == old_ids


def test_force_reindex_runs_embedding_for_unchanged_note(tmp_path, db_session):
    write_note(tmp_path)
    svc, embedding, _ = service(tmp_path)
    svc.index(db_session)
    result = svc.index(db_session, force=True)
    assert result["counts"]["changed"] == 1
    assert embedding.calls == 2


def test_reindex_unknown_note_raises_lookup_error(tmp_path, db_session):
    svc, _, _ = service(tmp_path)
    with pytest.raises(LookupError):
        svc.reindex_note(db_session, "missing")


def test_disabled_integration_rejects_scan_and_index(tmp_path, db_session):
    svc, _, _ = service(tmp_path, enabled=False)
    with pytest.raises(ObsidianDisabledError):
        svc.scan(db_session)
    with pytest.raises(ObsidianDisabledError):
        svc.index(db_session)


def test_status_does_not_return_absolute_vault_path(tmp_path, db_session):
    svc, _, _ = service(tmp_path)
    result = svc.status(db_session)
    assert result["vault_name"] == tmp_path.name
    assert str(tmp_path) not in str(result)
