"""The gateway's single MCP endpoint (Streamable HTTP, POST /mcp).

Clients authenticate with their personal gateway API key as a bearer token. The key
identifies a user; that user is recorded in the audit log for every call. The key is
never forwarded upstream: upstreams only ever see the credential an admin configured
for them.
"""

import logging
from typing import Any

import mcp_types as types
from anyio import to_thread
from mcp.server import Server, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.core.limiters import api_calls_by_user
from app.core.metrics import metrics
from app.db.session import session_scope
from app.gateway.service import GatewayService, Principal
from app.repositories.api_key_repository import APIKeyRepository
from app.repositories.user_repository import UserRepository
from app.services.api_key_service import APIKeyService
from app.services.user_service import UserService

logger = logging.getLogger(__name__)

_PRINCIPAL_SCOPE_KEY = "gateway_principal"


def authenticate_api_key(token: str) -> Principal | None:
    """Resolve a gateway API key to an active user, or None. Runs in a worker thread."""
    with session_scope() as session:
        metadata = APIKeyService(APIKeyRepository(session)).validate_key(token)
        if metadata is None:
            return None
        user = UserService(UserRepository(session)).find_user(metadata.user_id)
        if user is None or not user.is_active:
            return None
        return Principal(user_id=user.id, email=user.email, role=user.role, channel="mcp")


class ApiKeyAuthMiddleware:
    """Rejects any request without a valid, unrevoked key before it reaches MCP."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        header = Request(scope).headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        principal = None
        if scheme.lower() == "bearer" and token.strip():
            principal = await to_thread.run_sync(authenticate_api_key, token.strip())

        if principal is None:
            metrics.inc("gateway_auth_failures_total", kind="api_key")
            response = JSONResponse(
                {"error": "invalid_token", "error_description": "Missing, invalid or revoked API key"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
            await response(scope, receive, send)
            return

        allowed, retry_after = api_calls_by_user.hit(f"user:{principal.user_id}")
        if not allowed:
            metrics.inc("gateway_rate_limited_total", scope="mcp")
            response = JSONResponse(
                {"error": "rate_limited", "error_description": "Too many requests"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        scope[_PRINCIPAL_SCOPE_KEY] = principal
        await self._app(scope, receive, send)


def _principal(ctx: ServerRequestContext[Any]) -> Principal:
    request = ctx.request
    principal = getattr(request, "scope", {}).get(_PRINCIPAL_SCOPE_KEY) if request else None
    if not isinstance(principal, Principal):
        # The middleware guarantees a principal; reaching here means a wiring bug,
        # and failing closed is the only safe response.
        raise PermissionError("No authenticated principal for this request")
    return principal


def build_mcp_app(gateway: GatewayService, settings: Settings) -> Starlette:
    async def on_list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        tools = await gateway.list_tools(_principal(ctx))
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return await gateway.call_tool(_principal(ctx), params.name, params.arguments)

    server: Server[Any] = Server(
        "team-mcp-gateway",
        version="0.1.0",
        instructions=(
            "One endpoint for every MCP server your team connected. Tool names are "
            "prefixed with the server they belong to, for example woocommerce__list_orders."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        # Stateless: no per-client session is held in this process, so any replica
        # can serve any request.
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
        ),
    )
    app.add_middleware(ApiKeyAuthMiddleware)
    return app
