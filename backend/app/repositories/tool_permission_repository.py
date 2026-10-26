from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ToolPermission


class ToolPermissionRepository:
    """Persistence for tool permission rules; writes are flushed, not committed."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_rules(self) -> list[ToolPermission]:
        return list(self._session.scalars(select(ToolPermission).order_by(ToolPermission.id)).all())

    def get(self, rule_id: int) -> ToolPermission | None:
        return self._session.get(ToolPermission, rule_id)

    def find_same(
        self, *, role: str | None, user_id: int | None, server_slug: str, tool_name: str | None
    ) -> ToolPermission | None:
        statement = select(ToolPermission).where(
            ToolPermission.role.is_(None) if role is None else ToolPermission.role == role,
            ToolPermission.user_id.is_(None) if user_id is None else ToolPermission.user_id == user_id,
            ToolPermission.server_slug == server_slug,
            ToolPermission.tool_name.is_(None) if tool_name is None else ToolPermission.tool_name == tool_name,
        )
        return self._session.scalar(statement)

    def create(
        self,
        *,
        role: str | None,
        user_id: int | None,
        server_slug: str,
        tool_name: str | None,
        effect: str,
    ) -> ToolPermission:
        rule = ToolPermission(
            role=role, user_id=user_id, server_slug=server_slug, tool_name=tool_name, effect=effect
        )
        self._session.add(rule)
        self._session.flush()
        return rule

    def set_effect(self, rule: ToolPermission, effect: str) -> ToolPermission:
        rule.effect = effect
        self._session.flush()
        return rule

    def remove(self, rule: ToolPermission) -> None:
        self._session.delete(rule)
        self._session.flush()
