from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["admin", "member"]
AuthType = Literal["none", "bearer", "header"]


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    role: Role
    is_active: bool
    created_at: datetime | None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class UserCreate(BaseModel):
    email: str
    password: str = Field(repr=False)
    role: Role = "member"


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, repr=False)


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class APIKeyOut(BaseModel):
    id: int
    key_id: str
    name: str
    user_id: int
    user_email: str | None = None
    created_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class APIKeyIssued(APIKeyOut):
    # Shown exactly once, at creation. Only a hash is stored, so it cannot be recovered.
    key: str


class ToolOut(BaseModel):
    name: str
    description: str | None


class ServerToolOut(ToolOut):
    namespaced_name: str


class ServerOut(BaseModel):
    id: int
    name: str
    slug: str
    endpoint_url: str
    description: str | None
    is_enabled: bool
    auth_type: AuthType
    auth_header_name: str | None
    # Whether a credential is stored. The credential itself is never returned.
    has_credential: bool
    status: Literal["connected", "unreachable", "disabled"]
    status_detail: str | None
    checked_at: datetime | None
    tools: list[ServerToolOut]


class ServerCreate(BaseModel):
    name: str
    slug: str
    endpoint_url: str
    description: str | None = None
    is_enabled: bool = True
    auth_type: AuthType = "none"
    auth_header_name: str | None = None
    credential: str | None = Field(default=None, repr=False)


class ServerUpdate(BaseModel):
    """Omitted fields are unchanged. Send ``credential: null`` to clear the credential."""

    name: str | None = None
    endpoint_url: str | None = None
    description: str | None = None
    is_enabled: bool | None = None
    auth_type: AuthType | None = None
    auth_header_name: str | None = None
    credential: str | None = Field(default=None, repr=False)


class GatewayToolOut(BaseModel):
    name: str
    description: str | None
    input_schema: dict[str, Any]


class AuditEventOut(BaseModel):
    id: int
    occurred_at: datetime | None
    actor_user_id: int | None
    actor_email: str | None
    channel: str
    server: str | None
    tool: str | None
    operation: str
    duration_ms: int
    outcome: Literal["success", "failure"]
    error_category: str | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    total: int
    limit: int
    offset: int
