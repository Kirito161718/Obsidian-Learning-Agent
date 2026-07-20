from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.mcp.errors import MCPIntegrationError
from app.mcp.schemas import MCPAnyInput, MCPAnyOutput, MCPToolCatalogItem
from app.mcp.security import normalize_tool_result, validate_tool_input
from app.tools.definitions import CallerType, RiskLevel, ToolDefinition
from app.tools.executor import ToolExecutionContext, ToolExecutionError

if TYPE_CHECKING:
    from app.mcp.client_manager import MCPClientManager


class MCPToolAdapter:
    def __init__(self, manager: "MCPClientManager", catalog: MCPToolCatalogItem, timeout_seconds: int) -> None:
        self.manager = manager
        self.catalog = catalog
        self.timeout_seconds = timeout_seconds

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.catalog.namespaced_name,
            description=self.catalog.description,
            risk_level=RiskLevel.READ_ONLY,
            input_schema=MCPAnyInput,
            output_schema=MCPAnyOutput,
            callable=self.execute,
            enabled=True,
            idempotent=True,
            # The protocol layer owns the configured timeout; leave a bounded
            # cleanup margin so ToolExecutor does not cancel it first.
            timeout_seconds=self.timeout_seconds + 5,
            allowed_callers=frozenset({CallerType.user}),
            tool_source="mcp",
            provider_name=self.catalog.server_name,
            remote_tool_name=self.catalog.remote_tool_name,
            input_json_schema=self.catalog.input_schema,
            output_json_schema={"type": "object"},
        )

    async def execute(self, payload: MCPAnyInput, context: ToolExecutionContext) -> dict[str, Any]:
        raw_input = payload.model_dump(mode="json")
        serialized = json.dumps(raw_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(serialized) > self.manager.settings.mcp_max_input_chars:
            raise ToolExecutionError("mcp_input_invalid", "MCP tool input is too large")
        try:
            validate_tool_input(raw_input, self.catalog.input_schema)
            result, request_id = await self.manager.call_tool(
                self.catalog.server_name,
                self.catalog.remote_tool_name,
                raw_input,
                timeout_seconds=self.timeout_seconds,
            )
            if context.audit_record is not None:
                context.audit_record.protocol_request_id = request_id[:100]
            return normalize_tool_result(result, max_chars=self.manager.settings.mcp_max_output_chars)
        except MCPIntegrationError as exc:
            raise ToolExecutionError(exc.code, str(exc)) from exc
