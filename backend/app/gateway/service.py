"""Aggregates the tools of every enabled upstream behind one namespace.

Naming scheme: a tool called ``list_orders`` on the server with slug ``woocommerce`` is
exposed as ``woocommerce__list_orders``. Server slugs are lowercase letters, digits and
single hyphens, so they can never contain ``__``; splitting on the first ``__`` is
therefore unambiguous, and two servers can expose identically named tools without a
collision.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import mcp_types as types
from anyio import to_thread

from app.core.config import Settings
from app.core.metrics import metrics
from app.core.security import CredentialCipher
from app.db.session import session_scope
from app.gateway.upstream import (
    UpstreamError,
    call_upstream_tool,
    list_upstream_tools,
)
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.mcp_server_repository import MCPServerRepository
from app.services.audit_event_service import AuditEventInput, AuditEventService
from app.repositories.tool_permission_repository import ToolPermissionRepository
from app.services.mcp_server_service import (
    MCPServerRecord,
    MCPServerService,
    UpstreamTarget,
)
from app.services.tool_permission_service import Rule, ToolPermissionService, is_allowed

logger = logging.getLogger(__name__)

NAMESPACE_SEPARATOR = "__"
# Many MCP clients reject tool names longer than this.
MAX_TOOL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1500
# A failed probe is remembered briefly so a dead server is not hit on every request,
# yet recovers quickly once it is back.
_FAILURE_TTL_SECONDS = 5.0

CONNECTED = "connected"
UNREACHABLE = "unreachable"
DISABLED = "disabled"


@dataclass(frozen=True)
class Principal:
    """Who is making a call. ``channel`` records how they reached the gateway."""

    user_id: int
    email: str
    role: str
    channel: str = "mcp"


@dataclass
class _CacheEntry:
    version: str
    expires_at: float
    tools: list[types.Tool] = field(default_factory=list)
    error: str | None = None
    error_category: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ToolInfo:
    name: str
    namespaced_name: str
    description: str | None


@dataclass(frozen=True)
class ServerStatus:
    server: MCPServerRecord
    status: str
    tools: list[ToolInfo]
    error: str | None
    checked_at: datetime | None


def namespaced(slug: str, tool_name: str) -> str:
    return f"{slug}{NAMESPACE_SEPARATOR}{tool_name}"


def split_namespaced(name: str) -> tuple[str, str] | None:
    slug, separator, tool = name.partition(NAMESPACE_SEPARATOR)
    if not separator or not slug or not tool:
        return None
    return slug, tool


def _error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


class GatewayService:
    def __init__(self, settings: Settings, cipher: CredentialCipher) -> None:
        self._settings = settings
        self._cipher = cipher
        self._cache: dict[int, _CacheEntry] = {}
        self._checker: asyncio.Task[None] | None = None

    # ----- database access (sync, run in a worker thread) -----

    def _load_targets(self) -> list[UpstreamTarget]:
        with session_scope() as session:
            service = MCPServerService(MCPServerRepository(session), self._cipher)
            return service.list_enabled_targets()

    def _load_overview(self) -> tuple[list[MCPServerRecord], list[UpstreamTarget]]:
        with session_scope() as session:
            service = MCPServerService(MCPServerRepository(session), self._cipher)
            return service.list_servers(), service.list_enabled_targets()

    def _load_rules(self) -> list[Rule]:
        with session_scope() as session:
            return ToolPermissionService(ToolPermissionRepository(session)).list_rules()

    def _write_audit(self, event: AuditEventInput) -> None:
        with session_scope() as session:
            AuditEventService(AuditEventRepository(session)).record_event(event)

    async def _audit(
        self,
        principal: Principal,
        *,
        operation: str,
        started: float,
        error_category: str | None,
        tool_name: str | None = None,
        target: UpstreamTarget | None = None,
        slug: str | None = None,
        known_tool: bool = True,
    ) -> None:
        elapsed = max(0.0, time.monotonic() - started)
        server_label = (target.slug if target else slug) or "none"
        # Tool names from clients are unbounded, so any name that is not a tool the
        # upstream really exposes shares one label value.
        tool_label = (
            "unknown"
            if error_category == "not_found" or not known_tool
            else (tool_name or "none")
        )
        metrics.inc(
            "gateway_tool_calls_total",
            server=server_label,
            tool=tool_label,
            outcome="failure" if error_category else "success",
        )
        metrics.observe("gateway_tool_call_duration_seconds", elapsed, server=server_label)

        event = AuditEventInput(
            operation=operation,
            duration_ms=max(0, int(elapsed * 1000)),
            outcome="failure" if error_category else "success",
            actor_user_id=principal.user_id,
            actor_email=principal.email,
            upstream_server_id=target.id if target else None,
            upstream_server_slug=target.slug if target else slug,
            tool_name=tool_name,
            channel=principal.channel,
            error_category=error_category,
        )
        try:
            await to_thread.run_sync(self._write_audit, event)
        except Exception:  # noqa: BLE001 - a log failure must not turn a result into an error
            logger.exception("Could not write audit event for %s", operation)

    # ----- upstream probing -----

    async def _probe(self, target: UpstreamTarget, *, force: bool = False) -> _CacheEntry:
        entry = self._cache.get(target.id)
        if (
            not force
            and entry is not None
            and entry.version == target.version
            and entry.expires_at > time.monotonic()
        ):
            return entry

        try:
            tools = await list_upstream_tools(
                target, self._settings.upstream_list_timeout_seconds
            )
            fresh = _CacheEntry(
                version=target.version,
                expires_at=time.monotonic() + self._settings.tool_cache_ttl_seconds,
                tools=tools,
            )
        except UpstreamError as exc:
            fresh = _CacheEntry(
                version=target.version,
                expires_at=time.monotonic() + _FAILURE_TTL_SECONDS,
                error=exc.message,
                error_category=exc.category,
            )
        self._cache[target.id] = fresh
        metrics.set_gauge("gateway_upstream_up", 0.0 if fresh.error else 1.0, server=target.slug)
        return fresh

    async def _entry(self, target: UpstreamTarget, *, refresh: bool = False) -> _CacheEntry:
        """The latest known state of an upstream, probing only when it must.

        While the background checker runs, a stored result is used however old it is (the
        checker keeps it current), so a dead server never makes a request wait. Without
        the checker a result is reused only until it expires. A server with no result
        yet, or whose URL or credential changed, is probed now.
        """
        entry = self._cache.get(target.id)
        if entry is not None and entry.version == target.version and not refresh:
            if self._checker is not None or entry.expires_at > time.monotonic():
                return entry
        return await self._probe(target, force=True)

    async def _probe_all(
        self, targets: list[UpstreamTarget], *, refresh: set[int] | None = None
    ) -> list[_CacheEntry]:
        # _entry never raises, so one dead upstream cannot affect the others.
        refresh = refresh or set()
        return list(
            await asyncio.gather(*(self._entry(t, refresh=t.id in refresh) for t in targets))
        )

    # ----- background health checks -----

    def start_health_checks(self) -> None:
        interval = self._settings.health_check_interval_seconds
        if interval > 0 and self._checker is None:
            self._checker = asyncio.create_task(self._health_loop(interval))

    async def stop_health_checks(self) -> None:
        checker, self._checker = self._checker, None
        if checker is not None:
            checker.cancel()
            try:
                await checker
            except asyncio.CancelledError:
                pass

    async def _health_loop(self, interval: float) -> None:
        while True:
            try:
                targets = await to_thread.run_sync(self._load_targets)
                await asyncio.gather(*(self._probe(t, force=True) for t in targets))
                live = {t.id for t in targets}
                for server_id in [k for k in self._cache if k not in live]:
                    del self._cache[server_id]
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the checker must outlive any single failure
                logger.exception("Health check round failed")
            await asyncio.sleep(interval)

    @staticmethod
    def _expose(target: UpstreamTarget, tool: types.Tool) -> types.Tool | None:
        name = namespaced(target.slug, tool.name)
        if len(name) > MAX_TOOL_NAME_LENGTH:
            logger.warning("Skipping tool %r: gateway name exceeds %d chars", name, MAX_TOOL_NAME_LENGTH)
            return None
        # Descriptions are written by the upstream, which may be hostile; bound their
        # size and label the source so a model can see where the text came from.
        description = (tool.description or "")[:MAX_DESCRIPTION_LENGTH]
        return tool.model_copy(
            update={
                "name": name,
                "description": f"[{target.name}] {description}".strip(),
            }
        )

    # ----- public API used by the MCP endpoint, the chat agent and the dashboard -----

    async def list_tools(self, principal: Principal) -> list[types.Tool]:
        targets = await to_thread.run_sync(self._load_targets)
        rules = await to_thread.run_sync(self._load_rules)
        entries = await self._probe_all(targets)
        tools: list[types.Tool] = []
        for target, entry in zip(targets, entries, strict=True):
            for tool in entry.tools:
                # Tools the user may not call are hidden, not just rejected on use.
                if not is_allowed(
                    rules,
                    role=principal.role,
                    user_id=principal.user_id,
                    server_slug=target.slug,
                    tool_name=tool.name,
                ):
                    continue
                exposed = self._expose(target, tool)
                if exposed is not None:
                    tools.append(exposed)
        return tools

    async def call_tool(
        self, principal: Principal, name: str, arguments: dict[str, Any] | None
    ) -> types.CallToolResult:
        started = time.monotonic()
        parsed = split_namespaced(name)
        target: UpstreamTarget | None = None
        if parsed is not None:
            slug, tool_name = parsed
            # Re-read from the database on every call so disabling or removing a
            # server takes effect immediately, regardless of any cached tool list.
            targets = await to_thread.run_sync(self._load_targets)
            target = next((t for t in targets if t.slug == slug), None)

        if parsed is None or target is None:
            await self._audit(
                principal,
                operation="tool_call",
                started=started,
                error_category="not_found",
                tool_name=name,
                slug=parsed[0] if parsed else None,
            )
            return _error_result(f"Unknown tool '{name}'")

        # Checked on every call, independent of what the client was shown, so a hidden
        # tool cannot be invoked by guessing its name.
        rules = await to_thread.run_sync(self._load_rules)
        if not is_allowed(
            rules,
            role=principal.role,
            user_id=principal.user_id,
            server_slug=target.slug,
            tool_name=tool_name,
        ):
            await self._audit(
                principal,
                operation="tool_call",
                started=started,
                error_category="authorization",
                tool_name=tool_name,
                target=target,
            )
            return _error_result(f"You are not permitted to use '{name}'")

        # Only forward calls for tools the upstream really exposes. This stops a client
        # from spraying arbitrary tool names at a server (and into the metrics and audit
        # log); a tool added upstream moments ago is picked up by one forced re-probe.
        entry = await self._entry(target)
        if tool_name not in {t.name for t in entry.tools} and entry.error is None:
            entry = await self._entry(target, refresh=True)
        if entry.error is not None:
            await self._audit(
                principal,
                operation="tool_call",
                started=started,
                error_category=entry.error_category or "upstream",
                tool_name=tool_name,
                target=target,
                known_tool=False,
            )
            return _error_result(entry.error)
        if tool_name not in {t.name for t in entry.tools}:
            await self._audit(
                principal,
                operation="tool_call",
                started=started,
                error_category="not_found",
                tool_name=tool_name,
                target=target,
                known_tool=False,
            )
            return _error_result(f"Server '{target.slug}' has no tool '{tool_name}'")

        try:
            result = await call_upstream_tool(
                target, tool_name, arguments, self._settings.upstream_call_timeout_seconds
            )
        except UpstreamError as exc:
            # A failing call also makes the cached tool list suspect.
            self._cache.pop(target.id, None)
            await self._audit(
                principal,
                operation="tool_call",
                started=started,
                error_category=exc.category,
                tool_name=tool_name,
                target=target,
            )
            return _error_result(exc.message)

        await self._audit(
            principal,
            operation="tool_call",
            started=started,
            error_category="upstream" if result.is_error else None,
            tool_name=tool_name,
            target=target,
        )
        return result

    async def overview(self, *, refresh: set[int] | bool = False) -> list[ServerStatus]:
        """Every registered server with its status and tools, for the dashboard.

        Reads the latest known state, so it returns immediately even when a server is
        down. Pass ``refresh=True`` (or a set of server ids) to probe those servers now.
        """
        records, targets = await to_thread.run_sync(self._load_overview)
        by_id = {t.id: t for t in targets}
        wanted = set(by_id) if refresh is True else (refresh or set())
        entries = dict(
            zip(
                by_id.keys(),
                await self._probe_all(list(by_id.values()), refresh=wanted),
                strict=True,
            )
        )
        statuses: list[ServerStatus] = []
        for record in records:
            entry = entries.get(record.id)
            if not record.is_enabled or entry is None:
                statuses.append(ServerStatus(record, DISABLED, [], None, None))
                continue
            statuses.append(
                ServerStatus(
                    server=record,
                    status=UNREACHABLE if entry.error else CONNECTED,
                    tools=[
                        ToolInfo(
                            name=tool.name,
                            namespaced_name=namespaced(record.slug, tool.name),
                            description=(tool.description or "")[:MAX_DESCRIPTION_LENGTH],
                        )
                        for tool in entry.tools
                    ],
                    error=entry.error,
                    checked_at=entry.checked_at,
                )
            )
        return statuses

    def forget(self, server_id: int) -> None:
        self._cache.pop(server_id, None)
