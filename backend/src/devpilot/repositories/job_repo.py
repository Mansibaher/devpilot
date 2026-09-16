"""Data access for index jobs -- the durable queue.

The queue is a table, and its correctness lives in three statements here:

- ``claim_next`` atomically moves one queued job to running using
  ``FOR UPDATE SKIP LOCKED``, so any number of workers can pull from the same
  table with no coordination and never claim the same row.
- ``reap_expired_leases`` returns jobs whose worker has gone silent to the
  queue (or fails them once attempts are exhausted), which is what makes the
  queue survive a ``kill -9``.
- ``heartbeat`` renews a running job's lease so a slow-but-alive worker is not
  mistaken for a dead one.

Nothing here commits. The worker owns the transaction boundary around each
claim and each state change.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.models.index_job import (
    JOB_ACTIVE_STATES,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    IndexJob,
)


class JobRepository:
    """Queue operations for :class:`~devpilot.models.index_job.IndexJob`."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The active async session.

        """
        self._session = session

    async def get(self, job_id: uuid.UUID) -> IndexJob | None:
        """Return a job by id, or None.

        Args:
            job_id: The job id.

        Returns:
            The job or ``None``.

        """
        return await self._session.get(IndexJob, job_id)

    async def get_for_repository(
        self, job_id: uuid.UUID, repository_id: uuid.UUID
    ) -> IndexJob | None:
        """Return a job only if it belongs to the given repository.

        Args:
            job_id: The job id.
            repository_id: The repository the job must belong to.

        Returns:
            The job, or ``None`` if it does not exist or belongs elsewhere.

        """
        result = await self._session.execute(
            select(IndexJob).where(IndexJob.id == job_id, IndexJob.repository_id == repository_id)
        )
        return result.scalar_one_or_none()

    async def get_active_for_repository(self, repository_id: uuid.UUID) -> IndexJob | None:
        """Return a queued or running job for a repository, if one exists.

        Used to make enqueue idempotent: a duplicate index request attaches to
        this job rather than creating a second.

        Args:
            repository_id: The repository to check.

        Returns:
            An active job, or ``None``.

        """
        result = await self._session.execute(
            select(IndexJob)
            .where(
                IndexJob.repository_id == repository_id,
                IndexJob.status.in_(JOB_ACTIVE_STATES),
            )
            .order_by(IndexJob.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create(self, repository_id: uuid.UUID, max_attempts: int) -> IndexJob:
        """Enqueue a new job for a repository.

        Args:
            repository_id: The repository to index.
            max_attempts: Attempt ceiling for this job.

        Returns:
            The queued job.

        """
        job = IndexJob(repository_id=repository_id, max_attempts=max_attempts)
        self._session.add(job)
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def claim_next(self, worker_id: str) -> IndexJob | None:
        """Atomically claim the oldest queued job for a worker.

        The claim is a single statement: a subquery selects one queued row
        ``FOR UPDATE SKIP LOCKED`` -- locking it and skipping any already locked
        by another worker -- and the outer update flips it to running, stamps
        the lease, and increments attempts. Because the select and update are
        one statement, two concurrent workers cannot both take the same job.

        Args:
            worker_id: Identifier of the claiming worker, stored as the lease
                holder.

        Returns:
            The claimed job, refreshed, or ``None`` if the queue was empty.

        """
        now = datetime.now(UTC)
        stmt = text(
            """
            UPDATE index_jobs SET
                status = :running,
                locked_by = :worker_id,
                locked_at = :now,
                heartbeat_at = :now,
                started_at = COALESCE(started_at, :now),
                attempts = attempts + 1
            WHERE id = (
                SELECT id FROM index_jobs
                WHERE status = :queued
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id
            """
        )
        result = await self._session.execute(
            stmt,
            {
                "running": JOB_STATUS_RUNNING,
                "queued": JOB_STATUS_QUEUED,
                "worker_id": worker_id,
                "now": now,
            },
        )
        row = result.first()
        if row is None:
            return None
        # Re-read through the ORM so the caller gets a fully populated instance.
        # We must NOT expire_all here: the session commits when its context
        # exits, and with expire_on_commit=False a populated instance stays
        # usable after commit, whereas an expired one would trigger a lazy load
        # (and IO) outside any session once the worker moves to a new session.
        job = await self._session.get(IndexJob, row[0])
        if job is not None:
            await self._session.refresh(job)
        return job

    async def heartbeat(self, job_id: uuid.UUID) -> None:
        """Renew a running job's lease.

        Args:
            job_id: The job to keep alive.

        """
        await self._session.execute(
            update(IndexJob).where(IndexJob.id == job_id).values(heartbeat_at=datetime.now(UTC))
        )

    async def update_progress(
        self,
        job_id: uuid.UUID,
        *,
        files_total: int | None = None,
        files_done: int | None = None,
        current_file: str | None = None,
    ) -> None:
        """Update progress fields on a running job.

        Only provided fields are written, so a caller can advance ``files_done``
        without disturbing ``files_total``.

        Args:
            job_id: The job to update.
            files_total: Total discovered files, when known.
            files_done: Files processed so far.
            current_file: Path currently being processed.

        """
        values: dict[str, object] = {}
        if files_total is not None:
            values["files_total"] = files_total
        if files_done is not None:
            values["files_done"] = files_done
        values["current_file"] = current_file
        await self._session.execute(update(IndexJob).where(IndexJob.id == job_id).values(**values))

    async def mark_succeeded(self, job_id: uuid.UUID, commit_sha: str) -> None:
        """Mark a job succeeded and clear its transient fields.

        Args:
            job_id: The job to complete.
            commit_sha: The indexed HEAD SHA.

        """
        await self._session.execute(
            update(IndexJob)
            .where(IndexJob.id == job_id)
            .values(
                status="succeeded",
                commit_sha=commit_sha,
                current_file=None,
                locked_by=None,
                locked_at=None,
                finished_at=datetime.now(UTC),
            )
        )

    async def mark_failed_or_requeue(self, job: IndexJob, error: str) -> str:
        """Fail a job permanently, or return it to the queue for another attempt.

        A job that has reached ``max_attempts`` is marked failed; otherwise it
        goes back to queued and becomes claimable again. Either way the lease
        and current-file fields are cleared.

        Args:
            job: The job that failed. Its ``attempts`` reflects this run.
            error: A sanitised, truncated failure message.

        Returns:
            The resulting status, ``failed`` or ``queued``.

        """
        exhausted = job.attempts >= job.max_attempts
        new_status = JOB_STATUS_FAILED if exhausted else JOB_STATUS_QUEUED
        await self._session.execute(
            update(IndexJob)
            .where(IndexJob.id == job.id)
            .values(
                status=new_status,
                error=error[:2000],
                current_file=None,
                locked_by=None,
                locked_at=None,
                finished_at=datetime.now(UTC) if exhausted else None,
            )
        )
        return new_status

    async def reap_expired_leases(self, lease_timeout_seconds: float) -> int:
        """Requeue or fail jobs whose worker has gone silent.

        A running job whose heartbeat is older than the lease timeout is assumed
        to belong to a dead worker. If it still has attempts left it returns to
        the queue; if not, it is failed. This is what unwedges the queue after a
        worker is killed mid-job.

        Args:
            lease_timeout_seconds: Age past which a lease is considered dead.

        Returns:
            The number of jobs reaped.

        """
        cutoff = datetime.now(UTC) - timedelta(seconds=lease_timeout_seconds)
        requeue = text(
            """
            UPDATE index_jobs SET
                status = CASE WHEN attempts >= max_attempts THEN :failed ELSE :queued END,
                locked_by = NULL,
                locked_at = NULL,
                current_file = NULL,
                error = CASE WHEN attempts >= max_attempts
                             THEN 'worker lease expired' ELSE error END,
                finished_at = CASE WHEN attempts >= max_attempts
                                   THEN CAST(:now AS timestamptz) ELSE NULL END
            WHERE status = :running AND heartbeat_at < :cutoff
            RETURNING id
            """
        )
        result = await self._session.execute(
            requeue,
            {
                "failed": JOB_STATUS_FAILED,
                "queued": JOB_STATUS_QUEUED,
                "running": JOB_STATUS_RUNNING,
                "cutoff": cutoff,
                "now": datetime.now(UTC),
            },
        )
        return len(result.fetchall())
