"""Chunk ORM model.

One row per line-window chunk of a file. Stores the chunk's text and its
1-based inclusive line span, so a search result can cite exactly where in the
file the content lives. Chunks belong to both a repository and a specific file;
the file link is what lets a changed file's chunks be replaced without touching
the rest of the repository.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from devpilot.core.ids import new_id
from devpilot.db.base import Base


class Chunk(Base):
    """A line-window chunk of a repository file.

    Attributes:
        id: UUIDv7 primary key (row identity).
        repository_id: Owning repository. Cascade-deletes with it.
        file_id: Owning file. Cascade-deletes with it, so replacing a file's
            chunks is a delete-by-file plus insert.
        chunk_index: Zero-based ordinal within the file.
        start_line: First line, 1-based inclusive.
        end_line: Last line, 1-based inclusive.
        content: The chunk's source text.
        content_sha256: Deterministic content-and-position checksum.
        token_count: Advisory token estimate, nullable.
        created_at: Row creation time.

    """

    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("file_id", "chunk_index", name="uq_chunks_file_index"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repository_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    content_sha256: Mapped[str] = mapped_column(nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        """Return a debug representation."""
        return (
            f"Chunk(id={self.id!r}, file_id={self.file_id!r}, "
            f"lines={self.start_line}-{self.end_line})"
        )
