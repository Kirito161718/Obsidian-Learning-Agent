from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.core.config import BASE_DIR, Settings, get_settings
from app.mcp.client_session import MCPClientSession
from app.mcp.configuration import MCPConfiguration, MCPServerConfig, load_mcp_configuration
from app.mcp.errors import MCPIntegrationError
from app.mcp.schemas import MCPServerDetail, MCPServerListResponse, MCPServerSummary, MCPToolCatalogItem
from app.mcp.security import SAFE_TOOL_NAME, safe_description, validate_and_copy_schema
from app.security.redaction import redact_value
from app.tools.registry import ToolRegistry, get_tool_registry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MCPServerState:
    config: MCPServerConfig
    status: str
    session: MCPClientSession | None = None
    tools: list[MCPToolCatalogItem] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    last_error_code: str | None = None
    last_connected_at: datetime | None = None


class MCPClientManager:
    def __init__(
        self,
        *,
        settings: Settings,
        configuration: MCPConfiguration | None,
        registry: ToolRegistry | None = None,
        configuration_status: str = "loaded",
        configuration_error_code: str | None = None,
        session_factory: Callable[[MCPServerConfig, Settings], MCPClientSession] = MCPClientSession,
    ) -> None:
        self.settings = settings
        self.registry = registry or get_tool_registry()
        self.configuration_status = configuration_status
        self.configuration_error_code = configuration_error_code
        self._session_factory = session_factory
        self._states: dict[str, MCPServerState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        for config in (configuration.servers if configuration else []):
            status = "disconnected" if config.enabled else "disabled"
            self._states[config.name] = MCPServerState(config=config, status=status)
            self._locks[config.name] = asyncio.Lock()

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        registry: ToolRegistry | None = None,
        project_root: Path = BASE_DIR,
    ) -> "MCPClientManager":
        settings = settings or get_settings()
        if not settings.mcp_enabled:
            return cls(
                settings=settings,
                configuration=None,
                registry=registry,
                configuration_status="disabled",
            )
        try:
            configuration = load_mcp_configuration(settings, project_root=project_root)
        except MCPIntegrationError as exc:
            status = "missing" if not settings.mcp_config_path.exists() else "error"
            logger.warning("MCP configuration unavailable: error_code=%s", exc.code)
            return cls(
                settings=settings,
                configuration=None,
                registry=registry,
                configuration_status=status,
                configuration_error_code=exc.code,
            )
        return cls(settings=settings, configuration=configuration, registry=registry)

    def list_response(self) -> MCPServerListResponse:
        return MCPServerListResponse(
            enabled=self.settings.mcp_enabled,
            configuration_status=self.configuration_status,
            configuration_error_code=self.configuration_error_code,
            servers=[self.summary(name) for name in sorted(self._states)],
        )

    def summary(self, server_name: str) -> MCPServerSummary:
        state = self._require_state(server_name)
        if state.status == "connected" and state.session is not None and not state.session.running:
            self._set_error(state, "mcp_connection_failed")
        return MCPServerSummary(
            name=state.config.name,
            enabled=state.config.enabled,
            transport=state.config.transport,
            status=state.status,
            connected=state.status == "connected" and bool(state.session and state.session.running),
            tool_count=len(state.tools),
            allowed_tool_count=sum(1 for tool in state.tools if tool.allowed),
            last_error_code=state.last_error_code,
            last_connected_at=state.last_connected_at,
        )

    def detail(self, server_name: str) -> MCPServerDetail:
        summary = self.summary(server_name)
        state = self._states[server_name]
        return MCPServerDetail(
            **summary.model_dump(),
            capabilities=redact_value(state.capabilities),
            tools=state.tools,
        )

    def list_tools(
        self,
        *,
        server_name: str | None = None,
        connected: bool | None = None,
        allowed: bool | None = None,
    ) -> list[MCPToolCatalogItem]:
        items = [tool for state in self._states.values() for tool in state.tools]
        if server_name is not None:
            items = [tool for tool in items if tool.server_name == server_name]
        if connected is not None:
            items = [tool for tool in items if tool.connected is connected]
        if allowed is not None:
            items = [tool for tool in items if tool.allowed is allowed]
        return sorted(items, key=lambda item: item.namespaced_name)

    async def connect(self, server_name: str) -> MCPServerDetail:
        state = self._require_state(server_name)
        if not self.settings.mcp_enabled:
            raise MCPIntegrationError("mcp_server_disabled", "MCP integration is disabled")
        if not state.config.enabled:
            raise MCPIntegrationError("mcp_server_disabled", "MCP server is disabled")
        async with self._locks[server_name]:
            if state.status == "connected" and state.session and state.session.running:
                return self.detail(server_name)
            state.status = "connecting"
            state.last_error_code = None
            session = self._session_factory(state.config, self.settings)
            state.session = session
            try:
                initialization = await asyncio.wait_for(
                    session.start(), timeout=state.config.timeout_seconds
                )
                state.capabilities = self._safe_capabilities(initialization)
                state.status = "connected"
                await self._refresh_locked(state)
            except TimeoutError as exc:
                await session.stop()
                self._set_error(state, "mcp_timeout")
                raise MCPIntegrationError("mcp_timeout", "MCP connection timed out") from exc
            except MCPIntegrationError as exc:
                await session.stop()
                self._set_error(state, exc.code)
                raise
            except Exception as exc:
                await session.stop()
                self._set_error(state, "mcp_connection_failed")
                raise MCPIntegrationError("mcp_connection_failed", "MCP connection failed") from exc
            state.last_connected_at = datetime.now(UTC)
            return self.detail(server_name)

    async def disconnect(self, server_name: str) -> MCPServerDetail:
        state = self._require_state(server_name)
        async with self._locks[server_name]:
            self.registry.unregister_provider(server_name)
            if state.session is not None:
                await state.session.stop()
            state.session = None
            state.status = "disabled" if not state.config.enabled else "disconnected"
            state.tools = [tool.model_copy(update={"connected": False, "allowed": False}) for tool in state.tools]
            return self.detail(server_name)

    async def refresh_tools(self, server_name: str) -> MCPServerDetail:
        state = self._require_state(server_name)
        async with self._locks[server_name]:
            if state.status == "connected" and state.session is not None and not state.session.running:
                self._set_error(state, "mcp_connection_failed")
            if state.status != "connected" or not state.session or not state.session.running:
                raise MCPIntegrationError("mcp_server_not_connected", "MCP server is not connected")
            await self._refresh_locked(state)
            return self.detail(server_name)

    async def call_tool(
        self,
        server_name: str,
        remote_tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> tuple[Any, str]:
        state = self._require_state(server_name)
        if state.status == "connected" and state.session is not None and not state.session.running:
            self._set_error(state, "mcp_connection_failed")
        if state.status != "connected" or not state.session or not state.session.running:
            raise MCPIntegrationError("mcp_server_not_connected", "MCP server is not connected")
        catalog = next((tool for tool in state.tools if tool.remote_tool_name == remote_tool_name), None)
        if catalog is None:
            raise MCPIntegrationError("mcp_tool_not_found", "MCP tool was not discovered")
        if not catalog.allowed:
            raise MCPIntegrationError("mcp_tool_not_allowed", "MCP tool is not allowed")
        request_id = str(uuid4())
        try:
            response = await asyncio.wait_for(
                state.session.call_tool(remote_tool_name, arguments, timeout_seconds=timeout_seconds),
                timeout=timeout_seconds + 1,
            )
            if isinstance(response, tuple) and len(response) == 2:
                result, protocol_request_id = response
                return result, str(protocol_request_id)[:100] or request_id
            return response, request_id
        except TimeoutError as exc:
            raise MCPIntegrationError("mcp_timeout", "MCP tool call timed out") from exc
        except MCPIntegrationError:
            raise
        except Exception as exc:
            self._set_error(state, "mcp_protocol_error")
            raise MCPIntegrationError("mcp_protocol_error", "MCP tool call failed") from exc

    async def close_all(self) -> None:
        for name in list(self._states):
            try:
                await self.disconnect(name)
            except Exception:
                logger.warning("MCP shutdown cleanup failed: server=%s", name)

    async def _refresh_locked(self, state: MCPServerState) -> None:
        assert state.session is not None
        limit = min(state.config.max_tools, self.settings.mcp_max_tools_per_server)
        discovered = await state.session.list_tools(limit=limit)
        tools: list[MCPToolCatalogItem] = []
        for remote in discovered:
            raw_name = str(getattr(remote, "name", ""))
            name = safe_description(raw_name, max_chars=100)
            allowed = raw_name in state.config.allowed_tools and bool(SAFE_TOOL_NAME.fullmatch(raw_name))
            try:
                schema, schema_hash = validate_and_copy_schema(
                    getattr(remote, "inputSchema", None), max_chars=self.settings.mcp_max_schema_chars
                )
            except MCPIntegrationError:
                schema, schema_hash, allowed = {}, "", False
            item = MCPToolCatalogItem(
                server_name=state.config.name,
                remote_tool_name=name,
                namespaced_name=f"mcp.{state.config.name}.{name}"[:100],
                description=safe_description(getattr(remote, "description", "")),
                input_schema=schema,
                allowed=allowed,
                risk_level="READ_ONLY",
                connected=True,
                schema_hash=schema_hash,
            )
            tools.append(item)

        self.registry.unregister_provider(state.config.name)
        from app.mcp.tool_adapter import MCPToolAdapter

        for item in tools:
            if item.allowed:
                adapter = MCPToolAdapter(self, item, state.config.timeout_seconds)
                self.registry.register(adapter.definition())
        state.tools = tools

    def _require_state(self, server_name: str) -> MCPServerState:
        state = self._states.get(server_name)
        if state is None:
            raise MCPIntegrationError("mcp_server_not_configured", "MCP server is not configured")
        return state

    @staticmethod
    def _safe_capabilities(initialization: Any) -> dict[str, Any]:
        capabilities = getattr(initialization, "capabilities", None)
        if capabilities is None:
            return {}
        if hasattr(capabilities, "model_dump"):
            return capabilities.model_dump(mode="json", exclude_none=True)
        return {}

    @staticmethod
    def _set_error(state: MCPServerState, code: str) -> None:
        state.status = "error"
        state.last_error_code = code
