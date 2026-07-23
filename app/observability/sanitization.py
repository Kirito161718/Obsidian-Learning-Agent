from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.security.redaction import redact_value, stable_hash

MAX_TAGS = 20
MAX_TAG_CHARS = 80
MAX_LIST_ITEMS = 50
MAX_DEPTH = 6

_BLOCKED_KEYS = {
    "answer",
    "authorization",
    "backup",
    "backup_content",
    "chunk",
    "chunks",
    "content",
    "cookie",
    "database_url",
    "diff",
    "input",
    "inputs",
    "memory",
    "message",
    "messages",
    "note_body",
    "output",
    "outputs",
    "password",
    "prompt",
    "proposal",
    "query",
    "question",
    "raw",
    "snapshot",
    "state",
    "system_prompt",
    "token",
    "traceback",
}
_SAFE_EXACT_STRING_KEYS = {
    "app_name",
    "caller_type",
    "checkpoint_thread_id",
    "citation_mode_enum",
    "depth_enum",
    "endpoint_type",
    "environment",
    "finish_reason",
    "flow_type",
    "language",
    "ls_model_name",
    "ls_provider",
    "mode",
    "node_name",
    "observability_provider",
    "project_name",
    "provider",
    "provider_name",
    "risk_level",
    "style_enum",
    "tool_name",
    "tool_source",
    "trace_schema_version",
}
_SAFE_SUFFIXES = (
    "_code",
    "_enum",
    "_hash",
    "_id",
    "_name",
    "_reason",
    "_status",
    "_type",
    "_version",
)
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_PHONE = re.compile(
    r"(?<![0-9A-Za-z-])(?:\+?\d(?:[\s().-]*\d){7,})(?![0-9A-Za-z-])"
)
_TRACEBACK = re.compile(r"(?is)traceback\s*\(most recent call last\).*")
_TAG = re.compile(r"[^A-Za-z0-9_.:@/-]+")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@/-]{1,500}$")


def sanitize_metadata(value: Mapping[str, Any] | None, max_chars: int) -> dict[str, Any]:
    try:
        cleaned = _sanitize_mapping(value or {}, depth=0)
    except Exception:
        return {"observability_error_code": "observability_metadata_invalid"}
    serialized = _json(cleaned)
    if len(serialized) <= max_chars:
        return cleaned

    priority = (
        "trace_schema_version",
        "app_name",
        "environment",
        "flow_type",
        "internal_run_id",
        "session_id",
        "qa_history_id",
        "proposal_id",
        "approval_request_id",
        "official_source_id",
        "snapshot_id",
        "tool_execution_id",
        "checkpoint_thread_id",
        "status",
        "error_code",
    )
    result: dict[str, Any] = {}
    for key in (*priority, *sorted(cleaned)):
        if key in result or key not in cleaned:
            continue
        candidate = {**result, key: cleaned[key]}
        if len(_json({**candidate, "metadata_truncated": True})) <= max_chars:
            result = candidate
    result["metadata_truncated"] = True
    result["metadata_original_chars"] = len(serialized)
    return result


def sanitize_tags(tags: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    for raw in tags or ():
        value = _TAG.sub("-", str(raw)).strip("-")[:MAX_TAG_CHARS]
        if value and value not in result:
            result.append(value)
        if len(result) >= MAX_TAGS:
            break
    return result


def safe_error_metadata(exc: BaseException | None, *, error_code: str | None = None) -> dict[str, Any]:
    if exc is None and not error_code:
        return {}
    result: dict[str, Any] = {}
    if error_code:
        result["error_code"] = str(error_code)[:100]
    if exc is not None:
        result["error_type"] = type(exc).__name__[:100]
        result["error_char_count"] = len(str(exc))
    return result


def _sanitize_mapping(value: Mapping[str, Any], *, depth: int) -> dict[str, Any]:
    if depth >= MAX_DEPTH:
        return {"truncated": True}
    result: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = str(raw_key)[:100]
        normalized = key.casefold().replace("-", "_")
        if normalized in _BLOCKED_KEYS:
            result[f"{normalized}_redacted"] = True
            if isinstance(item, str):
                result[f"{normalized}_char_count"] = len(item)
            elif isinstance(item, (list, tuple, set, dict)):
                result[f"{normalized}_count"] = len(item)
            continue
        result[key] = _sanitize_item(key, item, depth=depth + 1)
    return result


def _sanitize_item(key: str, value: Any, *, depth: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, depth=depth)
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        safe = [_sanitize_item(key, item, depth=depth + 1) for item in items[:MAX_LIST_ITEMS]]
        if len(items) > MAX_LIST_ITEMS:
            safe.append({"truncated": True, "original_count": len(items)})
        return safe
    text = _scrub_text(str(value))
    normalized = key.casefold().replace("-", "_")
    if normalized in _SAFE_EXACT_STRING_KEYS or normalized.endswith(_SAFE_SUFFIXES):
        if _SAFE_IDENTIFIER.fullmatch(text) and not _looks_like_absolute_path(text):
            return text[:500]
        return {
            "sha256": stable_hash(text),
            "char_count": len(text),
            "redacted": True,
        }
    return {
        "sha256": stable_hash(text),
        "char_count": len(text),
        "redacted": True,
    }


def _scrub_text(value: str) -> str:
    value = _TRACEBACK.sub("<TRACEBACK_REDACTED>", value)
    value = _EMAIL.sub("<EMAIL_REDACTED>", value)
    value = _PHONE.sub("<PHONE_REDACTED>", value)
    redacted = redact_value(value, max_string_chars=500)
    return str(redacted)


def _looks_like_absolute_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return bool(
        normalized.startswith(("/", "//"))
        or re.match(r"^[A-Za-z]:/", normalized)
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
