import json

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import LearningSession, QAHistory
from app.security.redaction import safe_text_preview
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError


NO_CONTEXT_ANSWER = "根据当前知识库资料，无法确定。"


class SessionMemoryService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def load(self, db: Session | None, session_id: str | None) -> list[dict]:
        turns, _ = self.load_with_metadata(db, session_id)
        return turns

    def load_with_metadata(self, db: Session | None, session_id: str | None) -> tuple[list[dict], bool]:
        if not self.settings.session_memory_enabled or not session_id:
            return [], False
        if db is None:
            raise RuntimeError("Database session is required for Session Memory")
        try:
            session = db.get(LearningSession, session_id)
            if session is None:
                raise ResourceNotFoundError("Learning session not found")
            if not session.memory_enabled:
                return [], False
            rows = (
                db.query(QAHistory)
                .filter(QAHistory.session_id == session_id)
                .order_by(QAHistory.created_at.desc())
                .limit(self.settings.session_memory_max_turns + 1)
                .all()
            )
        except ResourceNotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to load Session Memory") from exc

        truncated = len(rows) > self.settings.session_memory_max_turns
        rows = rows[: self.settings.session_memory_max_turns]
        turns = [self._compact(row) for row in reversed(rows)]
        original_count = len(turns)
        fitted = self._fit_budget(turns)
        return fitted, truncated or len(fitted) < original_count

    def manifest(self, turns: list[dict], *, truncated: bool = False) -> dict:
        return {
            "included": bool(turns),
            "turns": len(turns),
            "chars": self.char_count(turns),
            "truncated": truncated,
        }

    @staticmethod
    def char_count(turns: list[dict]) -> int:
        return len(json.dumps(turns, ensure_ascii=False, separators=(",", ":"))) if turns else 0

    def _compact(self, row: QAHistory) -> dict:
        answer = safe_text_preview(row.answer, self.settings.session_memory_answer_preview_chars)
        return {
            "question": safe_text_preview(row.question, self.settings.session_memory_max_chars),
            "answer_preview": answer,
            "no_answer": answer == NO_CONTEXT_ANSWER or not bool(row.source_chunks),
        }

    def _fit_budget(self, turns: list[dict]) -> list[dict]:
        limit = self.settings.session_memory_max_chars
        while len(turns) > 1 and self.char_count(turns) > limit:
            turns.pop(0)
        if not turns or self.char_count(turns) <= limit:
            return turns

        turn = turns[0]
        overflow = self.char_count(turns) - limit
        answer = turn["answer_preview"]
        if answer:
            turn["answer_preview"] = answer[: max(0, len(answer) - overflow)]
        if self.char_count(turns) > limit:
            overflow = self.char_count(turns) - limit
            question = turn["question"]
            turn["question"] = question[: max(0, len(question) - overflow)]
        while self.char_count(turns) > limit and turn["answer_preview"]:
            turn["answer_preview"] = turn["answer_preview"][:-1]
        return turns if self.char_count(turns) <= limit else []
