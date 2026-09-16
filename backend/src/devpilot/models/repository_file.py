"""Repository-file ORM model.

One row per source file kept after discovery and filtering. Stores metadata
only -- path, language, size, and a content checksum -- never file contents in
M3. The checksum is what lets a later milestone re-index incrementally, skipping
files whose blobs have not changed, and it is cheap to compute now while every
kept file is already being read.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from devpilot.core.ids import new_id
from devpilot.db.base import Base


class RepositoryFile(Base):
    """Metadata for one kept file in a repository.

    Attributes:
        id: UUIDv7 primary key.
        repository_id: Owning repository. Cascade-deletes with it.
        path: Repository-relative POSIX path. Unique within the repository.
        language: Detected language, or null when unknown.
        size_bytes: File size in bytes.
        checksum_sha256: SHA-256 of the file contents, hex-encoded.
        created_at: Row creation time.

    """

    __tablename__ = "repository_files"
    __table_args__ = (
        UniqueConstraint("repository_id", "path", name="uq_repository_files_repo_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(nullable=False)
    language: Mapped[str | None] = mapped_column(nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        """Return a debug representation."""
        return (
            f"RepositoryFile(repository_id={self.repository_id!r}, "
            f"path={self.path!r}, language={self.language!r})"
        )
