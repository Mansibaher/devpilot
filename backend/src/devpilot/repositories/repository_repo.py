"""Data access for repositories.

Every read is owner-scoped at the query level: the owner id is a required
argument, not an optional filter a caller might forget. A repository belonging
to another user is indistinguishable from one that does not exist, because the
query simply returns nothing -- which is what lets the service answer 404 rather
than 403 for cross-tenant access without any extra branching.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.models.repository import Repository


class RepositoryRepository:
    """CRUD and lookups for :class:`~devpilot.models.repository.Repository`."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The active async session.

        """
        self._session = session

    async def get_for_owner(
        self, repository_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Repository | None:
        """Return a repository only if it belongs to this owner.

        Args:
            repository_id: The repository id.
            owner_id: The requesting user's id.

        Returns:
            The repository, or ``None`` if it does not exist or is owned by
            someone else.

        """
        result = await self._session.execute(
            select(Repository).where(
                Repository.id == repository_id, Repository.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_slug(
        self, owner_id: uuid.UUID, provider: str, owner_name: str, repo_name: str
    ) -> Repository | None:
        """Return this owner's repository matching a provider/owner/name slug.

        Args:
            owner_id: The requesting user's id.
            provider: Source host identifier.
            owner_name: Repository owner segment.
            repo_name: Repository name segment.

        Returns:
            The matching repository or ``None``.

        """
        result = await self._session.execute(
            select(Repository).where(
                Repository.owner_id == owner_id,
                Repository.provider == provider,
                Repository.owner_name == owner_name,
                Repository.repo_name == repo_name,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_owner(self, owner_id: uuid.UUID) -> list[Repository]:
        """Return all repositories owned by a user, newest first.

        Args:
            owner_id: The requesting user's id.

        Returns:
            The user's repositories.

        """
        result = await self._session.execute(
            select(Repository)
            .where(Repository.owner_id == owner_id)
            .order_by(Repository.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        provider: str,
        owner_name: str,
        repo_name: str,
        clone_url: str,
    ) -> Repository:
        """Insert a repository and return it.

        Flushes so the generated id is available and a uniqueness violation
        surfaces here. Does not commit.

        Args:
            owner_id: The owning user's id.
            provider: Source host identifier.
            owner_name: Repository owner segment.
            repo_name: Repository name segment.
            clone_url: Normalised HTTPS clone URL.

        Returns:
            The persisted repository.

        """
        repository = Repository(
            owner_id=owner_id,
            provider=provider,
            owner_name=owner_name,
            repo_name=repo_name,
            clone_url=clone_url,
        )
        self._session.add(repository)
        await self._session.flush()
        await self._session.refresh(repository)
        return repository

    async def get_by_id_unscoped(self, repository_id: uuid.UUID) -> Repository | None:
        """Return a repository by id without an owner filter.

        For the worker, which operates on jobs it has claimed and is not acting
        on behalf of a requesting user. Never call this from a request path;
        request paths must use :meth:`get_for_owner`.

        Args:
            repository_id: The repository id.

        Returns:
            The repository or ``None``.

        """
        return await self._session.get(Repository, repository_id)
