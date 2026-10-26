"""The gateway's search-first MCP endpoint, served at /lite/mcp.

Same authentication, permissions, rate limit and audit log as /mcp, but only three
tools are advertised (search, describe, run) however many tools are connected.
"""

import json
from typing import Any

import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from app.core.config import Settings
from app.gateway.discovery import (
    DESCRIBE_TOOL,
    META_TOOLS,
    RUN_TOOL,
    SEARCH_TOOLS,
    rank_tools,
    summarize,
)
from app.gateway.mcp_app import ApiKeyAuthMiddleware, _principal
from app.gateway.service import GatewayService


def _json_result(payload: Any, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, indent=2))],
        is_error=is_error,
    )


def build_lite_app(gateway: GatewayService, settings: Settings) -> Starlette:
    async def on_list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        _principal(ctx)  # fail closed if the request somehow arrived unauthenticated
        return types.ListToolsResult(tools=META_TOOLS)

    async def on_call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        principal = _principal(ctx)
        arguments = params.arguments or {}

        if params.name == SEARCH_TOOLS:
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                return _json_result({"error": "query is required"}, is_error=True)
            limit = arguments.get("limit", 8)
            limit = limit if isinstance(limit, int) and not isinstance(limit, bool) else 8
            visible = await gateway.list_tools(principal)
            matches = rank_tools(visible, query, max(1, min(limit, 25)))
            return _json_result(
                {"matches": [summarize(t) for t in matches], "total_tools_available": len(visible)}
            )

        if params.name == DESCRIBE_TOOL:
            name = arguments.get("name")
            visible = await gateway.list_tools(principal)
            tool = next((t for t in visible if t.name == name), None)
            if tool is None:
                return _json_result({"error": f"No tool named '{name}'. Use search_tools."}, is_error=True)
            return _json_result(
                {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            )

        if params.name == RUN_TOOL:
            name = arguments.get("name")
            tool_arguments = arguments.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(tool_arguments, dict):
                return _json_result({"error": "name (string) and arguments (object) are required"}, is_error=True)
            # The full gateway path: permission check, timeout, upstream call, audit.
            return await gateway.call_tool(principal, name, tool_arguments)

        return _json_result({"error": f"Unknown tool '{params.name}'"}, is_error=True)

    server: Server[Any] = Server(
        "team-mcp-gateway-lite",
        version="0.1.0",
        instructions=(
            "Many tools are available behind this endpoint. Call search_tools with what you "
            "want to do, optionally describe_tool for the exact arguments, then run_tool."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
        ),
    )
    app.add_middleware(ApiKeyAuthMiddleware)
    return app
