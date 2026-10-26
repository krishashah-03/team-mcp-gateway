import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.db.models import GatewayAPIKey
from app.repositories.api_key_repository import APIKeyRepository


_KEY_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_KEY_PREFIX = "gwk"


@dataclass(frozen=True)
class APIKeyMetadata:
    id: int
    user_id: int
    key_id: str
    name: str
    created_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class IssuedAPIKey:
    """One-time issuance result; repr deliberately omits the plaintext key."""

    metadata: APIKeyMetadata
    plaintext_key: str = field(repr=False)


class APIKeyService:
    """Issues and validates gateway keys, separate from upstream store credentials."""

    def __init__(self, repository: APIKeyRepository) -> None:
        self._repository = repository

    def issue_key(
        self, *, user_id: int, name: str, expires_at: datetime | None = None
    ) -> IssuedAPIKey:
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 200:
            raise ValueError("API key name must be 1 to 200 characters")

        now = datetime.now(timezone.utc)
        if expires_at is not None:
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                raise ValueError("API key expiry must include a timezone")
            if expires_at <= now:
                raise ValueError("API key expiry must be in the future")

        key_id = secrets.token_hex(16)
        secret = secrets.token_urlsafe(32)
        plaintext_key = f"{_KEY_PREFIX}_{key_id}_{secret}"
        key_hash = self._hash_secret(secret)

        record = self._repository.create(
            user_id=user_id,
            key_id=key_id,
            key_hash=key_hash,
            name=normalized_name,
            expires_at=expires_at,
        )
        return IssuedAPIKey(
            metadata=self._to_metadata(record),
            plaintext_key=plaintext_key,
        )

    def validate_key(self, plaintext_key: str) -> APIKeyMetadata | None:
        parsed = self._parse_key(plaintext_key)
        if parsed is None:
            return None
        key_id, secret = parsed

        record = self._repository.get_by_key_id(key_id)
        if record is None:
            return None

        candidate_hash = self._hash_secret(secret)
        if not hmac.compare_digest(record.key_hash, candidate_hash):
            return None

        now = datetime.now(timezone.utc)
        if record.revoked_at is not None:
            return None
        if record.expires_at is not None and record.expires_at <= now:
            return None
        return self._to_metadata(record)

    def list_keys(self, *, user_id: int | None = None) -> list[APIKeyMetadata]:
        records = (
            self._repository.list_for_user(user_id)
            if user_id is not None
            else self._repository.list_all()
        )
        return [self._to_metadata(record) for record in records]

    def get_key(self, key_id: str) -> APIKeyMetadata | None:
        record = self._repository.get_by_key_id(key_id)
        return self._to_metadata(record) if record is not None else None

    def revoke_key(self, key_id: str) -> APIKeyMetadata | None:
        record = self._repository.get_by_key_id(key_id)
        if record is None:
            return None
        revoked_record = self._repository.revoke(record, datetime.now(timezone.utc))
        return self._to_metadata(revoked_record)

    @staticmethod
    def _parse_key(plaintext_key: str) -> tuple[str, str] | None:
        if not isinstance(plaintext_key, str):
            return None
        parts = plaintext_key.split("_", 2)
        if len(parts) != 3 or parts[0] != _KEY_PREFIX:
            return None
        key_id, secret = parts[1], parts[2]
        if not _KEY_ID_PATTERN.fullmatch(key_id) or not _SECRET_PATTERN.fullmatch(secret):
            return None
        return key_id, secret

    @staticmethod
    def _hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode("ascii")).hexdigest()

    @staticmethod
    def _to_metadata(record: GatewayAPIKey) -> APIKeyMetadata:
        return APIKeyMetadata(
            id=record.id,
            user_id=record.user_id,
            key_id=record.key_id,
            name=record.name,
            created_at=record.created_at,
            expires_at=record.expires_at,
            revoked_at=record.revoked_at,
        )
