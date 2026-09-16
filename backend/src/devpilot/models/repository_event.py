"""Repository-event ORM model.

A lightweight append-only audit trail of what happened to a repository:
registered, index queued, started, completed, failed, or skipped as unchanged.
Rows are written only by the service and worker layers; M3 exposes no public
endpoint that reads them. The table exists so the history is durable and
queryable later without reconstructing it from logs.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from devpilot.core.ids import new_id
from devpilot.db.base import Base


class RepositoryEvent(Base):
    """A single audit event for a repository.

    Attributes:
        id: UUIDv7 primary key.
        repository_id: The repository the event concerns. Cascade-deletes.
        event_type: A stable string from ``services.events``.
        payload: Optional structured context, e.g. a job id or file counts.
        created_at: When the event was recorded.

    """

    __tablename__ = "repository_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        """Return a debug representation."""
        return (
            f"RepositoryEvent(repository_id={self.repository_id!r}, event_type={self.event_type!r})"
        )
