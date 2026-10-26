from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GatewayAuditEvent(Base):
    __tablename__ = "gateway_audit_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success', 'failure')",
            name="ck_gateway_audit_events_outcome",
        ),
        CheckConstraint(
            "channel IN ('mcp', 'chat', 'dashboard')",
            name="ck_gateway_audit_events_channel",
        ),
        CheckConstraint(
            "duration_ms >= 0", name="ck_gateway_audit_events_duration_nonnegative"
        ),
        CheckConstraint(
            "status_code IS NULL OR (status_code >= 100 AND status_code <= 599)",
            name="ck_gateway_audit_events_status_code",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN "
            "('authentication', 'authorization', 'validation', 'not_found', "
            "'rate_limit', 'timeout', 'upstream', 'internal')",
            name="ck_gateway_audit_events_error_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Copied at write time so the log stays readable after a user or server is removed.
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    upstream_server_slug: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    channel: Mapped[str] = mapped_column(
        String(16), nullable=False, default="mcp", server_default="mcp"
    )
    upstream_server_id: Mapped[int | None] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="SET NULL"), nullable=True
    )
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
