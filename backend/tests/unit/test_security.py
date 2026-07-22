"""Unit tests for password hashing and token handling.

No database. These pin the security primitives: that hashing round-trips, that
hashes are never plaintext, that the byte ceiling holds, and that token
decoding rejects tampering, expiry, and algorithm substitution.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from devpilot.core.config import Settings
from devpilot.core.security import (
    PasswordTooLongError,
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "ci",
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/devpilot_test",
        "jwt_secret": "unit-test-secret-value-at-least-32-characters",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestPasswordHashing:
    def test_hash_is_not_the_plaintext(self) -> None:
        hashed = hash_password("correct horse battery staple")
        assert hashed != "correct horse battery staple"
        assert hashed.startswith("$argon2id$")

    def test_verify_accepts_the_right_password(self) -> None:
        hashed = hash_password("a sufficiently long passphrase")
        assert verify_password("a sufficiently long passphrase", hashed) is True

    def test_verify_rejects_the_wrong_password(self) -> None:
        hashed = hash_password("a sufficiently long passphrase")
        assert verify_password("not the passphrase at all", hashed) is False

    def test_same_password_hashes_differently_each_time(self) -> None:
        # Distinct salts, so identical passwords must not produce identical hashes.
        assert hash_password("repeated passphrase x") != hash_password("repeated passphrase x")

    def test_verify_returns_false_on_a_malformed_hash(self) -> None:
        assert verify_password("anything", "not-a-valid-argon2-hash") is False

    def test_hash_rejects_an_absurdly_long_password(self) -> None:
        with pytest.raises(PasswordTooLongError):
            hash_password("x" * 2000)

    def test_unicode_passphrase_round_trips(self) -> None:
        secret = "정확한 말 배터리 기본 🔐 correct"
        assert verify_password(secret, hash_password(secret)) is True


class TestAccessTokens:
    def test_round_trips_the_subject(self) -> None:
        settings = _settings()
        user_id = uuid.uuid4()
        token = create_access_token(user_id, settings)
        assert decode_access_token(token, settings) == user_id

    def test_carries_sub_iat_and_exp(self) -> None:
        settings = _settings()
        token = create_access_token(uuid.uuid4(), settings)
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        assert set(claims) >= {"sub", "iat", "exp"}

    def test_rejects_a_token_signed_with_another_secret(self) -> None:
        good = _settings()
        forged = create_access_token(
            uuid.uuid4(), _settings(jwt_secret="a-different-secret-32-characters-long!!")
        )
        with pytest.raises(TokenError):
            decode_access_token(forged, good)

    def test_rejects_an_expired_token(self) -> None:
        settings = _settings()
        expired = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "iat": datetime.now(UTC) - timedelta(hours=2),
                "exp": datetime.now(UTC) - timedelta(hours=1),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(TokenError):
            decode_access_token(expired, settings)

    def test_rejects_the_none_algorithm(self) -> None:
        # The classic alg-substitution attack: a token that asks to be verified
        # with no signature. Decoding pins HS256, so this must be refused.
        settings = _settings()
        unsigned = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(TokenError):
            decode_access_token(unsigned, settings)

    def test_rejects_a_token_missing_required_claims(self) -> None:
        settings = _settings()
        no_exp = jwt.encode({"sub": str(uuid.uuid4())}, settings.jwt_secret, algorithm="HS256")
        with pytest.raises(TokenError):
            decode_access_token(no_exp, settings)

    def test_rejects_a_non_uuid_subject(self) -> None:
        settings = _settings()
        bad_sub = jwt.encode(
            {
                "sub": "not-a-uuid",
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(TokenError):
            decode_access_token(bad_sub, settings)
