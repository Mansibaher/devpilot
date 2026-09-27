"""Data access for chunks.

Chunk persistence is scoped to a file: a file's chunks are replaced as a unit
(delete then insert) whenever that file changes, which keeps re-indexing
idempotent and never leaves chunks from a previous version of a file behind.
Because ``chunk_embeddings`` cascades from ``chunks``, deleting a file's chunks
also removes their embeddings.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.models.chunk import Chunk


class ChunkRepository:
    """CRUD and per-file replacement for :class:`~devpilot.models.chunk.Chunk`."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The active async session.

        """
        self._session = session

    async def replace_for_file(
        self,
        *,
        repository_id: uuid.UUID,
        file_id: uuid.UUID,
        chunks: Sequence[dict[str, object]],
    ) -> list[Chunk]:
        """Replace all chunks for a file with a new set, returning the inserted rows.

        Deletes the file's existing chunks (cascading their embeddings) and
        inserts the new ones. Flushes so ids are available for embedding.

        Args:
            repository_id: The owning repository.
            file_id: The file whose chunks are being replaced.
            chunks: Mappings with ``chunk_index``, ``start_line``, ``end_line``,
                ``content``, ``content_sha256``, and optional ``token_count``.

        Returns:
            The persisted ``Chunk`` rows, in input order.

        """
        await self._session.execute(delete(Chunk).where(Chunk.file_id == file_id))
        rows = [Chunk(repository_id=repository_id, file_id=file_id, **chunk) for chunk in chunks]
        self._session.add_all(rows)
        await self._session.flush()
        return rows

    async def delete_for_repository_except_files(
        self, repository_id: uuid.UUID, keep_file_ids: Sequence[uuid.UUID]
    ) -> int:
        """Delete chunks belonging to files no longer present in the repository.

        Args:
            repository_id: The owning repository.
            keep_file_ids: File ids that still exist after the current scan.

        Returns:
            The number of chunk rows deleted.

        """
        stmt = delete(Chunk).where(Chunk.repository_id == repository_id)
        if keep_file_ids:
            stmt = stmt.where(Chunk.file_id.not_in(list(keep_file_ids)))
        result = await self._session.execute(stmt)
        return result.rowcount if hasattr(result, "rowcount") else 0

    async def list_for_repository(self, repository_id: uuid.UUID) -> list[Chunk]:
        """Return a repository's chunks ordered by file then position.

        Args:
            repository_id: The owning repository.

        Returns:
            The chunks.

        """
        result = await self._session.execute(
            select(Chunk)
            .where(Chunk.repository_id == repository_id)
            .order_by(Chunk.file_id, Chunk.chunk_index)
        )
        return list(result.scalars().all())

    async def count_for_repository(self, repository_id: uuid.UUID) -> int:
        """Return how many chunks are stored for a repository.

        Args:
            repository_id: The owning repository.

        Returns:
            The chunk count.

        """
        result = await self._session.execute(
            select(Chunk.id).where(Chunk.repository_id == repository_id)
        )
        return len(list(result.scalars().all()))
