"""Dependency injection wiring for the API layer.

Every dependency the routes need is resolved here and nowhere else. Routes
declare what they need as parameters; they never reach into ``app.state``
or construct collaborators themselves. This is the seam that lets tests
substitute fakes with ``app.dependency_overrides``.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.core.config import Settings
from devpilot.core.errors import AuthenticationError
from devpilot.core.security import TokenError, decode_access_token
from devpilot.db.session import Database
from devpilot.models.user import User
from devpilot.providers.embeddings.base import EmbeddingProvider
from devpilot.providers.embeddings.registry import build_embedding_provider
from devpilot.repositories.embedding_repo import EmbeddingRepository
from devpilot.repositories.event_repo import EventRepository
from devpilot.repositories.job_repo import JobRepository
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.repositories.user_repo import UserRepository
from devpilot.services.auth_service import AuthService
from devpilot.services.repository_service import RepositoryService
from devpilot.services.search_service import SearchService


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


def get_user_repository(session: SessionDep) -> UserRepository:
    """Return a user repository bound to the request-scoped session.

    Args:
        session: The request-scoped async session.

    Returns:
        A ``UserRepository`` for this request.

    """
    return UserRepository(session)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]


def get_auth_service(users: UserRepositoryDep, settings: SettingsDep) -> AuthService:
    """Return an auth service wired to the repository and settings.

    Args:
        users: The request-scoped user repository.
        settings: The application settings.

    Returns:
        An ``AuthService`` for this request.

    """
    return AuthService(users, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]

# auto_error=False so a missing Authorization header raises our own
# AuthenticationError with the canonical error envelope, rather than FastAPI's
# default 403 with a differently shaped body. Every auth failure -- absent,
# malformed, expired -- then returns an identical 401.
_bearer_scheme = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


async def get_current_user(
    credentials: BearerCredentials,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> User:
    """Resolve the authenticated user from a bearer token.

    Returns 401 for every failure mode -- no header, wrong scheme, bad
    signature, expired token, or a subject that no longer maps to an active
    user -- with one indistinguishable error, so nothing about why the token
    was rejected leaks to the caller.

    Args:
        credentials: Parsed ``Authorization: Bearer`` credentials, or None.
        auth_service: The auth service, used to load the subject.
        settings: Supplies the verification secret and permitted algorithm.

    Returns:
        The authenticated, active ``User``.

    Raises:
        AuthenticationError: For any missing, malformed, expired, or unresolved
            token. Maps to 401 via the installed handler.

    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Not authenticated.")
    try:
        subject = decode_access_token(credentials.credentials, settings)
    except TokenError as exc:
        raise AuthenticationError("Not authenticated.") from exc
    # InvalidCredentialsError (an AuthenticationError) propagates unchanged if
    # the subject no longer resolves to an active user.
    return await auth_service.get_user(subject)


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_repository_service(session: SessionDep, settings: SettingsDep) -> RepositoryService:
    """Return a repository service wired to its repositories and settings.

    Args:
        session: The request-scoped async session.
        settings: The application settings.

    Returns:
        A ``RepositoryService`` for this request.

    """
    return RepositoryService(
        RepositoryRepository(session),
        JobRepository(session),
        EventRepository(session),
        settings,
    )


RepositoryServiceDep = Annotated[RepositoryService, Depends(get_repository_service)]


# The embedding provider is cached per process, keyed by provider name and
# model, so the real model is loaded at most once in the API process (its first
# search) rather than rebuilt on every request. The fake provider is cheap, but
# caching it too keeps one code path. The cache is keyed, not global, so a test
# that switches providers gets the right one.
_embedding_provider_cache: dict[tuple[str, str], EmbeddingProvider] = {}


def get_embedding_provider(settings: SettingsDep) -> EmbeddingProvider:
    """Return the process-cached embedding provider for the current settings.

    Args:
        settings: The application settings, naming the provider and model.

    Returns:
        A shared ``EmbeddingProvider``. The real provider still loads its model
        lazily on first use, so acquiring this dependency never forces a load.

    """
    key = (settings.embedding_provider, settings.embedding_model)
    provider = _embedding_provider_cache.get(key)
    if provider is None:
        provider = build_embedding_provider(settings)
        _embedding_provider_cache[key] = provider
    return provider


EmbeddingProviderDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]


def get_search_service(
    session: SessionDep, embedder: EmbeddingProviderDep, settings: SettingsDep
) -> SearchService:
    """Return a search service wired to its repositories, provider, and settings.

    Args:
        session: The request-scoped async session.
        embedder: The process-cached embedding provider.
        settings: The application settings.

    Returns:
        A ``SearchService`` for this request.

    """
    return SearchService(
        RepositoryRepository(session),
        EmbeddingRepository(session),
        embedder,
        settings,
    )


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
