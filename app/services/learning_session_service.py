from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import LearningGoal, LearningSession, LearningStatus, QAHistory
from app.schemas.learning import DEFAULT_SESSION_TITLE, LearningSessionCreate, LearningSessionUpdate
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError


class InvalidSessionReferenceError(ValueError):
    def __init__(self, field_name: str, reference_id: str) -> None:
        self.field_name = field_name
        self.reference_id = reference_id
        super().__init__(f"Unknown {field_name}: {reference_id}")


class LearningSessionService:
    def create(self, db: Session, payload: LearningSessionCreate) -> LearningSession:
        self._validate_references(db, payload.goal_id, payload.learning_status_id)
        record = LearningSession(**payload.model_dump(mode="json"))
        db.add(record)
        self._commit(db, "Failed to create learning session")
        db.refresh(record)
        return record

    def list_records(
        self,
        db: Session,
        *,
        status: str | None = "active",
        goal_id: str | None = None,
        learning_status_id: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[int, list[LearningSession]]:
        try:
            query = db.query(LearningSession)
            if status is not None:
                query = query.filter(LearningSession.status == status)
            if goal_id is not None:
                query = query.filter(LearningSession.goal_id == goal_id)
            if learning_status_id is not None:
                query = query.filter(LearningSession.learning_status_id == learning_status_id)
            total = query.count()
            rows = (
                query.order_by(LearningSession.last_activity_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to list learning sessions") from exc
        return total, rows

    def get(self, db: Session, session_id: str) -> LearningSession:
        try:
            record = db.get(LearningSession, session_id)
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to get learning session") from exc
        if record is None:
            raise ResourceNotFoundError("Learning session not found")
        return record

    def update(self, db: Session, session_id: str, payload: LearningSessionUpdate) -> LearningSession:
        record = self.get(db, session_id)
        values = payload.model_dump(exclude_unset=True, mode="json")
        goal_id = values.get("goal_id", record.goal_id)
        learning_status_id = values.get("learning_status_id", record.learning_status_id)
        self._validate_references(db, goal_id, learning_status_id)
        source_types = values.get("source_types", record.source_types)
        source_prefixes = values.get("source_path_prefixes", record.source_path_prefixes)
        if source_prefixes and "obsidian_note" not in source_types:
            raise ValueError("source_path_prefixes require obsidian_note source type")
        for field_name, value in values.items():
            setattr(record, field_name, value)
        record.updated_at = datetime.now(UTC)
        self._commit(db, "Failed to update learning session")
        db.refresh(record)
        return record

    def archive(self, db: Session, session_id: str) -> LearningSession:
        return self._set_status(db, session_id, "archived")

    def restore(self, db: Session, session_id: str) -> LearningSession:
        return self._set_status(db, session_id, "active")

    def messages(self, db: Session, session_id: str) -> list[QAHistory]:
        self.get(db, session_id)
        try:
            return (
                db.query(QAHistory)
                .filter(QAHistory.session_id == session_id)
                .order_by(QAHistory.created_at.asc())
                .all()
            )
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to list learning session messages") from exc

    def record_activity(self, db: Session, session_id: str, question: str) -> LearningSession:
        record = self.get(db, session_id)
        now = datetime.now(UTC)
        record.last_activity_at = now
        record.updated_at = now
        if record.title == DEFAULT_SESSION_TITLE:
            normalized = " ".join(question.split()).strip()
            if normalized:
                record.title = normalized[:40]
        self._commit(db, "Failed to update learning session activity")
        return record

    def _set_status(self, db: Session, session_id: str, status: str) -> LearningSession:
        record = self.get(db, session_id)
        record.status = status
        record.updated_at = datetime.now(UTC)
        self._commit(db, "Failed to update learning session status")
        db.refresh(record)
        return record

    @staticmethod
    def _validate_references(
        db: Session,
        goal_id: str | None,
        learning_status_id: str | None,
    ) -> None:
        if goal_id is not None and db.get(LearningGoal, goal_id) is None:
            raise InvalidSessionReferenceError("goal_id", goal_id)
        if learning_status_id is not None and db.get(LearningStatus, learning_status_id) is None:
            raise InvalidSessionReferenceError("learning_status_id", learning_status_id)

    @staticmethod
    def _commit(db: Session, message: str) -> None:
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError(message) from exc
