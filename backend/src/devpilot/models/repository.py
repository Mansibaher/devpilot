"""Repository ORM model.

A GitHub repository that a user has registered for indexing. Every row is owned
by exactly one user, and uniqueness is scoped to that owner: two users may each
register the same public repository independently, and neither can see the
other's copy. That owner scoping is the tenancy boundary the whole M3 access
model rests on.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from devpilot.core.ids import new_id
from devpilot.db.base import Base

# Repository lifecycle states. Strings, not a DB enum, so adding a state later
# is a code change rather than a migration that mutates a Postgres type.
REPO_STATUS_PENDING = "pending"
REPO_STATUS_INDEXING = "indexing"
REPO_STATUS_INDEXED = "indexed"
REPO_STATUS_FAILED = "failed"


class Repository(Base):
    """A registered GitHub repository.

    Attributes:
        id: UUIDv7 primary key.
        owner_id: The user who registered it. Cascade-deletes with the user.
        provider: Source host. Always ``github`` in M3; the column exists so
            other providers are additive rather than a schema change.
        owner_name: The repository's GitHub owner, e.g. ``torvalds``.
        repo_name: The repository name, e.g. ``linux``.
        clone_url: The normalised HTTPS clone URL.
        default_branch: Discovered at clone time; null until first indexed.
        last_indexed_sha: HEAD commit of the last successful index. Drives the
            unchanged-repo short-circuit.
        status: Lifecycle state (see the module-level constants).
        created_at: Row creation time.
        updated_at: Last modification time.

    """

    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "provider",
            "owner_name",
            "repo_name",
            name="uq_repositories_owner_provider_slug",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(nullable=False, default="github")
    owner_name: Mapped[str] = mapped_column(nullable=False)
    repo_name: Mapped[str] = mapped_column(nullable=False)
    clone_url: Mapped[str] = mapped_column(nullable=False)
    default_branch: Mapped[str | None] = mapped_column(nullable=True)
    last_indexed_sha: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, default=REPO_STATUS_PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        """Return a debug representation."""
        return (
            f"Repository(id={self.id!r}, owner_id={self.owner_id!r}, "
            f"slug={self.owner_name}/{self.repo_name}, status={self.status!r})"
        )
