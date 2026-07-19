import json
import logging
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import LearningGoal, LearningSessionReview, LearningStatus, QAHistory
from app.schemas.learning import LearningReviewContent
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError
from app.services.learning_session_service import LearningSessionService
from app.services.llm_service import DeepSeekClient
from app.services.run_history_service import RunHistoryService

logger = logging.getLogger(__name__)


class EmptyLearningSessionError(ValueError):
    pass


class InvalidReviewOutputError(ValueError):
    pass


class LearningReviewService:
    def __init__(self, llm_client: DeepSeekClient | None = None) -> None:
        self.llm_client = llm_client
        self.session_service = LearningSessionService()
        self.run_history = RunHistoryService()

    def get(self, db: Session, session_id: str) -> LearningSessionReview:
        self.session_service.get(db, session_id)
        try:
            review = (
                db.query(LearningSessionReview)
                .filter(LearningSessionReview.session_id == session_id)
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to get learning session review") from exc
        if review is None:
            raise ResourceNotFoundError("Learning session review not found")
        return review

    def generate(self, db: Session, session_id: str) -> LearningSessionReview:
        session = self.session_service.get(db, session_id)
        run_id: str | None = None
        try:
            run_id = self.run_history.create(
                db,
                run_type="session_review",
                session_id=session_id,
                mode="review",
            ).id
        except Exception as exc:
            logger.warning("Session review run creation failed: error_type=%s", type(exc).__name__)
        try:
            review, qa_count = self._generate_review(db, session)
        except Exception as exc:
            self.run_history.safe_fail(db, run_id, exc)
            raise
        self.run_history.safe_succeed(
            db,
            run_id,
            state={"retrieved_count": qa_count, "model_call_count": 1},
        )
        return review

    def _generate_review(self, db: Session, session) -> tuple[LearningSessionReview, int]:
        limit = get_settings().learning_session_review_max_qa
        rows = (
            db.query(QAHistory)
            .filter(QAHistory.session_id == session.id)
            .order_by(QAHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        if not rows:
            raise EmptyLearningSessionError("Learning session has no QA history")
        rows.reverse()
        payload = {
            "goal": self._goal_context(db, session.goal_id),
            "learning_status": self._status_context(db, session.learning_status_id),
            "qa": [self._compact_qa(row) for row in rows],
        }
        client = self.llm_client or DeepSeekClient()
        raw = client.generate_learning_review(payload)
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            content = LearningReviewContent.model_validate(data)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise InvalidReviewOutputError("LLM returned an invalid learning review JSON object") from exc

        review = (
            db.query(LearningSessionReview)
            .filter(LearningSessionReview.session_id == session.id)
            .one_or_none()
        )
        values = content.model_dump(mode="json")
        now = datetime.now(UTC)
        if review is None:
            review = LearningSessionReview(
                session_id=session.id,
                source_qa_ids=[row.id for row in rows],
                **values,
            )
            db.add(review)
        else:
            for field_name, value in values.items():
                setattr(review, field_name, value)
            review.source_qa_ids = [row.id for row in rows]
            review.updated_at = now
        try:
            db.commit()
            db.refresh(review)
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to save learning session review") from exc
        return review, len(rows)

    @staticmethod
    def _goal_context(db: Session, goal_id: str | None) -> dict | None:
        goal = db.get(LearningGoal, goal_id) if goal_id else None
        if goal is None:
            return None
        return {
            "name": goal.name,
            "goal_type": goal.goal_type,
            "priority": goal.priority,
            "related_topics": list(goal.related_topics or []),
            "description": (goal.description or "")[:300] or None,
        }

    @staticmethod
    def _status_context(db: Session, status_id: str | None) -> dict | None:
        status = db.get(LearningStatus, status_id) if status_id else None
        if status is None:
            return None
        return {
            "topic": status.topic,
            "status": status.status,
            "mastery_level": status.mastery_level,
            "manual_importance": status.manual_importance,
            "current_problem": (status.current_problem or "")[:300] or None,
            "project_relevance": status.project_relevance,
        }

    @staticmethod
    def _compact_qa(row: QAHistory) -> dict:
        sources = []
        for item in row.source_chunks or []:
            if not isinstance(item, dict):
                continue
            sources.append(
                {
                    "source_name": item.get("source_name") or item.get("filename"),
                    "heading_path": list(item.get("heading_path") or [])[:6],
                    "section_title": item.get("section_title"),
                }
            )
        return {
            "question": row.question,
            "answer": row.answer,
            "sources": sources,
            "no_answer": not bool(row.source_chunks),
        }
