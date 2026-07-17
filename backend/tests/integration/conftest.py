"""Fixtures for tests that require a real PostgreSQL with pgvector.

Unit tests stub the database because they are about HTTP behaviour. These
tests are about the database itself, so a stub would prove nothing: the whole
point is to catch what a mock cannot -- a missing extension, a migration that
does not apply, a DSN the async driver rejects.

The suite skips rather than fails when no database is reachable, so ``pytest``
stays runnable on a laptop with nothing running. CI runs Postgres as a service
and therefore never skips.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from devpilot.core.config import Settings
from devpilot.db.session import Database


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    """Return settings pointed at the real test database via the environment."""
    return Settings(environment="ci")  # type: ignore[call-arg]


@pytest.fixture
async def database(integration_settings: Settings) -> AsyncIterator[Database]:
    """Yield a Database bound to live Postgres, skipping if none is reachable."""
    db = Database(integration_settings)
    try:
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        await db.dispose()
        pytest.skip(f"no database reachable at DEVPILOT_DATABASE_URL: {exc}")
    try:
        yield db
    finally:
        await db.dispose()
