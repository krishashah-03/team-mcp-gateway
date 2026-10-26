from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import GatewayAuditEvent


class AuditEventRepository:
    """Stores structured audit events without committing the caller's session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        actor_user_id: int | None,
        actor_email: str | None,
        upstream_server_id: int | None,
        upstream_server_slug: str | None,
        tool_name: str | None,
        channel: str,
        operation: str,
        duration_ms: int,
        outcome: str,
        error_category: str | None,
        status_code: int | None,
    ) -> GatewayAuditEvent:
        event = GatewayAuditEvent(
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            upstream_server_id=upstream_server_id,
            upstream_server_slug=upstream_server_slug,
            tool_name=tool_name,
            channel=channel,
            operation=operation,
            duration_ms=duration_ms,
            outcome=outcome,
            error_category=error_category,
            status_code=status_code,
        )
        self._session.add(event)
        self._session.flush()
        return event

    def search(
        self,
        *,
        actor_user_id: int | None = None,
        server_slug: str | None = None,
        outcome: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[GatewayAuditEvent], int]:
        filters = []
        if actor_user_id is not None:
            filters.append(GatewayAuditEvent.actor_user_id == actor_user_id)
        if server_slug is not None:
            filters.append(GatewayAuditEvent.upstream_server_slug == server_slug)
        if outcome is not None:
            filters.append(GatewayAuditEvent.outcome == outcome)

        total = (
            self._session.scalar(
                select(func.count()).select_from(GatewayAuditEvent).where(*filters)
            )
            or 0
        )
        statement = (
            select(GatewayAuditEvent)
            .where(*filters)
            .order_by(GatewayAuditEvent.occurred_at.desc(), GatewayAuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(statement).all()), total
