from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.core.config import Settings, get_settings
from app.observability import get_observability
from app.services.embedding_service import (
    embedding_runtime_status,
    get_embedding_service,
    shutdown_embedding_service,
)
from app.services.rerank_service import (
    get_rerank_service,
    reranker_runtime_status,
    shutdown_rerank_service,
)

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_status: dict[str, Any] = {
    "enabled": False,
    "attempted": False,
    "ready": False,
    "status": "not_loaded",
    "error_code": None,
    "embedding_initialization_ms": 0.0,
    "reranker_initialization_ms": 0.0,
    "total_ms": 0.0,
}


def prewarm_rag_models(
    settings: Settings | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    settings = settings or get_settings()
    enabled = bool(settings.rag_model_prewarm_enabled)
    if not enabled and not force:
        return _set_status(enabled=False, status="disabled", attempted=False)
    with _lock:
        if _status.get("ready"):
            return rag_model_runtime_status()
        started = time.perf_counter()
        result = {
            "enabled": enabled or force,
            "attempted": True,
            "ready": False,
            "status": "loading",
            "error_code": None,
            "embedding_initialization_ms": 0.0,
            "reranker_initialization_ms": 0.0,
            "total_ms": 0.0,
        }
        with get_observability().trace(
            name="ola.rag.performance.prewarm",
            run_type="chain",
            metadata={
                "flow_type": "rag_performance",
                "prewarm_enabled": enabled or force,
            },
            tags=["ola", "rag", "performance", "prewarm"],
        ) as span:
            try:
                embedding_started = time.perf_counter()
                embedding = get_embedding_service()
                embedding.embed_query("safe model readiness probe")
                result["embedding_initialization_ms"] = (
                    time.perf_counter() - embedding_started
                ) * 1000
                rerank_started = time.perf_counter()
                rerank_ready = get_rerank_service().prewarm()
                result["reranker_initialization_ms"] = (
                    time.perf_counter() - rerank_started
                ) * 1000
                result["ready"] = bool(
                    rerank_ready
                    and embedding_runtime_status()["status"] == "ready"
                )
                result["status"] = (
                    "ready" if rerank_ready else "degraded"
                )
            except Exception as exc:
                result["status"] = "failed_open"
                result["error_code"] = type(exc).__name__
                logger.warning(
                    "RAG model prewarm failed open: error_type=%s",
                    type(exc).__name__,
                )
            result["total_ms"] = (time.perf_counter() - started) * 1000
            span.update(
                {
                    "status": result["status"],
                    "error_code": result["error_code"],
                    "embedding_initialization_ms": result[
                        "embedding_initialization_ms"
                    ],
                    "reranker_initialization_ms": result[
                        "reranker_initialization_ms"
                    ],
                    "total_ms": result["total_ms"],
                }
            )
        return _set_status(**result)


def rag_model_runtime_status() -> dict[str, Any]:
    return {
        **dict(_status),
        "embedding": embedding_runtime_status(),
        "reranker": reranker_runtime_status(),
    }


def shutdown_rag_models() -> None:
    with _lock:
        shutdown_rerank_service()
        shutdown_embedding_service()
        _set_status(
            enabled=False,
            attempted=False,
            ready=False,
            status="not_loaded",
            error_code=None,
            embedding_initialization_ms=0.0,
            reranker_initialization_ms=0.0,
            total_ms=0.0,
        )


def _set_status(**values: Any) -> dict[str, Any]:
    _status.update(values)
    return dict(_status)
