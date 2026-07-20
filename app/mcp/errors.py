class MCPIntegrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


ERROR_CODES = {
    "mcp_server_not_configured",
    "mcp_server_disabled",
    "mcp_server_not_connected",
    "mcp_connection_failed",
    "mcp_handshake_failed",
    "mcp_tool_not_allowed",
    "mcp_tool_not_found",
    "mcp_schema_invalid",
    "mcp_input_invalid",
    "mcp_timeout",
    "mcp_protocol_error",
    "mcp_server_error",
    "mcp_output_too_large",
}
