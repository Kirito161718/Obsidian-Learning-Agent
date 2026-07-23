from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability.configuration import ObservabilityConfiguration
from app.observability.context import CURRENT_SPAN, SpanHandle
from app.observability.errors import (
    OBSERVABILITY_CLIENT_INIT_FAILED,
    OBSERVABILITY_DISABLED,
    OBSERVABILITY_EXPORT_FAILED,
    OBSERVABILITY_FLUSH_TIMEOUT,
    classify_observability_error,
)
from app.observability.sanitization import (
    safe_error_metadata,
    sanitize_metadata,
    sanitize_tags,
)

logger = logging.getLogger(__name__)


class _SuppressLangSmithSdkDiagnostics(logging.Filter):
    """Prevent the SDK from logging endpoints, masked keys, trace IDs, or payload sizes."""

    def filter(self, record: logging.LogRecord) -> bool:
        del record
        return False


class LangSmithCollector:
    def __init__(self, configuration: ObservabilityConfiguration, metadata_processor, error_callback) -> None:
        self._install_safe_sdk_logging()
        from langsmith import Client

        self.configuration = configuration
        self._trace = None
        self._tracing_context = None
        self.client = Client(
            api_url=configuration.endpoint,
            api_key=configuration.api_key,
            workspace_id=configuration.workspace_id,
            hide_inputs=True,
            hide_outputs=True,
            hide_metadata=metadata_processor,
            omit_traced_runtime_info=True,
            tracing_error_callback=error_callback,
        )

    @staticmethod
    def _install_safe_sdk_logging() -> None:
        for logger_name in (
            "langsmith",
            "langsmith.client",
            "langsmith.run_helpers",
            "langsmith.run_trees",
        ):
            sdk_logger = logging.getLogger(logger_name)
            if not any(
                isinstance(item, _SuppressLangSmithSdkDiagnostics)
                for item in sdk_logger.filters
            ):
                sdk_logger.addFilter(_SuppressLangSmithSdkDiagnostics())

    @contextmanager
    def open_span(
        self,
        *,
        name: str,
        run_type: str,
        metadata: dict[str, Any],
        tags: list[str],
        parent: Any | None,
    ) -> Iterator[Any]:
        if self._trace is None or self._tracing_context is None:
            from langsmith import trace, tracing_context

            self._trace = trace
            self._tracing_context = tracing_context
        with self._tracing_context(
            enabled=True,
            project_name=self.configuration.project_name,
            client=self.client,
        ):
            with self._trace(
                name,
                run_type=run_type,
                inputs={},
                metadata=metadata,
                tags=tags,
                parent=parent,
                project_name=self.configuration.project_name,
                client=self.client,
            ) as run:
                yield run

    def flush(self, timeout: float) -> None:
        self.client.flush(timeout=timeout)


