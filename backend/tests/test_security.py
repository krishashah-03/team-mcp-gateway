from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.security import (
    CredentialCipher,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswords:
    def test_correct_password_verifies(self):
        assert verify_password("correct horse battery", hash_password("correct horse battery"))

    def test_wrong_password_fails(self):
        assert not verify_password("wrong", hash_password("correct horse battery"))

    def test_same_password_hashes_differently(self):
        assert hash_password("same-password-1") != hash_password("same-password-1")

    def test_hash_is_not_the_password(self):
        assert "correct horse" not in hash_password("correct horse battery")

    @pytest.mark.parametrize("garbage", ["", "plain", "scrypt$1$2", "bcrypt$a$b$c$d$e", "scrypt$x$y$z$!!$!!"])
    def test_malformed_hash_fails_closed(self, garbage):
        assert verify_password("anything", garbage) is False

    def test_unicode_password(self):
        assert verify_password("pässwörd-密码-1", hash_password("pässwörd-密码-1"))


class TestSessionTokens:
    def test_round_trip(self):
        token, ttl = create_access_token(user_id=7, role="admin")
        assert decode_access_token(token) == 7
        assert ttl == settings.jwt_ttl_minutes * 60

    def test_tampered_token_rejected(self):
        token, _ = create_access_token(user_id=7, role="member")
        assert decode_access_token(token[:-2] + "xx") is None

    def test_wrong_secret_rejected(self):
        forged = jwt.encode(
            {"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            "a-completely-different-secret-value-123456",
            algorithm="HS256",
        )
        assert decode_access_token(forged) is None

    def test_expired_token_rejected(self):
        expired = jwt.encode(
            {"sub": "1", "exp": datetime.now(timezone.utc) - timedelta(seconds=5)},
            settings.jwt_secret,
            algorithm="HS256",
        )
        assert decode_access_token(expired) is None

    def test_alg_none_rejected(self):
        unsigned = jwt.encode({"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "", algorithm="none")
        assert decode_access_token(unsigned) is None

    def test_missing_expiry_rejected(self):
        assert decode_access_token(jwt.encode({"sub": "1"}, settings.jwt_secret, algorithm="HS256")) is None

    def test_non_numeric_subject_rejected(self):
        token = jwt.encode(
            {"sub": "admin", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            settings.jwt_secret,
            algorithm="HS256",
        )
        assert decode_access_token(token) is None

    def test_garbage_rejected(self):
        assert decode_access_token("not.a.token") is None
        assert decode_access_token("") is None


class TestCredentialCipher:
    def test_round_trip(self):
        cipher = CredentialCipher(Fernet.generate_key().decode())
        assert cipher.decrypt(cipher.encrypt("super-secret")) == "super-secret"

    def test_ciphertext_hides_plaintext(self):
        cipher = CredentialCipher(Fernet.generate_key().decode())
        assert "super-secret" not in cipher.encrypt("super-secret")

    def test_wrong_key_fails_clearly(self):
        encrypted = CredentialCipher(Fernet.generate_key().decode()).encrypt("x")
        with pytest.raises(ValueError, match="cannot be decrypted"):
            CredentialCipher(Fernet.generate_key().decode()).decrypt(encrypted)
