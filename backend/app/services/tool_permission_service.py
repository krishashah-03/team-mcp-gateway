import re
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Iterable

from app.db.models import ToolPermission
from app.repositories.tool_permission_repository import ToolPermissionRepository

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOOL = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
ROLES = frozenset({"admin", "member"})
EFFECTS = frozenset({"allow", "deny"})


class PermissionValidationError(ValueError):
    pass


class PermissionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class Rule:
    id: int
    role: str | None
    user_id: int | None
    server_slug: str
    tool_name: str | None
    effect: str
    created_at: datetime | None = None


def is_allowed(
    rules: Iterable[Rule], *, role: str, user_id: int, server_slug: str, tool_name: str
) -> bool:
    """Decide whether a user may use a tool.

    Applicable rules are those for the user's role or for the user personally, on the
    tool's server, covering either the whole server or this exact tool. The most
    specific rule wins: a user rule beats a role rule, and a tool rule beats a
    server-wide rule. At equal specificity deny beats allow. With no rule, allow.
    """
    best_score = -1
    deny_at_best = False
    for rule in rules:
        if rule.server_slug != server_slug:
            continue
        if rule.tool_name is not None and rule.tool_name != tool_name:
            continue
        if rule.user_id is not None:
            if rule.user_id != user_id:
                continue
        elif rule.role != role:
            continue

        score = (2 if rule.user_id is not None else 0) + (1 if rule.tool_name is not None else 0)
        if score > best_score:
            best_score, deny_at_best = score, rule.effect == "deny"
        elif score == best_score and rule.effect == "deny":
            deny_at_best = True
    return not deny_at_best


class ToolPermissionService:
    def __init__(self, repository: ToolPermissionRepository) -> None:
        self._repository = repository

    def list_rules(self) -> list[Rule]:
        return [self._to_rule(r) for r in self._repository.list_rules()]

    def set_rule(
        self,
        *,
        role: str | None,
        user_id: int | None,
        server_slug: str,
        tool_name: str | None,
        effect: str,
    ) -> Rule:
        """Create a rule, or change the effect of an identical existing one."""
        if (role is None) == (user_id is None):
            raise PermissionValidationError("Set exactly one of role or user_id")
        if role is not None and role not in ROLES:
            raise PermissionValidationError("Role must be admin or member")
        if effect not in EFFECTS:
            raise PermissionValidationError("Effect must be allow or deny")
        server_slug = server_slug.strip()
        if len(server_slug) > 40 or not _SLUG.fullmatch(server_slug):
            raise PermissionValidationError("server_slug is not a valid server slug")
        tool_name = tool_name.strip() if tool_name else None
        if tool_name is not None and not _TOOL.fullmatch(tool_name):
            raise PermissionValidationError("tool_name contains invalid characters")

        existing = self._repository.find_same(
            role=role, user_id=user_id, server_slug=server_slug, tool_name=tool_name
        )
        if existing is not None:
            return self._to_rule(self._repository.set_effect(existing, effect))
        return self._to_rule(
            self._repository.create(
                role=role, user_id=user_id, server_slug=server_slug, tool_name=tool_name, effect=effect
            )
        )

    def delete_rule(self, rule_id: int) -> None:
        rule = self._repository.get(rule_id)
        if rule is None:
            raise PermissionNotFoundError(f"Permission rule {rule_id} was not found")
        self._repository.remove(rule)

    @staticmethod
    def _to_rule(rule: ToolPermission) -> Rule:
        return Rule(
            id=rule.id,
            role=rule.role,
            user_id=rule.user_id,
            server_slug=rule.server_slug,
            tool_name=rule.tool_name,
            effect=rule.effect,
            created_at=rule.created_at,
        )