class ObservabilityManager:
    def __init__(
        self,
        configuration: ObservabilityConfiguration,
        *,
        collector: Any | None = None,
    ) -> None:
        self.configuration = configuration
        self._collector = collector
        self._lock = threading.Lock()
        self._status = configuration.initial_status
        self._last_success_at: datetime | None = None
        self._last_error_code = configuration.initial_error_code
        self._failed_count = 0
        self._cooldown_until: datetime | None = None
        if self._collector is None and configuration.enabled:
            try:
                self._collector = LangSmithCollector(
                    configuration,
                    self.safe_metadata,
                    self._background_error,
                )
            except Exception:
                self._record_failure(OBSERVABILITY_CLIENT_INIT_FAILED)
                logger.warning(
                    "Observability client initialization failed: error_code=%s",
                    OBSERVABILITY_CLIENT_INIT_FAILED,
                )

    @contextmanager
    def trace(
        self,
        *,
        name: str,
        run_type: str = "chain",
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[SpanHandle]:
        if not self._can_trace():
            parent = CURRENT_SPAN.get()
            yield SpanHandle(
                name=name,
                run_type=run_type,
                external_trace_id=None,
                parent_trace_id=parent.external_trace_id if parent else None,
                status=self._status,
                _manager=self,
            )
            return

        parent = CURRENT_SPAN.get()
        base = {
            "trace_schema_version": self.configuration.trace_schema_version,
            "app_name": self.configuration.app_name,
            "environment": self.configuration.environment,
            "status": "running",
            **(metadata or {}),
        }
        safe_metadata = self.safe_metadata(base)
        safe_tags = sanitize_tags(tags)
        opened = None
        backend = None
        try:
            opened = self._collector.open_span(
                name=name,
                run_type=run_type,
                metadata=safe_metadata,
                tags=safe_tags,
                parent=parent.backend_span if parent and parent.enabled else None,
            )
            backend = opened.__enter__()
        except Exception as exc:
            self._record_failure(classify_observability_error(exc))
            logger.warning(
                "Observability span creation failed: error_code=%s",
                self._last_error_code or OBSERVABILITY_EXPORT_FAILED,
            )
            yield SpanHandle(
                name=name,
                run_type=run_type,
                external_trace_id=None,
                parent_trace_id=parent.external_trace_id if parent else None,
                status="failed",
                _manager=self,
            )
            return

        external_id = str(getattr(backend, "trace_id", None) or getattr(backend, "id", ""))
        handle = SpanHandle(
            name=name,
            run_type=run_type,
            external_trace_id=external_id or None,
            parent_trace_id=parent.external_trace_id if parent else None,
            status="pending",
            backend_span=backend,
            metadata=safe_metadata,
            _manager=self,
        )
        token = CURRENT_SPAN.set(handle)
        started = time.perf_counter()
        business_error: BaseException | None = None
        business_traceback = None
        try:
            try:
                yield handle
            except BaseException as exc:
                business_error = exc
                business_traceback = exc.__traceback__
        finally:
            interrupted = (
                business_error is not None
                and type(business_error).__name__ == "GraphInterrupt"
            )
            terminal = {
                "status": "pending" if interrupted else "failed" if business_error else "succeeded",
                "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
                **safe_error_metadata(
                    None if interrupted else business_error,
                    error_code=(
                        None
                        if interrupted or business_error is None
                        else type(business_error).__name__
                    ),
                ),
            }
            self.update_span(handle, metadata=terminal)
            CURRENT_SPAN.reset(token)
            try:
                opened.__exit__(None, None, None)
                handle.status = "submitted"
                self._record_submission()
            except Exception as exc:
                handle.status = "failed"
                self._record_failure(classify_observability_error(exc))
                logger.warning(
                    "Observability span submission failed: error_code=%s",
                    self._last_error_code or OBSERVABILITY_EXPORT_FAILED,
                )
        if business_error is not None:
            raise business_error.with_traceback(business_traceback)

    @contextmanager
    def suppress_automatic_tracing(self) -> Iterator[None]:
        """Disable framework auto-tracing while explicit safe spans stay available."""
        try:
            from langsmith import tracing_context
        except Exception:
            yield
            return
        with tracing_context(enabled=False):
            yield

    def trace_async(self, **kwargs: Any):
        return _AsyncTraceContext(self, kwargs)

    def update_current_span(
        self,
        metadata: dict[str, Any] | None = None,
        *,
        tags: list[str] | None = None,
    ) -> None:
        span = CURRENT_SPAN.get()
        if span is not None:
            self.update_span(span, metadata=metadata, tags=tags)

    def update_span(
        self,
        span: SpanHandle,
        *,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        if not span.enabled:
            return
        combined = {**span.metadata, **(metadata or {})}
        safe = self.safe_metadata(combined)
        span.metadata = safe
        try:
            if hasattr(span.backend_span, "add_metadata"):
                span.backend_span.add_metadata(safe)
            if tags and hasattr(span.backend_span, "add_tags"):
                span.backend_span.add_tags(sanitize_tags(tags))
        except Exception as exc:
            self._record_failure(classify_observability_error(exc))

    def safe_metadata(self, value: dict[str, Any] | None) -> dict[str, Any]:
        return sanitize_metadata(value, self.configuration.max_metadata_chars)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "provider": self.configuration.provider,
                "enabled": self.configuration.enabled,
                "configured": self.configuration.configured,
                "safe_mode": self.configuration.safe_mode,
                "project_name": self.configuration.project_name,
                "workspace_configured": bool(self.configuration.workspace_id),
                "trace_schema_version": self.configuration.trace_schema_version,
                "status": self._status,
                "last_success_at": self._last_success_at,
                "last_error_code": self._last_error_code,
                "dropped_or_failed_count": self._failed_count,
            }

    def flush(self, timeout: float | None = None) -> bool:
        if self._collector is None:
            return True
        limit = timeout or self.configuration.flush_timeout_seconds
        result: dict[str, Any] = {}

        def run() -> None:
            try:
                self._collector.flush(limit)
                result["ok"] = True
            except Exception as exc:
                result["error_code"] = classify_observability_error(exc)

        thread = threading.Thread(target=run, name="ola-langsmith-flush", daemon=True)
        thread.start()
        thread.join(limit)
        if thread.is_alive():
            self._record_failure(OBSERVABILITY_FLUSH_TIMEOUT)
            return False
        if not result.get("ok"):
            self._record_failure(result.get("error_code") or OBSERVABILITY_EXPORT_FAILED)
            return False
        return True

    def _can_trace(self) -> bool:
        if not self.configuration.enabled or self._collector is None:
            return False
        with self._lock:
            if self._cooldown_until and datetime.now(UTC) < self._cooldown_until:
                return False
        return True

    def _record_submission(self) -> None:
        with self._lock:
            self._status = "submitted"
            self._last_success_at = datetime.now(UTC)
            self._last_error_code = None
            self._cooldown_until = None

    def _record_failure(self, error_code: str) -> None:
        with self._lock:
            self._status = "failed"
            self._last_error_code = error_code
            self._failed_count += 1
            self._cooldown_until = datetime.now(UTC) + timedelta(
                seconds=self.configuration.failure_cooldown_seconds
            )

    def _background_error(self, exc: Exception) -> None:
        self._record_failure(classify_observability_error(exc))
        logger.warning(
            "Observability background export failed: error_code=%s",
            self._last_error_code or OBSERVABILITY_EXPORT_FAILED,
        )


class _AsyncTraceContext:
    def __init__(self, manager: ObservabilityManager, kwargs: dict[str, Any]) -> None:
        self._context = manager.trace(**kwargs)

    async def __aenter__(self) -> SpanHandle:
        return self._context.__enter__()

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return bool(self._context.__exit__(exc_type, exc, traceback))
