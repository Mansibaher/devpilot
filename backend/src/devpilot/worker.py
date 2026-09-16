"""The indexing worker process.

Run as ``python -m devpilot.worker``. It shares the application's image, models,
repositories, and configuration, and imports no web framework -- the services it
drives were built HTTP-free precisely so they could run here unchanged.

The loop is: reap dead leases, claim one job with ``SKIP LOCKED``, process it
while heartbeating, and record the outcome. An empty queue sleeps rather than
spins. SIGTERM and SIGINT request a graceful stop: the current job finishes and
the process exits, so a deploy or ``docker compose stop`` is clean.

Each job runs in its own database session and transaction. A claim commits
immediately so the running state and incremented attempt are durable even if the
process dies mid-job; the terminal state (succeeded/failed) commits at the end.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import uuid

import structlog

from devpilot.core.config import get_settings
from devpilot.core.logging import configure_logging
from devpilot.db.session import Database
from devpilot.models.index_job import IndexJob
from devpilot.providers.vcs.git_client import GitClient
from devpilot.repositories.event_repo import EventRepository
from devpilot.repositories.file_repo import FileRepository
from devpilot.repositories.job_repo import JobRepository
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.services import events
from devpilot.services.indexing_service import IndexingService

logger = structlog.get_logger(__name__)


def _worker_id() -> str:
    """Return an identifier unique to this worker process.

    Combines hostname, pid, and a short random suffix so two workers on the same
    host, or a restarted worker reusing a pid, never collide as lease holders.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class Worker:
    """Claims and processes index jobs until asked to stop."""

    def __init__(self, database: Database) -> None:
        """Construct the worker.

        Args:
            database: The shared database, providing sessions per job.

        """
        self._db = database
        self._settings = database.settings
        self._id = _worker_id()
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        """Signal the loop to finish the current job and exit."""
        self._stopping.set()

    async def run(self) -> None:
        """Run the claim/process loop until a stop is requested."""
        logger.info("worker_started", worker_id=self._id)
        while not self._stopping.is_set():
            await self._reap()
            job = await self._claim()
            if job is None:
                # Nothing to do; wait, but wake immediately on a stop signal.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(),
                        timeout=self._settings.worker_poll_interval_seconds,
                    )
                continue
            await self._process(job)
        logger.info("worker_stopped", worker_id=self._id)

    async def _reap(self) -> None:
        """Return jobs from dead workers to the queue in their own transaction."""
        async with self._db.session() as session:
            reaped = await JobRepository(session).reap_expired_leases(
                self._settings.worker_lease_timeout_seconds
            )
        if reaped:
            logger.info("leases_reaped", count=reaped, worker_id=self._id)

    async def _claim(self) -> IndexJob | None:
        """Claim one job, committing the claim so it is durable immediately."""
        async with self._db.session() as session:
            job = await JobRepository(session).claim_next(self._id)
        if job is not None:
            logger.info("job_claimed", job_id=str(job.id), attempt=job.attempts, worker_id=self._id)
        return job

    async def _process(self, job: IndexJob) -> None:
        """Index one repository, heartbeating, and record the terminal state.

        The indexing work and the state write share a session and transaction,
        so a repository's status, its files, and the job's outcome move together.
        A heartbeat task runs alongside to keep the lease fresh during a long
        clone or scan.

        Args:
            job: The claimed job.

        """
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        try:
            async with self._db.session() as session:
                service = IndexingService(
                    RepositoryRepository(session),
                    FileRepository(session),
                    JobRepository(session),
                    EventRepository(session),
                    GitClient(self._settings.index_clone_timeout_seconds),
                    self._settings,
                )
                await EventRepository(session).record(
                    job.repository_id, events.EVENT_INDEX_STARTED, {"job_id": str(job.id)}
                )
                outcome = await service.run(job)
                await JobRepository(session).mark_succeeded(job.id, outcome.commit_sha)
            logger.info(
                "job_succeeded",
                job_id=str(job.id),
                skipped=outcome.skipped_unchanged,
                files=outcome.files_indexed,
            )
        except Exception as exc:
            await self._fail(job, exc)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _fail(self, job: IndexJob, exc: Exception) -> None:
        """Record a job failure in a fresh session and emit the failure event.

        A fresh session is used because the processing session may have been
        left in a failed transaction by the exception.

        Args:
            job: The job that failed.
            exc: The raised exception; its message is sanitised for storage.

        """
        message = f"{type(exc).__name__}: {exc}"[:2000]
        async with self._db.session() as session:
            jobs = JobRepository(session)
            # Reload the job so attempts reflects the claim increment.
            fresh = await jobs.get(job.id)
            if fresh is None:
                return
            result = await jobs.mark_failed_or_requeue(fresh, message)
            await EventRepository(session).record(
                job.repository_id,
                events.EVENT_INDEX_FAILED,
                {"job_id": str(job.id), "status": result, "error": message[:500]},
            )
        logger.warning("job_failed", job_id=str(job.id), result=result, error=message[:200])

    async def _heartbeat(self, job_id: uuid.UUID) -> None:
        """Renew a job's lease on an interval until cancelled.

        Args:
            job_id: The running job to keep alive.

        """
        interval = self._settings.worker_heartbeat_interval_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                async with self._db.session() as session:
                    await JobRepository(session).heartbeat(job_id)
            except Exception:
                logger.warning("heartbeat_failed", job_id=str(job_id))


async def _amain() -> None:
    """Configure, wire, and run the worker with signal handling."""
    settings = get_settings()
    configure_logging(log_level=settings.log_level, json_output=not settings.debug)
    database = Database(settings)
    worker = Worker(database)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_stop)

    try:
        await worker.run()
    finally:
        await database.dispose()


def main() -> None:
    """Run the worker synchronously for ``python -m devpilot.worker``."""
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
