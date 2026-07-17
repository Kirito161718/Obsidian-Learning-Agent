from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ObsidianNote
from app.services.vector_service import VectorService


class ObsidianNoteNotFoundError(LookupError):
    pass


class ObsidianNoteService:
    def list_notes(
        self,
        db: Session,
        *,
        status: str | None = None,
        path_prefix: str | None = None,
        tag: str | None = None,
    ) -> list[ObsidianNote]:
        statement = select(ObsidianNote)
        if status:
            statement = statement.where(ObsidianNote.index_status == status)
        if path_prefix:
            normalized = path_prefix.replace("\\", "/").strip("/")
            statement = statement.where(ObsidianNote.relative_path.like(f"{normalized}%"))
        notes = list(db.scalars(statement.order_by(ObsidianNote.relative_path)).all())
        if tag:
            notes = [note for note in notes if tag in (note.tags or [])]
        return notes

    def get_note(self, db: Session, note_id: str) -> ObsidianNote:
        note = db.get(ObsidianNote, note_id)
        if note is None:
            raise ObsidianNoteNotFoundError("Obsidian note not found")
        return note

    def list_chunks(self, db: Session, note_id: str, vector_service: VectorService) -> list[dict]:
        note = self.get_note(db, note_id)
        if not note.indexed_hash:
            return []
        return vector_service.list_obsidian_chunks(note.id, note.indexed_hash)
