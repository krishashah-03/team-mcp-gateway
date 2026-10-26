from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import User


class UserRepository:
    """Persistence operations for users; writes are flushed but not committed."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_users(self) -> list[User]:
        return list(self._session.scalars(select(User).order_by(User.id)).all())

    def get_by_id(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self._session.scalar(select(User).where(User.email == email))

    def count(self) -> int:
        return self._session.scalar(select(func.count()).select_from(User)) or 0

    def count_active_admins(self) -> int:
        statement = (
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True))
        )
        return self._session.scalar(statement) or 0

    def create(self, *, email: str, password_hash: str, role: str) -> User:
        user = User(email=email, password_hash=password_hash, role=role)
        self._session.add(user)
        self._session.flush()
        return user

    def update(
        self,
        user: User,
        *,
        role: str | None = None,
        is_active: bool | None = None,
        password_hash: str | None = None,
    ) -> User:
        if role is not None:
            user.role = role
        if is_active is not None:
            user.is_active = is_active
        if password_hash is not None:
            user.password_hash = password_hash
        self._session.flush()
        return user
