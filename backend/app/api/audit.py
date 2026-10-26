from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import AdminUser
from app.api.schemas import AuditEventOut, AuditPage
from app.db.session import session_scope
from app.repositories.audit_event_repository import AuditEventRepository
from app.services.audit_event_service import AuditEventService

router = APIRouter(tags=["Audit"])


@router.get("/audit", response_model=AuditPage)
def list_audit_events(
    _: AdminUser,
    user_id: int | None = None,
    server: Annotated[str | None, Query(description="Server slug")] = None,
    outcome: Literal["success", "failure"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    """Every tool call, newest first: who, which server and tool, when, how long, outcome."""
    with session_scope() as session:
        events, total = AuditEventService(AuditEventRepository(session)).search(
            actor_user_id=user_id,
            server_slug=server,
            outcome=outcome,
            limit=limit,
            offset=offset,
        )
    return AuditPage(
        items=[
            AuditEventOut(
                id=e.id,
                occurred_at=e.occurred_at,
                actor_user_id=e.actor_user_id,
                actor_email=e.actor_email,
                channel=e.channel,
                server=e.upstream_server_slug,
                tool=e.tool_name,
                operation=e.operation,
                duration_ms=e.duration_ms,
                outcome=e.outcome,
                error_category=e.error_category,
            )
            for e in events
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
