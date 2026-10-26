import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_JWT_ALGORITHM = "HS256"
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    """Hash with scrypt (standard library); the parameters are stored in the hash."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored_hash.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_access_token(*, user_id: int, role: str) -> tuple[str, int]:
    """Return a signed session token and its lifetime in seconds."""
    lifetime = timedelta(minutes=settings.jwt_ttl_minutes)
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": str(user_id), "role": role, "iat": now, "exp": now + lifetime},
        settings.jwt_secret,
        algorithm=_JWT_ALGORITHM,
    )
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str) -> int | None:
    """Return the user id from a valid, unexpired token, else None.

    The role in the token is deliberately ignored: authorization always uses the
    current role stored in the database, so demotions take effect immediately.
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[_JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        return int(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


class CredentialCipher:
    """Encrypts upstream credentials at rest. Plaintext never leaves the server."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            raise ValueError(
                "Stored credential cannot be decrypted; was CREDENTIAL_ENCRYPTION_KEY changed?"
            ) from None


cipher = CredentialCipher(settings.credential_encryption_key)
