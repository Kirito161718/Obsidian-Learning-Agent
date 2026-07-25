from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.database import SessionLocal
from app.db.models import RagRetrievalProfile
from app.observability import get_observability
from app.services.rag_model_runtime import rag_model_runtime_status
from app.services.vector_service import get_qdrant_client


def collect_system_status(
    db: Session | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run bounded, read-only checks without loading models or calling an LLM."""
    settings = settings or get_settings()
    owned_db = db is None
    session = db or SessionLocal()
    database = {"status": "unavailable", "error_code": None}
    alembic_revision: str | None = None
    active_profile: str | None = None
    try:
        session.execute(text("SELECT 1"))
        database["status"] = "healthy"
        try:
            alembic_revision = session.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar_one_or_none()
        except Exception:
            alembic_revision = None
        try:
            active_profile = session.scalar(
                select(RagRetrievalProfile.version)
                .where(RagRetrievalProfile.is_active.is_(True))
                .limit(1)
            )
        except Exception:
            active_profile = None
    except Exception as exc:
        database["error_code"] = type(exc).__name__
    finally:
        if owned_db:
            session.close()

    qdrant: dict[str, Any] = {
        "status": "unavailable",
        "collection": settings.qdrant_collection,
        "points": None,
        "error_code": None,
    }
    try:
        info = get_qdrant_client().get_collection(settings.qdrant_collection)
        qdrant.update(
            status="healthy",
            points=int(getattr(info, "points_count", 0) or 0),
        )
    except Exception as exc:
        qdrant["error_code"] = type(exc).__name__

    vault_enabled = bool(settings.obsidian_enabled)
    vault_exists = bool(
        settings.obsidian_vault_path and settings.obsidian_vault_path.is_dir()
    )
    vault = {
        "status": (
            "disabled"
            if not vault_enabled
            else "healthy"
            if vault_exists
            else "unavailable"
        ),
        "enabled": vault_enabled,
        "markdown_files": (
            sum(1 for _ in settings.obsidian_vault_path.rglob("*.md"))
            if vault_exists and settings.obsidian_vault_path
            else 0
        ),
    }

    model_runtime = rag_model_runtime_status()
    embedding = {
        **model_runtime["embedding"],
        "cache_available": _model_cache_available(
            settings.embedding_model_name, settings
        ),
    }
    reranker = {
        **model_runtime["reranker"],
        "cache_available": _model_cache_available(
            settings.rerank_model, settings
        ),
    }
    observability = get_observability().status()
    langsmith = {
        "status": (
            "disabled"
            if not observability["enabled"]
            else "degraded"
            if observability["status"] == "failed"
            else "healthy"
        ),
        "configured": bool(observability["configured"]),
        "safe_mode": bool(observability["safe_mode"]),
        "last_error_code": observability["last_error_code"],
    }
    dependencies_healthy = (
        database["status"] == "healthy"
        and qdrant["status"] == "healthy"
        and vault["status"] in {"healthy", "disabled"}
    )
    return {
        "application": {
            "status": "healthy" if dependencies_healthy else "degraded",
            "environment": settings.app_env,
        },
        "database": database,
        "qdrant": qdrant,
        "vault": vault,
        "embedding": embedding,
        "reranker": reranker,
        "deepseek_configured": bool(
            settings.deepseek_api_key
            and settings.deepseek_api_key != "your_deepseek_api_key"
        ),
        "langsmith": langsmith,
        "active_rag_profile": active_profile,
        "alembic_revision": alembic_revision,
    }


def _model_cache_available(model_name: str, settings: Settings) -> bool:
    candidate = Path(model_name)
    if not candidate.is_absolute():
        candidate = settings.model_cache_dir.parent / candidate
    if candidate.exists():
        return True
    return bool(
        settings.model_cache_dir.exists()
        and any(settings.model_cache_dir.iterdir())
    )


if __name__ == "__main__":
    print(json.dumps(collect_system_status(), ensure_ascii=False))
