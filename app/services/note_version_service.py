from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import NoteVersion
from app.official_sources.errors import official_error


class NoteVersionService:
    def list(self, db: Session) -> list[NoteVersion]:
        return list(db.scalars(select(NoteVersion).order_by(NoteVersion.created_at.desc())).all())

    def get(self, db: Session, version_id: str) -> NoteVersion:
        row = db.get(NoteVersion, version_id)
        if row is None:
            raise official_error("official_source_not_found", "Note version was not found", status_code=404)
        return row
