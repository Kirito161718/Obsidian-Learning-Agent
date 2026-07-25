from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import OfficialSource, UpdateProposal
from app.schemas.official_sources import OfficialSourceReminderPatch
from app.services.official_source_service import OfficialSourceService


class OfficialReminderService:
    """Calculate user-facing reminders without fetching any remote content."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.sources = OfficialSourceService(self.settings)

    def summary(self, db: Session, *, now: datetime | None = None) -> dict:
        current_time = self._as_utc(now or datetime.now(UTC))
        sources = list(
            db.scalars(
                select(OfficialSource).order_by(OfficialSource.created_at.desc())
            ).all()
        )
        rows = [self.source_row(source, now=current_time) for source in sources]
        priority = {
            "overdue": 0,
            "error": 1,
            "never_checked": 2,
            "due": 3,
            "current": 4,
            "disabled": 5,
        }
        rows.sort(
            key=lambda row: (
                priority[row["reminder_status"]],
                row["next_suggested_check_at"] or datetime.max.replace(tzinfo=UTC),
                row["name"].casefold(),
            )
        )
        pending = db.scalar(
            select(func.count())
            .select_from(UpdateProposal)
            .where(UpdateProposal.status == "pending_approval")
        )
        return {
            "total_sources": len(rows),
            "due_count": sum(row["reminder_status"] == "due" for row in rows),
            "overdue_count": sum(row["reminder_status"] == "overdue" for row in rows),
            "never_checked_count": sum(
                row["reminder_status"] == "never_checked" for row in rows
            ),
            "error_count": sum(row["reminder_status"] == "error" for row in rows),
            "pending_proposal_count": int(pending or 0),
            "batch_limit": self.settings.official_reminder_batch_limit,
            "sources": rows,
        }

    def update(
        self,
        db: Session,
        source_id: str,
        payload: OfficialSourceReminderPatch,
    ) -> OfficialSource:
        source = self.sources.get(db, source_id)
        source.reminder_enabled = payload.reminder_enabled
        source.check_interval_days = payload.check_interval_days
        db.commit()
        db.refresh(source)
        return source

    def source_row(
        self,
        source: OfficialSource,
        *,
        now: datetime | None = None,
    ) -> dict:
        current_time = self._as_utc(now or datetime.now(UTC))
        last_checked = (
            self._as_utc(source.last_checked_at)
            if source.last_checked_at is not None
            else None
        )
        next_check = (
            last_checked + timedelta(days=source.check_interval_days)
            if last_checked is not None
            else None
        )
        if not source.enabled or not source.reminder_enabled:
            status = "disabled"
        elif last_checked is None:
            status = "never_checked"
        elif source.last_error_code:
            status = "error"
        elif current_time < next_check:
            status = "current"
        elif current_time >= next_check + timedelta(
            days=self.settings.official_reminder_overdue_grace_days
        ):
            status = "overdue"
        else:
            status = "due"
        return {
            "id": source.id,
            "name": source.name,
            "reminder_status": status,
            "check_interval_days": source.check_interval_days,
            "last_checked_at": last_checked,
            "next_suggested_check_at": next_check,
            "last_error_code": source.last_error_code,
        }

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
