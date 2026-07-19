import hashlib
import json
import re
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "api_key",
    "apikey",
    "token",
    "secret",
    "authorization",
    "cookie",
    "database_url",
    "dsn",
}
WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?<![\w])(?:[a-z]:[\\/])(?:[^\s\"'<>|]+[\\/]?)+")
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|api[_-]?key|token|secret|authorization|cookie|database[_-]?url|dsn)\s*[:=]\s*[^\s,;]+"
)
URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s:]+(?::[^@\s]*)?@")
HOME_PATH = str(Path.home()).replace("\\", "/")


def stable_hash(value: Any) -> str:
    serialized = json.dumps(_canonical_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def redact_value(value: Any, *, max_string_chars: int | None = None) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            result[key_text] = "***" if normalized in SENSITIVE_KEYS else redact_value(
                item, max_string_chars=max_string_chars
            )
        return result
    if isinstance(value, (list, tuple, set)):
        return [redact_value(item, max_string_chars=max_string_chars) for item in value]
    if isinstance(value, str):
        return _redact_string(value, max_string_chars)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value), max_string_chars)


def summarize_value(value: Any, max_chars: int) -> dict[str, Any]:
    redacted = redact_value(value, max_string_chars=max_chars)
    serialized = json.dumps(redacted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return {"value": redacted, "chars": len(serialized), "truncated": False}
    preview = serialized[: max(0, max_chars - 3)] + "..."
    return {"value_preview": preview, "chars": len(serialized), "truncated": True}


def safe_text_preview(value: object, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    redacted = _redact_string(text, None)
    return redacted[:max_chars]


def _redact_string(value: str, max_chars: int | None) -> str:
    normalized = value.replace("\\", "/")
    normalized = SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=***", normalized)
    normalized = URL_CREDENTIALS.sub(r"\1***@", normalized)
    if HOME_PATH and HOME_PATH.lower() in normalized.lower():
        start = normalized.lower().find(HOME_PATH.lower())
        normalized = normalized[:start] + "<HOME>" + normalized[start + len(HOME_PATH) :]
    normalized = WINDOWS_ABSOLUTE_PATH.sub("<ABSOLUTE_PATH>", normalized)
    if max_chars is not None and len(normalized) > max_chars:
        normalized = normalized[: max(0, max_chars - 3)] + "..."
    return normalized


def _canonical_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_canonical_safe(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)
