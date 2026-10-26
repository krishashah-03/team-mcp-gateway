import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.core.security import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.db.models import User
from app.repositories.user_repository import UserRepository


ROLES = frozenset({"admin", "member"})
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Verified against when the email is unknown, so login time does not reveal
# whether an account exists.
_DUMMY_HASH = hash_password("not-a-real-password")


class UserValidationError(ValueError):
    pass


class UserNotFoundError(LookupError):
    pass


class DuplicateUserError(ValueError):
    pass


@dataclass(frozen=True)
class UserRecord:
    id: int
    email: str
    role: str
    is_active: bool
    created_at: datetime | None


class UserService:
    def __init__(self, repository: UserRepository) -> None:
        self._repository = repository

    def list_users(self) -> list[UserRecord]:
        return [self._to_record(user) for user in self._repository.list_users()]

    def get_user(self, user_id: int) -> UserRecord:
        return self._to_record(self._require(user_id))

    def find_user(self, user_id: int) -> UserRecord | None:
        user = self._repository.get_by_id(user_id)
        return self._to_record(user) if user is not None else None

    def create_user(self, *, email: str, password: str, role: str) -> UserRecord:
        email = self._validate_email(email)
        self._validate_password(password)
        self._validate_role(role)
        if self._repository.get_by_email(email) is not None:
            raise DuplicateUserError(f"A user with email '{email}' already exists")
        try:
            user = self._repository.create(
                email=email, password_hash=hash_password(password), role=role
            )
        except IntegrityError:
            raise DuplicateUserError(f"A user with email '{email}' already exists") from None
        return self._to_record(user)

    def authenticate(self, email: str, password: str) -> UserRecord | None:
        user = self._repository.get_by_email(email.strip().lower())
        if user is None:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, user.password_hash) or not user.is_active:
            return None
        return self._to_record(user)

    def update_user(
        self,
        user_id: int,
        *,
        role: str | None = None,
        is_active: bool | None = None,
        password: str | None = None,
    ) -> UserRecord:
        user = self._require(user_id)
        if role is not None:
            self._validate_role(role)
        if password is not None:
            self._validate_password(password)

        loses_admin = (
            user.role == "admin"
            and user.is_active
            and (role == "member" or is_active is False)
        )
        if loses_admin and self._repository.count_active_admins() <= 1:
            raise UserValidationError("The last active admin cannot be demoted or disabled")

        updated = self._repository.update(
            user,
            role=role,
            is_active=is_active,
            password_hash=hash_password(password) if password is not None else None,
        )
        return self._to_record(updated)

    def ensure_bootstrap_admin(self, email: str, password: str) -> bool:
        """Create the first admin when no users exist. Returns True if created."""
        if self._repository.count() > 0:
            return False
        self.create_user(email=email, password=password, role="admin")
        return True

    def _require(self, user_id: int) -> User:
        user = self._repository.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"User {user_id} was not found")
        return user

    @staticmethod
    def _validate_email(email: str) -> str:
        normalized = email.strip().lower()
        if len(normalized) > 320 or not _EMAIL_PATTERN.fullmatch(normalized):
            raise UserValidationError("Email address is not valid")
        return normalized

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH or len(password) > 200:
            raise UserValidationError(
                f"Password must be {MIN_PASSWORD_LENGTH} to 200 characters"
            )

    @staticmethod
    def _validate_role(role: str) -> None:
        if role not in ROLES:
            raise UserValidationError("Role must be admin or member")

    @staticmethod
    def _to_record(user: User) -> UserRecord:
        return UserRecord(
            id=user.id,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
        )
