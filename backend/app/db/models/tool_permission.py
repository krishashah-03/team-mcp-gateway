from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ToolPermission(Base):
    """One allow/deny rule for a role or a single user.

    ``tool_name`` empty means the rule covers every tool of the server.
    """

    __tablename__ = "tool_permissions"
    __table_args__ = (
        CheckConstraint("effect IN ('allow', 'deny')", name="ck_tool_permissions_effect"),
        CheckConstraint("role IS NULL OR role IN ('admin', 'member')", name="ck_tool_permissions_role"),
        CheckConstraint(
            "(role IS NULL) <> (user_id IS NULL)", name="ck_tool_permissions_one_subject"
        ),
        UniqueConstraint(
            "role",
            "user_id",
            "server_slug",
            "tool_name",
            name="uq_tool_permissions_rule",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    server_slug: Mapped[str] = mapped_column(String(40), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    effect: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
