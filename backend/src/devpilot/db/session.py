"""Database engine and session management.

The engine is owned by a ``Database`` object rather than a module-level global.
That matters for two reasons: the worker process constructs its own instance
with different pool sizing, and tests can build and dispose an engine per
fixture without mutating import-time state.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devpilot.core.config import Settings
from devpilot.core.logging import get_logger

logger = get_logger(__name__)


class Database:
    """Owns the async engine and session factory for one process.

    A single instance is created at application startup and disposed at
    shutdown. Holding it on ``app.state`` rather than in a module global keeps
    the object graph explicit and makes the API process, the worker process,
    and the test suite construct it on the same terms.
    """

    def __init__(self, settings: Settings) -> None:
        """Create the engine and session factory.

        Args:
            settings: Validated application settings supplying the DSN and
                pool configuration.

        """
        self._engine: AsyncEngine = create_async_engine(
            str(settings.database_url),
            echo=settings.debug,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_pre_ping=True,
        )
        self._settings = settings
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def settings(self) -> Settings:
        """Return the settings this database was built from."""
        return self._settings

    @property
    def engine(self) -> AsyncEngine:
        """Return the underlying async engine."""
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session, committing on success and rolling back on error.

        The session is always closed, returning its connection to the pool.
        Callers that stream a long response must exit this context *before*
        streaming, so a pooled connection is not pinned for the duration.

        Yields:
            An ``AsyncSession`` bound to this process's engine.

        """
        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def check(self) -> bool:
        """Report whether the database is reachable.

        Returns:
            True if a trivial query succeeds, False otherwise.

        """
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            logger.warning("database_healthcheck_failed", error=str(exc))
            return False
        return True

    async def dispose(self) -> None:
        """Close all pooled connections. Called at process shutdown."""
        await self._engine.dispose()
