from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import AdminUser
from app.db.session import session_scope
from app.repositories.tool_permission_repository import ToolPermissionRepository
from app.repositories.user_repository import UserRepository
from app.services.tool_permission_service import (
    PermissionNotFoundError,
    PermissionValidationError,
    Rule,
    ToolPermissionService,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/permissions", tags=["Tool permissions"])


class RuleCreate(BaseModel):
    """Exactly one of ``role`` or ``user_id``. Omit ``tool_name`` to cover the whole server."""

    role: Literal["admin", "member"] | None = None
    user_id: int | None = Field(default=None, ge=1)
    server_slug: str
    tool_name: str | None = None
    effect: Literal["allow", "deny"]


class RuleOut(BaseModel):
    id: int
    role: str | None
    user_id: int | None
    server_slug: str
    tool_name: str | None
    effect: str
    created_at: datetime | None


def _out(rule: Rule) -> RuleOut:
    return RuleOut(**rule.__dict__)


@router.get("", response_model=list[RuleOut])
def list_rules(_: AdminUser) -> list[RuleOut]:
    with session_scope() as session:
        return [_out(r) for r in ToolPermissionService(ToolPermissionRepository(session)).list_rules()]


@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def set_rule(body: RuleCreate, _: AdminUser) -> RuleOut:
    """Create a rule, or change the effect of an identical existing one."""
    try:
        with session_scope() as session:
            if body.user_id is not None and UserService(UserRepository(session)).find_user(body.user_id) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
            rule = ToolPermissionService(ToolPermissionRepository(session)).set_rule(
                role=body.role,
                user_id=body.user_id,
                server_slug=body.server_slug,
                tool_name=body.tool_name,
                effect=body.effect,
            )
    except PermissionValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return _out(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, _: AdminUser) -> None:
    try:
        with session_scope() as session:
            ToolPermissionService(ToolPermissionRepository(session)).delete_rule(rule_id)
    except PermissionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
