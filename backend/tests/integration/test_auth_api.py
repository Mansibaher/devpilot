"""End-to-end tests for the authentication API against a real database.

These exercise the full stack -- route, service, repository, Postgres, argon2,
JWT -- because the properties under test (a unique constraint firing, a hash
never appearing in a response, an enumeration-resistant login) only exist when
those layers run together. Every test shares one transaction that is rolled
back at teardown, so they are isolated without recreating the schema.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_VALID_PASSWORD = "a perfectly reasonable passphrase"


async def _register(client: AsyncClient, email: str, password: str = _VALID_PASSWORD):
    return await client.post("/api/v1/auth/register", json={"email": email, "password": password})


async def _login(client: AsyncClient, email: str, password: str = _VALID_PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


class TestRegistration:
    async def test_successful_registration_returns_201_without_a_hash(
        self, api_client: AsyncClient
    ) -> None:
        response = await _register(api_client, "new.user@example.com")
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "new.user@example.com"
        assert body["is_active"] is True
        assert "id" in body and "created_at" in body
        # The response must never carry credential material.
        assert "password" not in body
        assert "password_hash" not in body

    async def test_duplicate_email_is_rejected_with_409(self, api_client: AsyncClient) -> None:
        await _register(api_client, "dupe@example.com")
        second = await _register(api_client, "dupe@example.com")
        assert second.status_code == 409

    async def test_duplicate_is_case_insensitive(self, api_client: AsyncClient) -> None:
        await _register(api_client, "Case@Example.com")
        collision = await _register(api_client, "case@example.com")
        assert collision.status_code == 409

    async def test_invalid_email_is_rejected_with_422(self, api_client: AsyncClient) -> None:
        response = await _register(api_client, "not-an-email")
        assert response.status_code == 422

    async def test_weak_password_is_rejected_with_422(self, api_client: AsyncClient) -> None:
        response = await _register(api_client, "shortpw@example.com", password="tooshort")
        assert response.status_code == 422

    async def test_password_is_stored_as_a_hash_never_plaintext(
        self, api_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _register(api_client, "stored@example.com")
        stored = (
            await db_session.execute(
                text("SELECT password_hash FROM users WHERE email = :e"),
                {"e": "stored@example.com"},
            )
        ).scalar_one()
        assert stored != _VALID_PASSWORD
        assert stored.startswith("$argon2id$")


class TestLogin:
    async def test_successful_login_returns_a_bearer_token(self, api_client: AsyncClient) -> None:
        await _register(api_client, "login@example.com")
        response = await _login(api_client, "login@example.com")
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert isinstance(body["access_token"], str) and body["access_token"]

    async def test_wrong_password_is_rejected_with_401(self, api_client: AsyncClient) -> None:
        await _register(api_client, "wrongpw@example.com")
        response = await _login(api_client, "wrongpw@example.com", password="the wrong passphrase")
        assert response.status_code == 401

    async def test_unknown_user_is_rejected_with_401(self, api_client: AsyncClient) -> None:
        response = await _login(api_client, "ghost@example.com")
        assert response.status_code == 401

    async def test_unknown_and_wrong_password_give_an_identical_response(
        self, api_client: AsyncClient
    ) -> None:
        # Enumeration resistance: the two failures must be indistinguishable.
        await _register(api_client, "real@example.com")
        wrong = await _login(api_client, "real@example.com", password="definitely not it here")
        unknown = await _login(api_client, "absent@example.com")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()


class TestCurrentUser:
    async def _token_for(self, client: AsyncClient, email: str) -> str:
        await _register(client, email)
        return (await _login(client, email)).json()["access_token"]

    async def test_me_returns_the_authenticated_user(self, api_client: AsyncClient) -> None:
        token = await self._token_for(api_client, "me@example.com")
        response = await api_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["email"] == "me@example.com"

    async def test_missing_token_is_rejected_with_401(self, api_client: AsyncClient) -> None:
        response = await api_client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_malformed_token_is_rejected_with_401(self, api_client: AsyncClient) -> None:
        response = await api_client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert response.status_code == 401

    async def test_expired_token_is_rejected_with_401(self, api_client: AsyncClient) -> None:
        # Mint a token that expired in the past, signed with the test secret.
        expired = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "iat": datetime.now(UTC) - timedelta(hours=2),
                "exp": datetime.now(UTC) - timedelta(hours=1),
            },
            "integration-secret-not-for-production-min-32c",
            algorithm="HS256",
        )
        response = await api_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
        )
        assert response.status_code == 401

    async def test_wrong_scheme_is_rejected_with_401(self, api_client: AsyncClient) -> None:
        token = await self._token_for(api_client, "scheme@example.com")
        response = await api_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Basic {token}"}
        )
        assert response.status_code == 401
