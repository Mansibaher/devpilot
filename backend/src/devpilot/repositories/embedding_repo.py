"""Data access for chunk embeddings, including the vector search query.

Holds the two operations that touch the vector store: inserting embeddings for a
batch of chunks, and the owner-scoped approximate-nearest-neighbour search that
powers the search endpoint. The distance operator and the ordering live here, in
the only module that knows the embeddings are vectors.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import bindparam, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.models.chunk_embedding import ChunkEmbedding


@dataclass(frozen=True)
class SearchHit:
    """One ranked search result joined from chunk and embedding.

    Attributes:
        chunk_id: The chunk's id.
        path: The file path (joined from repository_files).
        language: The file language, or None.
        start_line: First line of the chunk, 1-based inclusive.
        end_line: Last line of the chunk, 1-based inclusive.
        content: The chunk's source text.
        score: Cosine similarity in [0, 1]; higher is closer.

    """

    chunk_id: uuid.UUID
    path: str
    language: str | None
    start_line: int
    end_line: int
    content: str
    score: float


class EmbeddingRepository:
    """Insert embeddings and run vector search over them."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The active async session.

        """
        self._session = session

    async def add_many(
        self,
        *,
        repository_id: uuid.UUID,
        model: str,
        dim: int,
        items: Sequence[tuple[uuid.UUID, list[float]]],
    ) -> int:
        """Insert embeddings for a batch of chunks.

        Existing embeddings for these chunk ids are removed first, so a
        re-embed replaces rather than conflicts on the primary key. In normal
        flow the chunks are freshly inserted and have none, but this keeps the
        operation safe to repeat.

        Args:
            repository_id: The owning repository (denormalised onto each row).
            model: The model that produced the vectors.
            dim: The vector dimension.
            items: (chunk_id, vector) pairs.

        Returns:
            The number of embeddings inserted.

        """
        if not items:
            return 0
        chunk_ids = [cid for cid, _ in items]
        await self._session.execute(
            delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids))
        )
        rows = [
            ChunkEmbedding(
                chunk_id=chunk_id,
                repository_id=repository_id,
                model=model,
                dim=dim,
                embedding=vector,
            )
            for chunk_id, vector in items
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return len(rows)

    async def count_for_repository(self, repository_id: uuid.UUID) -> int:
        """Return how many embeddings exist for a repository.

        Used to answer "is this repository searchable yet?" before running a
        query, so an unindexed repository gets a clear 409 rather than an empty
        result.

        Args:
            repository_id: The repository to check.

        Returns:
            The embedding count.

        """
        result = await self._session.execute(
            text("SELECT count(*) FROM chunk_embeddings WHERE repository_id = :rid").bindparams(
                rid=repository_id
            )
        )
        return int(result.scalar_one())

    async def search(
        self, *, repository_id: uuid.UUID, query_vector: list[float], top_k: int
    ) -> list[SearchHit]:
        """Return the top-k chunks nearest a query vector, owner already checked.

        Filters to the repository, orders by cosine distance via pgvector's
        ``<=>`` operator, and returns similarity as ``1 - distance`` so callers
        see a higher-is-better score. Because embeddings are unit-normalised at
        write time, this similarity is exact.

        Args:
            repository_id: The repository to search within. Ownership must have
                been verified by the caller before this runs.
            query_vector: The embedded query.
            top_k: Maximum results to return.

        Returns:
            Ranked hits, closest first.

        """
        # pgvector accepts the vector as a bracketed string literal cast to
        # vector; bound as a parameter so the query is never built by string
        # concatenation.
        vector_literal = "[" + ",".join(repr(float(x)) for x in query_vector) + "]"
        stmt = text(
            """
                SELECT
                    c.id            AS chunk_id,
                    f.path          AS path,
                    f.language      AS language,
                    c.start_line    AS start_line,
                    c.end_line      AS end_line,
                    c.content       AS content,
                    1 - (e.embedding <=> CAST(:qvec AS vector)) AS score
                FROM chunk_embeddings e
                JOIN chunks c ON c.id = e.chunk_id
                JOIN repository_files f ON f.id = c.file_id
                WHERE e.repository_id = :rid
                ORDER BY e.embedding <=> CAST(:qvec AS vector)
                LIMIT :k
                """
        ).bindparams(
            bindparam("qvec", value=vector_literal),
            bindparam("rid", value=repository_id),
            bindparam("k", value=top_k),
        )
        result = await self._session.execute(stmt)
        return [
            SearchHit(
                chunk_id=row.chunk_id,
                path=row.path,
                language=row.language,
                start_line=row.start_line,
                end_line=row.end_line,
                content=row.content,
                score=float(row.score),
            )
            for row in result
        ]
