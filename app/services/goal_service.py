from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import LearningGoal, LearningSession, LearningStatus, utc_now
from app.schemas.goal import GoalStatus, GoalType, LearningGoalCreate, LearningGoalUpdate
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError


class GoalService:
    def create(self, db: Session, payload: LearningGoalCreate) -> LearningGoal:
        goal = LearningGoal(**payload.model_dump(mode="json"))
        db.add(goal)
        self._commit(db)
        db.refresh(goal)
        return goal

    def list_records(
        self,
        db: Session,
        *,
        goal_type: GoalType | None = None,
        status: GoalStatus | None = None,
    ) -> list[LearningGoal]:
        try:
            query = db.query(LearningGoal)
            if goal_type is not None:
                query = query.filter(LearningGoal.goal_type == goal_type.value)
            if status is not None:
                query = query.filter(LearningGoal.status == status.value)
            return query.order_by(LearningGoal.priority.desc(), LearningGoal.updated_at.desc()).all()
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to list learning goals") from exc

    def get(self, db: Session, goal_id: str) -> LearningGoal:
        try:
            goal = db.get(LearningGoal, goal_id)
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to get learning goal") from exc
        if goal is None:
            raise ResourceNotFoundError("Learning goal not found")
        return goal

    def update(self, db: Session, goal_id: str, payload: LearningGoalUpdate) -> LearningGoal:
        goal = self.get(db, goal_id)
        for field_name, value in payload.model_dump(exclude_unset=True, mode="json").items():
            setattr(goal, field_name, value)
        goal.updated_at = utc_now()
        self._commit(db)
        db.refresh(goal)
        return goal

    def delete(self, db: Session, goal_id: str) -> None:
        goal = self.get(db, goal_id)
        try:
            statuses = db.query(LearningStatus).all()
            for learning_status in statuses:
                if goal_id in learning_status.related_goal_ids:
                    learning_status.related_goal_ids = [
                        related_id for related_id in learning_status.related_goal_ids if related_id != goal_id
                    ]
                    learning_status.updated_at = utc_now()
            db.query(LearningSession).filter(LearningSession.goal_id == goal_id).update(
                {LearningSession.goal_id: None, LearningSession.updated_at: utc_now()},
                synchronize_session=False,
            )
            db.delete(goal)
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to delete learning goal") from exc

    @staticmethod
    def _commit(db: Session) -> None:
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to save learning goal") from exc
