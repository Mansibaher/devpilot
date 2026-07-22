"""Shared test fixtures.

M0 has no persistence, so these fixtures stub the ``Database`` collaborator
rather than standing up Postgres. Integration fixtures backed by a real
pgvector container arrive in M1, when there is a schema worth migrating.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from devpilot.api.deps import get_database
from devpilot.core.config import Settings
from devpilot.main import create_app


class StubDatabase:
    """A ``Database`` stand-in whose health check result is programmable."""

    def __init__(self, *, healthy: bool = True) -> None:
        """Initialise the stub.

        Args:
            healthy: The value ``check`` should return.
        """
        self.healthy = healthy
        self.dispose_calls = 0

    async def check(self) -> bool:
        """Return the configured health value."""
        return self.healthy

    async def dispose(self) -> None:
        """Record that disposal was requested."""
        self.dispose_calls += 1


@pytest.fixture
def settings() -> Settings:
    """Return settings suitable for tests, with no real database behind them."""
    return Settings(
        environment="ci",
        database_url="postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot_test",  # type: ignore[arg-type]
        jwt_secret="test-secret-not-used-in-production-min-32-chars",
        jwt_access_ttl_minutes=15,
    )


@pytest.fixture
def stub_database() -> StubDatabase:
    """Return a healthy stub database."""
    return StubDatabase(healthy=True)


@pytest.fixture
def app(settings: Settings, stub_database: StubDatabase) -> FastAPI:
    """Return an app whose database dependency resolves to the stub."""
    application = create_app(settings)
    application.dependency_overrides[get_database] = lambda: stub_database
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Yield an HTTP client bound to the app via ASGI, with no network involved."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
