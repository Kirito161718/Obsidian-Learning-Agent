from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings

router = APIRouter(tags=["admin"])
settings = get_settings()
templates = Jinja2Templates(directory=settings.template_dir)


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_dashboard(request: Request) -> HTMLResponse:
    if not settings.admin_enabled:
        raise HTTPException(status_code=404, detail="Admin dashboard is disabled")
    return templates.TemplateResponse(request, "admin.html")


@router.get("/admin/status", include_in_schema=False)
def admin_status() -> dict[str, object]:
    if not settings.admin_enabled:
        raise HTTPException(status_code=404, detail="Admin dashboard is disabled")
    return {
        "rerank_enabled": settings.rerank_enabled,
        "rerank_model": safe_model_name(settings.rerank_model),
        "rerank_top_n": settings.rerank_top_n,
    }


def safe_model_name(model_name: str) -> str:
    model_path = Path(model_name)
    if model_path.is_absolute():
        return model_path.name
    return model_name
