from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.observability.errors import (
    OBSERVABILITY_DISABLED,
    OBSERVABILITY_NOT_CONFIGURED,
)


@dataclass(frozen=True, slots=True)
class ObservabilityConfiguration:
    provider: str
    requested: bool
    enabled: bool
    configured: bool
    safe_mode: bool
    hide_inputs: bool
    hide_outputs: bool
    api_key: str
    project_name: str
    workspace_id: str | None
    endpoint: str
    trace_schema_version: str
    environment: str
    app_name: str
    flush_timeout_seconds: float
    max_metadata_chars: int
    max_error_preview_chars: int
    failure_cooldown_seconds: int
    initial_status: str
    initial_error_code: str | None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        allow_test_environment: bool = False,
    ) -> "ObservabilityConfiguration":
        requested = settings.langsmith_observability_requested
        privacy_ready = bool(
            settings.langsmith_safe_mode
            and settings.langsmith_hide_inputs
            and settings.langsmith_hide_outputs
        )
        configured = bool(
            settings.langsmith_api_key.strip()
            and settings.langsmith_project.strip()
            and privacy_ready
        )
        enabled = bool(
            requested
            and configured
            and (settings.app_env != "test" or allow_test_environment)
        )
        if not requested or (settings.app_env == "test" and not allow_test_environment):
            status = "disabled"
            error_code = OBSERVABILITY_DISABLED
        elif not configured:
            status = "not_configured"
            error_code = OBSERVABILITY_NOT_CONFIGURED
        else:
            status = "ready"
            error_code = None
        return cls(
            provider="langsmith",
            requested=requested,
            enabled=enabled,
            configured=configured,
            safe_mode=settings.langsmith_safe_mode,
            hide_inputs=settings.langsmith_hide_inputs,
            hide_outputs=settings.langsmith_hide_outputs,
            api_key=settings.langsmith_api_key,
            project_name=settings.langsmith_project.strip() or "obsidian-learning-agent",
            workspace_id=settings.langsmith_workspace_id,
            endpoint=settings.langsmith_endpoint,
            trace_schema_version=settings.langsmith_trace_schema_version,
            environment=settings.langsmith_environment,
            app_name=settings.app_name,
            flush_timeout_seconds=settings.langsmith_flush_timeout_seconds,
            max_metadata_chars=settings.langsmith_max_metadata_chars,
            max_error_preview_chars=settings.langsmith_max_error_preview_chars,
            failure_cooldown_seconds=settings.langsmith_failure_cooldown_seconds,
            initial_status=status,
            initial_error_code=error_code,
        )
