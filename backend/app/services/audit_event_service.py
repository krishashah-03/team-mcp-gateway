import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from app.db.models import GatewayAuditEvent
from app.repositories.audit_event_repository import AuditEventRepository


SAFE_ERROR_CATEGORIES = frozenset(
    {
        "authentication",
        "authorization",
        "validation",
        "not_found",
        "rate_limit",
        "timeout",
        "upstream",
        "internal",
    }
)
_OPERATION_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")
AuditOutcome = Literal["success", "failure"]


AUDIT_CHANNELS = frozenset({"mcp", "chat", "dashboard"})
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class AuditEventInput:
    operation: str
    duration_ms: int
    outcome: AuditOutcome
    actor_user_id: int | None = None
    actor_email: str | None = None
    upstream_server_id: int | None = None
    upstream_server_slug: str | None = None
    tool_name: str | None = None
    channel: str = "mcp"
    error_category: str | None = None
    status_code: int | None = None


@dataclass(frozen=True)
class AuditEventRecord:
    id: int
    actor_user_id: int | None
    actor_email: str | None
    upstream_server_id: int | None
    upstream_server_slug: str | None
    tool_name: str | None
    channel: str
    operation: str
    occurred_at: datetime | None
    duration_ms: int
    outcome: AuditOutcome
    error_category: str | None
    status_code: int | None


class AuditEventService:
    """Validates and records safe event metadata, never request bodies or secrets."""

    def __init__(self, repository: AuditEventRepository) -> None:
        self._repository = repository

    def record_event(self, event: AuditEventInput) -> AuditEventRecord:
        if not isinstance(event.operation, str) or not _OPERATION_PATTERN.fullmatch(
            event.operation
        ):
            raise ValueError("Operation must be a safe identifier of at most 100 characters")
        if (
            isinstance(event.duration_ms, bool)
            or not isinstance(event.duration_ms, int)
            or event.duration_ms < 0
        ):
            raise ValueError("Duration must be a nonnegative integer number of milliseconds")
        if event.outcome not in ("success", "failure"):
            raise ValueError("Outcome must be success or failure")
        if event.error_category is not None and (
            not isinstance(event.error_category, str)
            or event.error_category not in SAFE_ERROR_CATEGORIES
        ):
            raise ValueError("Error category is not in the safe category list")
        if event.outcome == "success" and event.error_category is not None:
            raise ValueError("Successful events cannot include an error category")
        if event.status_code is not None and (
            isinstance(event.status_code, bool)
            or not isinstance(event.status_code, int)
            or not 100 <= event.status_code <= 599
        ):
            raise ValueError("Status code must be between 100 and 599")
        if event.actor_user_id is not None and (
            isinstance(event.actor_user_id, bool)
            or not isinstance(event.actor_user_id, int)
            or event.actor_user_id <= 0
        ):
            raise ValueError("Actor user ID must be positive")
        if event.upstream_server_id is not None and (
            isinstance(event.upstream_server_id, bool)
            or not isinstance(event.upstream_server_id, int)
            or event.upstream_server_id <= 0
        ):
            raise ValueError("Upstream server ID must be positive")

        if event.channel not in AUDIT_CHANNELS:
            raise ValueError("Channel must be mcp, chat or dashboard")

        stored = self._repository.create(
            actor_user_id=event.actor_user_id,
            actor_email=self._clean(event.actor_email, 320),
            upstream_server_id=event.upstream_server_id,
            upstream_server_slug=self._clean(event.upstream_server_slug, 100),
            # Tool names come from untrusted upstreams or clients: strip control
            # characters and bound the length before they reach the log.
            tool_name=self._clean(event.tool_name, 200),
            channel=event.channel,
            operation=event.operation,
            duration_ms=event.duration_ms,
            outcome=event.outcome,
            error_category=event.error_category,
            status_code=event.status_code,
        )
        return self._to_record(stored)

    def search(
        self,
        *,
        actor_user_id: int | None = None,
        server_slug: str | None = None,
        outcome: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEventRecord], int]:
        events, total = self._repository.search(
            actor_user_id=actor_user_id,
            server_slug=server_slug,
            outcome=outcome,
            limit=limit,
            offset=offset,
        )
        return [self._to_record(event) for event in events], total

    @staticmethod
    def _clean(value: str | None, max_length: int) -> str | None:
        if value is None:
            return None
        return _CONTROL_CHARS.sub("", value)[:max_length]

    @staticmethod
    def _to_record(event: GatewayAuditEvent) -> AuditEventRecord:
        return AuditEventRecord(
            id=event.id,
            actor_user_id=event.actor_user_id,
            actor_email=event.actor_email,
            upstream_server_id=event.upstream_server_id,
            upstream_server_slug=event.upstream_server_slug,
            tool_name=event.tool_name,
            channel=event.channel,
            operation=event.operation,
            occurred_at=event.occurred_at,
            duration_ms=event.duration_ms,
            outcome=cast(AuditOutcome, event.outcome),
            error_category=event.error_category,
            status_code=event.status_code,
        )
