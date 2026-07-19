import json

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import LearningGoal, LearningSession, LearningStatus
from app.services.crud_errors import ResourceNotFoundError


class LearningContextService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def resolve(
        self,
        db: Session | None,
        *,
        session_id: str | None,
        goal_id: str | None,
        learning_status_id: str | None,
        source_types: list[str] | None = None,
        source_path_prefixes: list[str] | None = None,
    ) -> dict:
        session = None
        if session_id:
            if db is None:
                raise RuntimeError("Database session is required when session_id is provided")
            session = db.get(LearningSession, session_id)
            if session is None:
                raise ResourceNotFoundError("Learning session not found")
            source_types = list(session.source_types or [])
            source_path_prefixes = list(session.source_path_prefixes or [])
            goal_id = goal_id or session.goal_id
            learning_status_id = learning_status_id or session.learning_status_id

        context = self.build(db, goal_id=goal_id, learning_status_id=learning_status_id)
        return {
            "session": session,
            "goal_id": goal_id,
            "learning_status_id": learning_status_id,
            "source_types": source_types,
            "source_path_prefixes": source_path_prefixes,
            "context": context,
        }

    def build(
        self,
        db: Session | None,
        *,
        goal_id: str | None,
        learning_status_id: str | None,
    ) -> dict:
        if goal_id is None and learning_status_id is None:
            return {}
        if db is None:
            raise RuntimeError("Database session is required for explicit learning context")
        value: dict[str, dict] = {}
        if goal_id:
            goal = db.get(LearningGoal, goal_id)
            if goal is None:
                raise ResourceNotFoundError("Learning goal not found")
            value["goal"] = {
                "name": goal.name,
                "goal_type": goal.goal_type,
                "priority": goal.priority,
                "related_topics": [str(item)[:80] for item in (goal.related_topics or [])[:12]],
                "description": (goal.description or "")[:400] or None,
            }
        if learning_status_id:
            status = db.get(LearningStatus, learning_status_id)
            if status is None:
                raise ResourceNotFoundError("Learning status not found")
            value["learning_status"] = {
                "topic": status.topic,
                "status": status.status,
                "mastery_level": status.mastery_level,
                "manual_importance": status.manual_importance,
                "current_problem": (status.current_problem or "")[:400] or None,
                "project_relevance": status.project_relevance,
            }
        return self._bound(value)

    def _bound(self, value: dict) -> dict:
        limit = self.settings.explicit_learning_context_max_chars
        if len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) <= limit:
            return value
        bounded = json.loads(json.dumps(value, ensure_ascii=False))
        if "goal" in bounded:
            bounded["goal"]["description"] = None
            bounded["goal"]["related_topics"] = bounded["goal"].get("related_topics", [])[:5]
        if "learning_status" in bounded:
            bounded["learning_status"]["current_problem"] = None
        return bounded
