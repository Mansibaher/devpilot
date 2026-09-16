"""Data access for repository files.

Persistence here is idempotent by construction. Files are written with an upsert
on ``(repository_id, path)``, so re-running a job over the same repository
converges to the same rows rather than duplicating or erroring, and a retry
after a partial failure heals instead of leaving orphans. Paths that existed
before but are absent from the current scan are deleted, so the stored set
always matches the repository as cloned.
"""

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.models.repository_file import RepositoryFile


class FileRepository:
    """Bulk, idempotent persistence for repository files."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The active async session.

        """
        self._session = session

    async def upsert_many(
        self,
        repository_id: uuid.UUID,
        files: Sequence[dict[str, object]],
    ) -> int:
        """Insert or update a batch of files for a repository.

        On a ``(repository_id, path)`` conflict the language, size, and checksum
        are refreshed, so a file whose contents changed between runs is updated
        in place rather than duplicated.

        Args:
            repository_id: The owning repository.
            files: Mappings with ``path``, ``language``, ``size_bytes``, and
                ``checksum_sha256``.

        Returns:
            The number of rows submitted.

        """
        if not files:
            return 0
        rows = [{"repository_id": repository_id, **f} for f in files]
        stmt = pg_insert(RepositoryFile).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[RepositoryFile.repository_id, RepositoryFile.path],
            set_={
                "language": stmt.excluded.language,
                "size_bytes": stmt.excluded.size_bytes,
                "checksum_sha256": stmt.excluded.checksum_sha256,
            },
        )
        await self._session.execute(stmt)
        return len(rows)

    async def delete_paths_not_in(self, repository_id: uuid.UUID, keep_paths: Sequence[str]) -> int:
        """Delete a repository's files whose paths are not in the kept set.

        Args:
            repository_id: The owning repository.
            keep_paths: Paths that survived the current scan.

        Returns:
            The number of rows deleted.

        """
        stmt = delete(RepositoryFile).where(RepositoryFile.repository_id == repository_id)
        if keep_paths:
            stmt = stmt.where(RepositoryFile.path.not_in(list(keep_paths)))
        # A DELETE yields a CursorResult, whose rowcount is the number of rows
        # removed. execute_options pins the type so mypy sees rowcount.
        result = cast("CursorResult[object]", await self._session.execute(stmt))
        return result.rowcount

    async def list_for_repository(self, repository_id: uuid.UUID) -> list[RepositoryFile]:
        """Return a repository's files, ordered by path.

        Args:
            repository_id: The owning repository.

        Returns:
            The repository's files.

        """
        result = await self._session.execute(
            select(RepositoryFile)
            .where(RepositoryFile.repository_id == repository_id)
            .order_by(RepositoryFile.path.asc())
        )
        return list(result.scalars().all())

    async def count_for_repository(self, repository_id: uuid.UUID) -> int:
        """Return how many files are stored for a repository.

        Args:
            repository_id: The owning repository.

        Returns:
            The file count.

        """
        result = await self._session.execute(
            select(RepositoryFile.id).where(RepositoryFile.repository_id == repository_id)
        )
        return len(list(result.scalars().all()))
