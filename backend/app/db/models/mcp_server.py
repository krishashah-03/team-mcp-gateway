from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MCPServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        CheckConstraint(
            "auth_type IN ('none', 'bearer', 'header')", name="ck_mcp_servers_auth_type"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # How the gateway authenticates to this upstream. The credential is Fernet-encrypted
    # and is never returned by any API response.
    auth_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none", server_default="none"
    )
    auth_header_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    credential_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
