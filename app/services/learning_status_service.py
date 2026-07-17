from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import LearningGoal, LearningStatus, utc_now
from app.schemas.learning_status import LearningStatusCreate, LearningStatusUpdate, LearningStatusValue
from app.services.crud_errors import InvalidReferenceError, ResourceNotFoundError, ServiceDatabaseError


class LearningStatusService:
    """Manual-only CRUD boundary.

    RAG, model output, chat history, and reading activity must never call this
    service to infer or mutate a user's learning state.
    """

    def create(self, db: Session, payload: LearningStatusCreate) -> LearningStatus:
        self._validate_goal_ids(db, payload.related_goal_ids)
        learning_status = LearningStatus(**payload.model_dump(mode="json"))
        db.add(learning_status)
        self._commit(db)
        db.refresh(learning_status)
        return learning_status

    def list_records(
        self,
        db: Session,
        *,
        status: LearningStatusValue | None = None,
        min_mastery: int | None = None,
        max_mastery: int | None = None,
        related_goal_id: str | None = None,
    ) -> list[LearningStatus]:
        try:
            query = db.query(LearningStatus)
            if status is not None:
                query = query.filter(LearningStatus.status == status.value)
            if min_mastery is not None:
                query = query.filter(LearningStatus.mastery_level >= min_mastery)
            if max_mastery is not None:
                query = query.filter(LearningStatus.mastery_level <= max_mastery)
            records = query.order_by(LearningStatus.updated_at.desc()).all()
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to list learning statuses") from exc
        if related_goal_id is not None:
            records = [record for record in records if related_goal_id in record.related_goal_ids]
        return records

    def get(self, db: Session, status_id: str) -> LearningStatus:
        try:
            learning_status = db.get(LearningStatus, status_id)
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to get learning status") from exc
        if learning_status is None:
            raise ResourceNotFoundError("Learning status not found")
        return learning_status

    def update(self, db: Session, status_id: str, payload: LearningStatusUpdate) -> LearningStatus:
        learning_status = self.get(db, status_id)
        values = payload.model_dump(exclude_unset=True, mode="json")
        if "related_goal_ids" in values:
            self._validate_goal_ids(db, values["related_goal_ids"])
        for field_name, value in values.items():
            setattr(learning_status, field_name, value)
        learning_status.updated_at = utc_now()
        self._commit(db)
        db.refresh(learning_status)
        return learning_status

    def delete(self, db: Session, status_id: str) -> None:
        learning_status = self.get(db, status_id)
        try:
            db.delete(learning_status)
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to delete learning status") from exc

    def goals_by_id(self, db: Session, goal_ids: set[str]) -> dict[str, LearningGoal]:
        if not goal_ids:
            return {}
        try:
            goals = db.query(LearningGoal).filter(LearningGoal.id.in_(goal_ids)).all()
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to load related learning goals") from exc
        return {goal.id: goal for goal in goals}

    def _validate_goal_ids(self, db: Session, goal_ids: list[str]) -> None:
        if not goal_ids:
            return
        goals_by_id = self.goals_by_id(db, set(goal_ids))
        missing = [goal_id for goal_id in goal_ids if goal_id not in goals_by_id]
        if missing:
            raise InvalidReferenceError(missing)

    @staticmethod
    def _commit(db: Session) -> None:
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to save learning status") from exc
