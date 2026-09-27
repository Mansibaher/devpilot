"""chunks and chunk_embeddings

Adds the M4 vector store: a ``chunks`` table holding each file's line-window
chunks, and a separate ``chunk_embeddings`` table holding one vector per chunk.
They are separate so embeddings can be regenerated with a new model without
rewriting chunk text, and so the HNSW index sits on a narrow table.

The embedding column is ``vector(768)`` to match jina-embeddings-v2-base-code.
That dimension is a schema invariant of this revision: changing the embedding
model to a different width requires a new migration that alters this column and
rebuilds the HNSW index, not merely a configuration change. The application's
settings validator enforces the same 768 at startup.

The HNSW index (cosine ops) is created explicitly here because autogenerate does
not emit vector indexes. The M3 partial index ``ix_index_jobs_claimable`` is
deliberately left untouched -- autogenerate cannot see its ``WHERE`` predicate
and wrongly proposes dropping it, which this migration does not do.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the chunks and chunk_embeddings tables and the HNSW index."""
    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("content_sha256", sa.String(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["repository_files.id"],
            name=op.f("fk_chunks_file_id_repository_files"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_chunks_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint("file_id", "chunk_index", name="uq_chunks_file_index"),
    )
    op.create_index(op.f("ix_chunks_file_id"), "chunks", ["file_id"], unique=False)
    op.create_index(op.f("ix_chunks_repository_id"), "chunks", ["repository_id"], unique=False)

    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=768), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_chunk_embeddings_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_chunk_embeddings_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", name=op.f("pk_chunk_embeddings")),
    )
    op.create_index(
        op.f("ix_chunk_embeddings_repository_id"),
        "chunk_embeddings",
        ["repository_id"],
        unique=False,
    )
    # HNSW index for approximate nearest-neighbour search under cosine distance.
    # HNSW (not IVFFlat) because it builds incrementally as repos arrive and
    # needs no training set or list retuning as the table grows.
    op.create_index(
        "ix_chunk_embeddings_hnsw",
        "chunk_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Drop the M4 tables and indexes, leaving M3 objects untouched."""
    op.drop_index("ix_chunk_embeddings_hnsw", table_name="chunk_embeddings")
    op.drop_index(op.f("ix_chunk_embeddings_repository_id"), table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_index(op.f("ix_chunks_repository_id"), table_name="chunks")
    op.drop_index(op.f("ix_chunks_file_id"), table_name="chunks")
    op.drop_table("chunks")
