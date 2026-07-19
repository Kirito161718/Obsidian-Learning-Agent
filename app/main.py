from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.database import init_db
from app.routers import admin, documents, goals, learning, learning_status, obsidian, qa, rag

setup_logging()
settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.app_env != "test":
        try:
            init_db()
        except SQLAlchemyError:
            logger.exception("Database initialization failed")
            if settings.database_required_on_startup:
                raise
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(documents.router)
app.include_router(rag.router)
app.include_router(qa.router)
app.include_router(goals.router)
app.include_router(learning_status.router)
app.include_router(obsidian.router)
app.include_router(learning.router)
app.include_router(admin.router)
