from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.database import init_db
from app.mcp.client_manager import MCPClientManager
from app.observability import get_observability
from app.official_sources.errors import OfficialSourceError
from app.routers import admin, agent, documents, goals, learning, learning_status, mcp, obsidian, official_sources, qa, rag, rag_evaluation
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError

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
    app.state.mcp_manager = MCPClientManager.from_settings(settings)
    app.state.observability = get_observability()
    try:
        yield
    finally:
        await app.state.mcp_manager.close_all()
        await asyncio.to_thread(
            app.state.observability.flush,
            settings.langsmith_flush_timeout_seconds,
        )


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(OfficialSourceError)
async def official_source_error_handler(request: Request, exc: OfficialSourceError) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"error_code": exc.code, "message": str(exc)}},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    del request
    errors = [
        {key: value for key, value in item.items() if key not in {"input", "ctx"}}
        for item in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(ResourceNotFoundError)
async def resource_not_found_handler(
    request: Request, exc: ResourceNotFoundError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ServiceDatabaseError)
async def service_database_error_handler(
    request: Request, exc: ServiceDatabaseError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.include_router(documents.router)
app.include_router(rag.router)
app.include_router(qa.router)
app.include_router(goals.router)
app.include_router(learning_status.router)
app.include_router(obsidian.router)
app.include_router(official_sources.router)
app.include_router(learning.router)
app.include_router(admin.router)
app.include_router(agent.router)
app.include_router(rag_evaluation.router)
app.include_router(mcp.router)
