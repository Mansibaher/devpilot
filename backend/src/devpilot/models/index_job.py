"""Index-job ORM model.

One table serving two roles: it is the durable work queue the worker claims
from, and it is the resource the status endpoint reads. The queue mechanics
(claiming, leasing, reaping) live in the job repository; this file only
describes the shape and the legal states.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from devpilot.core.ids import new_id
from devpilot.db.base import Base

# Job states. A job is created 'queued', a worker moves it to 'running' when it
# claims it, and it ends 'succeeded' or 'failed'. A crashed worker's 'running'
# job is returned to 'queued' by lease reaping, so 'running' is never terminal.
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"

# States in which a repository already has active work, so a duplicate index
# request attaches to the existing job rather than creating another.
JOB_ACTIVE_STATES = (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)


class IndexJob(Base):
    """A unit of indexing work for one repository.

    Attributes:
        id: UUIDv7 primary key.
        repository_id: The repository being indexed. Cascade-deletes with it.
        status: One of the module-level state constants.
        commit_sha: HEAD SHA the job cloned; set once the clone succeeds.
        attempts: Incremented each time a worker claims the job, so a worker
            that dies mid-run without recording failure still consumes an
            attempt and crash-loops are bounded.
        max_attempts: Attempts before the job is marked permanently failed.
        error: Last failure message, sanitised and truncated. Never a stack
            trace, never a URL carrying credentials.
        files_total: Discovered file count; populated once discovery completes.
        files_done: Files persisted so far, for progress reporting.
        current_file: Path currently being processed. Cleared on completion.
        locked_by: Identifier of the worker holding the lease.
        locked_at: When the lease was taken.
        heartbeat_at: Last liveness renewal; staleness triggers reaping.
        created_at: Enqueue time; the claim order.
        started_at: First claim time.
        finished_at: Completion time, success or failure.

    """

    __tablename__ = "index_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(nullable=False, default=JOB_STATUS_QUEUED)
    commit_sha: Mapped[str | None] = mapped_column(nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    error: Mapped[str | None] = mapped_column(nullable=True)
    files_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    files_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_file: Mapped[str | None] = mapped_column(nullable=True)
    locked_by: Mapped[str | None] = mapped_column(nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        """Return a debug representation."""
        return (
            f"IndexJob(id={self.id!r}, repository_id={self.repository_id!r}, "
            f"status={self.status!r}, attempts={self.attempts})"
        )
