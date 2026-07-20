from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.mcp.client_manager import MCPClientManager
from app.mcp.errors import MCPIntegrationError
from app.mcp.schemas import (
    MCPActionResponse,
    MCPServerDetail,
    MCPServerListResponse,
    MCPToolListResponse,
)

router = APIRouter(prefix="/agent/mcp", tags=["agent-mcp"])


def get_mcp_manager(request: Request) -> MCPClientManager:
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None:  # pragma: no cover - lifespan invariant
        raise HTTPException(status_code=503, detail={"error_code": "mcp_server_not_configured"})
    return manager


@router.get("/servers", response_model=MCPServerListResponse)
def list_servers(manager: MCPClientManager = Depends(get_mcp_manager)) -> MCPServerListResponse:
    return manager.list_response()


@router.get("/servers/{server_name}", response_model=MCPServerDetail)
def get_server(server_name: str, manager: MCPClientManager = Depends(get_mcp_manager)) -> MCPServerDetail:
    try:
        return manager.detail(server_name)
    except MCPIntegrationError as exc:
        _raise_mcp_error(exc)


@router.post("/servers/{server_name}/connect", response_model=MCPActionResponse)
async def connect_server(server_name: str, manager: MCPClientManager = Depends(get_mcp_manager)) -> MCPActionResponse:
    try:
        return MCPActionResponse(server=await manager.connect(server_name))
    except MCPIntegrationError as exc:
        _raise_mcp_error(exc)


@router.post("/servers/{server_name}/disconnect", response_model=MCPActionResponse)
async def disconnect_server(server_name: str, manager: MCPClientManager = Depends(get_mcp_manager)) -> MCPActionResponse:
    try:
        return MCPActionResponse(server=await manager.disconnect(server_name))
    except MCPIntegrationError as exc:
        _raise_mcp_error(exc)


@router.post("/servers/{server_name}/refresh-tools", response_model=MCPActionResponse)
async def refresh_server_tools(server_name: str, manager: MCPClientManager = Depends(get_mcp_manager)) -> MCPActionResponse:
    try:
        return MCPActionResponse(server=await manager.refresh_tools(server_name))
    except MCPIntegrationError as exc:
        _raise_mcp_error(exc)


@router.get("/tools", response_model=MCPToolListResponse)
def list_mcp_tools(
    server_name: str | None = None,
    connected: bool | None = None,
    allowed: bool | None = None,
    manager: MCPClientManager = Depends(get_mcp_manager),
) -> MCPToolListResponse:
    return MCPToolListResponse(
        items=manager.list_tools(server_name=server_name, connected=connected, allowed=allowed)
    )


def _raise_mcp_error(exc: MCPIntegrationError) -> None:
    if exc.code == "mcp_server_not_configured":
        status = 404
    elif exc.code in {"mcp_server_disabled", "mcp_server_not_connected"}:
        status = 409
    elif exc.code in {"mcp_connection_failed", "mcp_handshake_failed", "mcp_timeout"}:
        status = 503
    else:
        status = 422
    raise HTTPException(status_code=status, detail={"error_code": exc.code, "message": str(exc)}) from exc
