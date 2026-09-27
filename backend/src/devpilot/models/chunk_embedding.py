"""Chunk-embedding ORM model.

The vector store. Separate from ``chunks`` on purpose: embeddings can be
regenerated with a different model without rewriting chunk text, and the HNSW
index sits on this narrow table rather than on rows carrying a multi-kilobyte
content blob, which keeps the ANN scan's pages dense.

``repository_id`` is denormalised here so the owner-scoped search can filter and
run the distance operator against one table, with no join before the ANN step.
The embedding column is a fixed ``vector(768)``; that width is a schema
invariant of migration 0004 (see ``Settings`` and its validator).
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from devpilot.db.base import Base

# The one place the numeric dimension appears in the ORM. Kept equal to
# Settings.SCHEMA_EMBEDDING_DIM and the vector(768) column in migration 0004.
EMBEDDING_DIM = 768


class ChunkEmbedding(Base):
    """The embedding vector for one chunk.

    Attributes:
        chunk_id: Primary key and FK to the chunk. One embedding per chunk;
            cascade-deletes with the chunk.
        repository_id: Denormalised owner scope for filtered ANN search.
        model: The model that produced the vector, for provenance.
        dim: The vector dimension, stored for validation and clarity.
        embedding: The vector itself, ``vector(768)``.

    """

    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model: Mapped[str] = mapped_column(nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    def __repr__(self) -> str:
        """Return a debug representation that omits the vector."""
        return f"ChunkEmbedding(chunk_id={self.chunk_id!r}, model={self.model!r}, dim={self.dim})"
