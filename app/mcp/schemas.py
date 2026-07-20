from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MCPToolCatalogItem(BaseModel):
    server_name: str
    remote_tool_name: str
    namespaced_name: str
    description: str
    input_schema: dict[str, Any]
    allowed: bool
    risk_level: Literal["READ_ONLY"] = "READ_ONLY"
    connected: bool
    schema_hash: str


class MCPServerSummary(BaseModel):
    name: str
    enabled: bool
    transport: Literal["stdio", "streamable_http"]
    status: Literal["disabled", "disconnected", "connecting", "connected", "error"]
    connected: bool
    tool_count: int
    allowed_tool_count: int
    last_error_code: str | None = None
    last_connected_at: datetime | None = None


class MCPServerDetail(MCPServerSummary):
    capabilities: dict[str, Any] = Field(default_factory=dict)
    tools: list[MCPToolCatalogItem] = Field(default_factory=list)


class MCPServerListResponse(BaseModel):
    enabled: bool
    configuration_status: Literal["disabled", "loaded", "error", "missing"]
    configuration_error_code: str | None = None
    servers: list[MCPServerSummary] = Field(default_factory=list)


class MCPActionResponse(BaseModel):
    server: MCPServerDetail


class MCPToolListResponse(BaseModel):
    items: list[MCPToolCatalogItem]


class MCPAnyInput(BaseModel):
    model_config = ConfigDict(extra="allow")


class MCPAnyOutput(BaseModel):
    model_config = ConfigDict(extra="allow")
