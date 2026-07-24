from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.core.config import Settings, get_settings
from app.observability import get_observability
from app.services.embedding_service import get_embedding_service
from app.services.rerank_service import get_rerank_service

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_status: dict[str, Any] = {
    "enabled": False,
    "attempted": False,
    "ready": False,
    "status": "disabled",
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
        return _set_status(enabled=False, status="disabled")
    with _lock:
        if _status.get("ready"):
            return dict(_status)
        started = time.perf_counter()
        result = {
            "enabled": enabled or force,
            "attempted": True,
            "ready": False,
            "status": "running",
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
                get_embedding_service().vector_size
                result["embedding_initialization_ms"] = (
                    time.perf_counter() - embedding_started
                ) * 1000
                rerank_started = time.perf_counter()
                rerank_ready = get_rerank_service().prewarm()
                result["reranker_initialization_ms"] = (
                    time.perf_counter() - rerank_started
                ) * 1000
                result["ready"] = bool(rerank_ready)
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
    return dict(_status)


def _set_status(**values: Any) -> dict[str, Any]:
    _status.update(values)
    return dict(_status)
