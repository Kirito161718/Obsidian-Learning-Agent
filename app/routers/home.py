from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_db
from app.db.models import AgentRun, ObsidianNote, RagRetrievalProfile
from app.services.official_reminder_service import OfficialReminderService

settings = get_settings()
templates = Jinja2Templates(directory=settings.template_dir)
router = APIRouter(tags=["home"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    active_profile = db.scalar(
        select(RagRetrievalProfile)
        .where(RagRetrievalProfile.is_active.is_(True))
        .limit(1)
    )
    indexed_notes = int(
        db.scalar(
            select(func.count())
            .select_from(ObsidianNote)
            .where(ObsidianNote.index_status == "indexed")
        )
        or 0
    )
    index_anomalies = int(
        db.scalar(
            select(func.count())
            .select_from(ObsidianNote)
            .where(ObsidianNote.index_status != "indexed")
        )
        or 0
    )
    reminders = OfficialReminderService().summary(db)
    attention_count = sum(
        reminders[key]
        for key in (
            "due_count",
            "overdue_count",
            "never_checked_count",
            "error_count",
        )
    )
    recent_runs = list(
        db.scalars(
            select(AgentRun).order_by(AgentRun.created_at.desc()).limit(5)
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "active_profile": active_profile.version if active_profile else "未设置",
            "indexed_notes": indexed_notes,
            "index_anomalies": index_anomalies,
            "reminder_attention_count": attention_count,
            "pending_proposal_count": reminders["pending_proposal_count"],
            "recent_runs": recent_runs,
        },
    )
