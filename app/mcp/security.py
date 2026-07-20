from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator, ValidationError as JSONSchemaValidationError, SchemaError

from app.mcp.errors import MCPIntegrationError
from app.security.redaction import redact_value, safe_text_preview, stable_hash

SAFE_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_and_copy_schema(schema: object, *, max_chars: int) -> tuple[dict[str, Any], str]:
    if not isinstance(schema, dict):
        raise MCPIntegrationError("mcp_schema_invalid", "MCP tool input schema must be an object")
    try:
        serialized = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MCPIntegrationError("mcp_schema_invalid", "MCP tool input schema is not JSON serializable") from exc
    if len(serialized) > max_chars:
        raise MCPIntegrationError("mcp_schema_invalid", "MCP tool input schema is too large")
    _check_depth(schema, depth=0)
    _check_local_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise MCPIntegrationError("mcp_schema_invalid", "MCP tool input schema is invalid") from exc
    copied = _sanitize_schema_metadata(json.loads(serialized))
    return copied, stable_hash(copied)


def validate_tool_input(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        Draft202012Validator(schema).validate(instance)
    except JSONSchemaValidationError as exc:
        raise MCPIntegrationError("mcp_input_invalid", "MCP tool input validation failed") from exc


def safe_description(value: object, *, max_chars: int = 500) -> str:
    return safe_text_preview(value, max_chars)


def normalize_tool_result(result: Any, *, max_chars: int) -> dict[str, Any]:
    if bool(getattr(result, "isError", False)):
        raise MCPIntegrationError("mcp_server_error", "MCP server returned a tool error")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: Any = structured
    else:
        payload = []
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", None) == "text":
                payload.append({"type": "text", "text": getattr(block, "text", "")})
            else:
                payload.append({"type": str(getattr(block, "type", "unknown"))})
    safe_payload = redact_value(payload)
    serialized = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return {"data": safe_payload, "truncated": False, "chars": len(serialized)}
    return {
        "data_preview": safe_text_preview(serialized, max_chars),
        "truncated": True,
        "chars": len(serialized),
        "error_code": "mcp_output_too_large",
    }


def _check_depth(value: object, *, depth: int) -> None:
    if depth > 30:
        raise MCPIntegrationError("mcp_schema_invalid", "MCP tool schema nesting is too deep")
    if isinstance(value, dict):
        for child in value.values():
            _check_depth(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_depth(child, depth=depth + 1)


def _sanitize_schema_metadata(value: object, *, parent_key: str = "") -> object:
    if isinstance(value, dict):
        return {str(key): _sanitize_schema_metadata(child, parent_key=str(key)) for key, child in value.items()}
    if isinstance(value, list):
        return [_sanitize_schema_metadata(child, parent_key=parent_key) for child in value]
    if isinstance(value, str) and parent_key in {"description", "title", "$comment", "default", "examples"}:
        return safe_text_preview(value, 500)
    return value


def _check_local_refs(schema: dict[str, Any]) -> None:
    defs = schema.get("$defs", {})
    if defs is not None and not isinstance(defs, dict):
        raise MCPIntegrationError("mcp_schema_invalid", "MCP schema $defs must be an object")

    graph: dict[str, set[str]] = {}

    def collect(value: object, owner: str = "#") -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                    raise MCPIntegrationError("mcp_schema_invalid", "Only local MCP schema references are supported")
                target = reference.removeprefix("#/$defs/")
                if target not in defs:
                    raise MCPIntegrationError("mcp_schema_invalid", "MCP schema reference target is missing")
                graph.setdefault(owner, set()).add(target)
            for key, child in value.items():
                child_owner = key if owner == "$defs" else owner
                collect(child, child_owner)
        elif isinstance(value, list):
            for child in value:
                collect(child, owner)

    for name, definition in defs.items():
        collect(definition, str(name))
    collect({key: value for key, value in schema.items() if key != "$defs"}, "#")

    def visit(node: str, active: set[str], done: set[str]) -> None:
        if node in active:
            raise MCPIntegrationError("mcp_schema_invalid", "Recursive MCP schemas are not supported")
        if node in done:
            return
        active.add(node)
        for target in graph.get(node, set()):
            visit(target, active, done)
        active.remove(node)
        done.add(node)

    completed: set[str] = set()
    for name in defs:
        visit(str(name), set(), completed)
