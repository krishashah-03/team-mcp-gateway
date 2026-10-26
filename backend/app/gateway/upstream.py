"""Talks to one upstream MCP server over Streamable HTTP.

Each operation opens a short-lived session. That keeps the gateway stateless (safe to
run several replicas) and means a dead upstream can never leave a half-open connection
behind that blocks other servers.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx2
import mcp_types as types
from anyio import to_thread
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.core.config import settings
from app.core.urlguard import UnsafeURLError, check_upstream_url
from app.services.mcp_server_service import UpstreamTarget

logger = logging.getLogger(__name__)

# Error categories shared with the audit log.
TIMEOUT = "timeout"
UPSTREAM = "upstream"
AUTHENTICATION = "authentication"


class UpstreamError(Exception):
    """A safe, human-readable failure of one upstream call."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


@asynccontextmanager
async def _session(target: UpstreamTarget, timeout: float) -> AsyncIterator[Client]:
    http = httpx2.AsyncClient(
        headers=target.headers,
        timeout=httpx2.Timeout(timeout),
        follow_redirects=False,
    )
    async with http:
        transport = streamable_http_client(target.url, http_client=http)
        async with Client(transport) as client:
            yield client


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for inner in exc.exceptions:
            leaves.extend(_leaf_exceptions(inner))
        return leaves
    return [exc]


def _classify(target: UpstreamTarget, exc: BaseException, timeout: float) -> UpstreamError:
    leaves = _leaf_exceptions(exc)
    if any(isinstance(leaf, TimeoutError) for leaf in leaves):
        return UpstreamError(
            TIMEOUT, f"Server '{target.slug}' did not respond within {timeout:g}s"
        )

    for leaf in leaves:
        status = getattr(getattr(leaf, "response", None), "status_code", None)
        if status in (401, 403):
            return UpstreamError(
                AUTHENTICATION,
                f"Server '{target.slug}' rejected the gateway's credential (HTTP {status})",
            )
        if isinstance(leaf, httpx2.ConnectError):
            return UpstreamError(UPSTREAM, f"Server '{target.slug}' is unreachable")

    # Exception text can echo upstream-controlled content, so only the type is reported.
    detail = type(leaves[0]).__name__
    return UpstreamError(UPSTREAM, f"Server '{target.slug}' failed ({detail})")


async def _enforce_network_policy(target: UpstreamTarget) -> None:
    """Re-check the destination before every connection, not only when it was saved."""
    try:
        await to_thread.run_sync(
            lambda: check_upstream_url(
                target.url,
                allow_private=settings.upstream_allow_private_networks,
                allowed_hosts=settings.upstream_host_allowlist,
            )
        )
    except UnsafeURLError:
        raise UpstreamError(
            UPSTREAM, f"Server '{target.slug}' is blocked by the gateway's network policy"
        ) from None


async def list_upstream_tools(target: UpstreamTarget, timeout: float) -> list[types.Tool]:
    await _enforce_network_policy(target)
    try:
        with anyio.fail_after(timeout):
            async with _session(target, timeout) as client:
                tools: list[types.Tool] = []
                cursor: str | None = None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    tools.extend(page.tools)
                    cursor = page.next_cursor
                    if not cursor or len(tools) > 1000:
                        return tools
    except Exception as exc:  # noqa: BLE001 - every failure becomes a safe UpstreamError
        error = _classify(target, exc, timeout)
        logger.warning("tools/list failed for %s: %s", target.slug, error.message)
        raise error from None


async def call_upstream_tool(
    target: UpstreamTarget,
    tool_name: str,
    arguments: dict[str, Any] | None,
    timeout: float,
) -> types.CallToolResult:
    await _enforce_network_policy(target)
    try:
        with anyio.fail_after(timeout):
            async with _session(target, timeout) as client:
                return await client.call_tool(tool_name, arguments or {})
    except Exception as exc:  # noqa: BLE001
        error = _classify(target, exc, timeout)
        logger.warning("tools/call failed for %s.%s: %s", target.slug, tool_name, error.message)
        raise error from None
