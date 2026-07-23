from __future__ import annotations


OBSERVABILITY_DISABLED = "observability_disabled"
OBSERVABILITY_NOT_CONFIGURED = "observability_not_configured"
OBSERVABILITY_CLIENT_INIT_FAILED = "observability_client_init_failed"
OBSERVABILITY_WORKSPACE_REQUIRED = "observability_workspace_required"
OBSERVABILITY_EXPORT_FAILED = "observability_export_failed"
OBSERVABILITY_AUTH_FAILED = "observability_auth_failed"
OBSERVABILITY_RATE_LIMITED = "observability_rate_limited"
OBSERVABILITY_TIMEOUT = "observability_timeout"
OBSERVABILITY_FLUSH_TIMEOUT = "observability_flush_timeout"
OBSERVABILITY_METADATA_INVALID = "observability_metadata_invalid"
OBSERVABILITY_METADATA_TRUNCATED = "observability_metadata_truncated"
OBSERVABILITY_CONTEXT_LOST = "observability_context_lost"


def classify_observability_error(exc: BaseException) -> str:
    name = type(exc).__name__.casefold()
    message = str(exc).casefold()
    if "timeout" in name or "timed out" in message:
        return OBSERVABILITY_TIMEOUT
    if "401" in message or "403" in message or "unauthorized" in message or "forbidden" in message:
        return OBSERVABILITY_AUTH_FAILED
    if "429" in message or "rate limit" in message:
        return OBSERVABILITY_RATE_LIMITED
    if "workspace" in message:
        return OBSERVABILITY_WORKSPACE_REQUIRED
    return OBSERVABILITY_EXPORT_FAILED
