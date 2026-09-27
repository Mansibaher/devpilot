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
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.api.deps import get_session
from devpilot.core.config import Settings
from devpilot.db.session import Database
from devpilot.main import create_app


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    """Return settings pointed at the real test database via the environment.

    The embedding provider is pinned to 'fake' so the whole integration suite
    is deterministic and never downloads a model or touches the network, even
    though it drives the real worker and search paths end to end.
    """
    return Settings(  # type: ignore[call-arg]
        environment="ci",
        jwt_secret="integration-secret-not-for-production-min-32c",
        jwt_access_ttl_minutes=15,
        embedding_provider="fake",
    )


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


@pytest.fixture
async def db_session(database: Database) -> AsyncIterator[AsyncSession]:
    """Yield a session whose writes are undone after the test.

    A single outer transaction is opened on one connection and never committed;
    the session joins it and runs inside a SAVEPOINT. Using a savepoint (rather
    than the outer transaction directly) means a statement that raises -- an
    IntegrityError from a duplicate-email test, say -- aborts only the
    savepoint, leaving the outer transaction healthy enough to roll back
    cleanly at teardown. This is the standard "join an external transaction"
    pattern from the SQLAlchemy docs.
    """
    async with database.engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def api_client(
    integration_settings: Settings, db_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """Yield an HTTP client whose app uses the rolled-back test session.

    The app's ``get_session`` dependency is overridden to hand every request
    the same transactional session, so writes made through the API are visible
    within the test and vanish when the transaction rolls back.
    """
    app = create_app(integration_settings)

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
