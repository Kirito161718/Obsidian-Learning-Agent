from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import UserPreference
from app.schemas.agent import UserPreferenceUpdate
from app.services.crud_errors import ServiceDatabaseError

DEFAULT_USER_PREFERENCES = {
    "id": "default",
    "preferred_language": "zh-CN",
    "answer_depth": "standard",
    "answer_style": "structured",
    "citation_mode": "required",
    "custom_instructions": None,
    "created_at": None,
    "updated_at": None,
}


class UserPreferenceService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def get(self, db: Session | None) -> dict:
        if db is None:
            return dict(DEFAULT_USER_PREFERENCES)
        try:
            record = db.get(UserPreference, "default")
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to get user preferences") from exc
        return self.to_dict(record) if record is not None else dict(DEFAULT_USER_PREFERENCES)

    def update(self, db: Session, payload: UserPreferenceUpdate) -> dict:
        values = payload.model_dump(exclude_unset=True, mode="json")
        custom = values.get("custom_instructions")
        if custom is not None and len(custom) > self.settings.user_preferences_max_chars:
            raise ValueError(
                f"custom_instructions must not exceed {self.settings.user_preferences_max_chars} characters"
            )
        try:
            record = db.get(UserPreference, "default")
            if record is None:
                record = UserPreference(id="default")
                db.add(record)
            for field_name, value in values.items():
                setattr(record, field_name, value)
            record.updated_at = datetime.now(UTC)
            db.commit()
            db.refresh(record)
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to update user preferences") from exc
        return self.to_dict(record)

    @staticmethod
    def to_dict(record: UserPreference) -> dict:
        return {
            "id": record.id,
            "preferred_language": record.preferred_language,
            "answer_depth": record.answer_depth,
            "answer_style": record.answer_style,
            "citation_mode": record.citation_mode,
            "custom_instructions": record.custom_instructions,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
