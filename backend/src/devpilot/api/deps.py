"""Dependency injection wiring for the API layer.

Every dependency the routes need is resolved here and nowhere else. Routes
declare what they need as parameters; they never reach into ``app.state``
or construct collaborators themselves. This is the seam that lets tests
substitute fakes with ``app.dependency_overrides``.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.core.config import Settings
from devpilot.db.session import Database


def get_settings_from_state(request: Request) -> Settings:
    """Return the settings instance this application was constructed with.

    Deliberately *not* ``core.config.get_settings``: that returns a cached
    process-wide singleton built from the environment, which would silently
    ignore the settings passed to ``create_app`` and make the factory's
    argument a lie. Resolving from application state keeps one source of
    truth per app instance and lets tests configure an app directly.

    Args:
        request: The incoming request, used to reach application state.

    Returns:
        The ``Settings`` instance owned by the application.

    """
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    """Return the process-wide ``Database`` created during app startup.

    Args:
        request: The incoming request, used to reach application state.

    Returns:
        The ``Database`` instance owned by the application.

    """
    database: Database = request.app.state.database
    return database


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session.

    The session is committed on success and rolled back on failure by the
    ``Database.session`` context manager.

    Args:
        database: The application's database handle.

    Yields:
        An ``AsyncSession`` for the lifetime of the request.

    """
    async with database.session() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings_from_state)]
DatabaseDep = Annotated[Database, Depends(get_database)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
