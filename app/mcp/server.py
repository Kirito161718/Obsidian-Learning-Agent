from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from app.db.database import SessionLocal
from app.core.config import BASE_DIR, get_settings
from app.tools.executor import ToolExecutionContext
from app.tools.internal.tools import (
    EmptyInput,
    GoalListInput,
    GoalListOutput,
    ObsidianSearchInput,
    ObsidianSearchOutput,
    PreferenceOutput,
    RetrieveKnowledgeInput,
    RetrieveKnowledgeOutput,
    SessionMessagesInput,
    SessionMessagesOutput,
    StatusListInput,
    StatusListOutput,
    get_session_messages,
    get_user_preferences,
    list_learning_goals,
    list_learning_status,
    retrieve_knowledge,
    search_obsidian_notes,
)

logging.basicConfig(stream=sys.stderr, level=logging.ERROR, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
server = Server("obsidian-learning-agent-read-only", version="1.0.0")

_TOOLS: dict[str, tuple[str, type, type, Any]] = {
    "list_learning_goals": (
        "列出手动维护的学习目标。",
        GoalListInput,
        GoalListOutput,
        list_learning_goals,
    ),
    "list_learning_status": (
        "列出学习状态及确定性重要指数。",
        StatusListInput,
        StatusListOutput,
        list_learning_status,
    ),
    "search_obsidian_notes": (
        "按标题、Vault 相对路径前缀或标签搜索 Obsidian 笔记元数据。",
        ObsidianSearchInput,
        ObsidianSearchOutput,
        search_obsidian_notes,
    ),
    "get_session_messages": (
        "读取指定学习 Session 的最近问答预览。",
        SessionMessagesInput,
        SessionMessagesOutput,
        get_session_messages,
    ),
    "get_user_preferences": (
        "读取当前用户手动偏好。",
        EmptyInput,
        PreferenceOutput,
        get_user_preferences,
    ),
    "retrieve_knowledge": (
        "复用现有 RAG Retrieve 检索知识，不生成答案或写入问答历史。",
        RetrieveKnowledgeInput,
        RetrieveKnowledgeOutput,
        retrieve_knowledge,
    ),
}


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=name,
            description=description,
            inputSchema=input_model.model_json_schema(),
            outputSchema=output_model.model_json_schema(),
        )
        for name, (description, input_model, output_model, _) in _TOOLS.items()
    ]


@server.call_tool(validate_input=True)
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any] | types.CallToolResult:
    definition = _TOOLS.get(name)
    if definition is None:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="mcp_tool_not_found")],
            isError=True,
        )
    _, input_model, output_model, callable_obj = definition
    try:
        payload = input_model.model_validate(arguments)
        # The project services are synchronous. Running them on the stdio
        # server task avoids a Windows/PyTorch worker-thread cold-start stall;
        # one stdio process serves one client session, so calls remain ordered.
        result = _invoke_existing(callable_obj, payload)
        return output_model.model_validate(result).model_dump(mode="json")
    except Exception as exc:
        logger.error("MCP tool failed: tool=%s error_type=%s", name, type(exc).__name__)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="mcp_server_error")],
            isError=True,
        )


def _invoke_existing(callable_obj, payload) -> dict[str, Any]:
    db = SessionLocal()
    try:
        result = callable_obj(payload, ToolExecutionContext(db=db))
        if not isinstance(result, dict):
            raise TypeError("Tool output must be an object")
        return result
    finally:
        db.close()


async def run_server() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
            raise_exceptions=False,
        )


def main() -> None:
    _preload_local_embedding()
    anyio.run(run_server)


def _preload_local_embedding() -> None:
    """Warm an existing local model before the Windows stdio event loop starts.

    This happens only after a user manually connects the MCP server. Missing or
    remote model references are not downloaded here and never block handshake.
    """
    settings = get_settings()
    if settings.app_env == "test":
        return
    configured = Path(settings.embedding_model_name)
    candidate = configured if configured.is_absolute() else BASE_DIR / configured
    if not candidate.exists():
        return
    try:
        from app.services.embedding_service import get_embedding_service

        get_embedding_service()
    except Exception as exc:
        logger.error("MCP local embedding warmup failed: error_type=%s", type(exc).__name__)


if __name__ == "__main__":
    main()
