from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GatewayAPIKey


class APIKeyRepository:
    """Persistence operations for gateway API keys; writes are not committed."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_key_id(self, key_id: str) -> GatewayAPIKey | None:
        statement = select(GatewayAPIKey).where(GatewayAPIKey.key_id == key_id)
        return self._session.scalar(statement)

    def list_for_user(self, user_id: int) -> list[GatewayAPIKey]:
        statement = (
            select(GatewayAPIKey)
            .where(GatewayAPIKey.user_id == user_id)
            .order_by(GatewayAPIKey.id.desc())
        )
        return list(self._session.scalars(statement).all())

    def list_all(self) -> list[GatewayAPIKey]:
        statement = select(GatewayAPIKey).order_by(GatewayAPIKey.id.desc())
        return list(self._session.scalars(statement).all())

    def create(
        self,
        *,
        user_id: int,
        key_id: str,
        key_hash: str,
        name: str,
        expires_at: datetime | None,
    ) -> GatewayAPIKey:
        api_key = GatewayAPIKey(
            user_id=user_id,
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            expires_at=expires_at,
        )
        self._session.add(api_key)
        self._session.flush()
        return api_key

    def revoke(self, api_key: GatewayAPIKey, revoked_at: datetime) -> GatewayAPIKey:
        if api_key.revoked_at is None:
            api_key.revoked_at = revoked_at
            self._session.flush()
        return api_key
