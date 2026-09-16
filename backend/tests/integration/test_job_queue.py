"""Integration tests for the durable job queue against real Postgres.

The queue's guarantees -- exclusive claims, lease reaping, attempt bounding --
only exist with a real database and real row locking, so these run against
Postgres and use separate connections where concurrency is the point.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from devpilot.db.session import Database
from devpilot.models.index_job import (
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
)
from devpilot.repositories.job_repo import JobRepository
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.repositories.user_repo import UserRepository

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _clean_queue(database: Database):
    """Empty the job and repository tables before each test.

    These tests commit real rows (the queue's row locking cannot be exercised
    inside a single rolled-back transaction), so without a reset a leftover
    queued job from an earlier test would be visible to ``claim_next`` and make
    the concurrency assertions flaky. Truncating first gives each test a known
    empty queue.
    """
    async with database.session() as s:
        await s.execute(
            text(
                "TRUNCATE users, repositories, index_jobs, "
                "repository_files, repository_events CASCADE"
            )
        )
    yield


async def _seed_repo(session, slug: str) -> uuid.UUID:
    user = await UserRepository(session).create(
        email=f"{slug}-{uuid.uuid4().hex[:6]}@example.com", password_hash="h"
    )
    repo = await RepositoryRepository(session).create(
        owner_id=user.id,
        provider="github",
        owner_name="o",
        repo_name=f"{slug}-{uuid.uuid4().hex[:6]}",
        clone_url="https://github.com/o/r.git",
    )
    return repo.id


async def test_claim_moves_queued_to_running_and_increments_attempts(
    database: Database,
) -> None:
    async with database.session() as s:
        repo_id = await _seed_repo(s, "claim")
        await JobRepository(s).create(repo_id, 3)
    async with database.session() as s:
        job = await JobRepository(s).claim_next("worker-1")
    assert job is not None
    assert job.status == JOB_STATUS_RUNNING
    assert job.attempts == 1
    assert job.locked_by == "worker-1"


async def test_two_workers_never_claim_the_same_job(database: Database) -> None:
    # Two queued jobs (on two repos, since only one active job per repo), two
    # concurrent claimers on independent sessions -> two distinct jobs.
    async with database.session() as s:
        repo_a = await _seed_repo(s, "concur-a")
        repo_b = await _seed_repo(s, "concur-b")
        await JobRepository(s).create(repo_a, 3)
        await JobRepository(s).create(repo_b, 3)

    async def claim(worker: str) -> str | None:
        async with database.session() as s:
            job = await JobRepository(s).claim_next(worker)
            return str(job.id) if job else None

    a, b = await asyncio.gather(claim("A"), claim("B"))
    assert a is not None and b is not None
    assert a != b


async def test_empty_queue_returns_none(database: Database) -> None:
    async with database.session() as s:
        assert await JobRepository(s).claim_next("idle-worker") is None


async def test_idempotent_enqueue_finds_active_job(database: Database) -> None:
    async with database.session() as s:
        repo_id = await _seed_repo(s, "active")
        created = await JobRepository(s).create(repo_id, 3)
    async with database.session() as s:
        active = await JobRepository(s).get_active_for_repository(repo_id)
    assert active is not None
    assert active.id == created.id


async def test_reaper_requeues_an_expired_lease(database: Database) -> None:
    async with database.session() as s:
        repo_id = await _seed_repo(s, "reap")
        job = await JobRepository(s).create(repo_id, 3)
        job_id = job.id
    # Put this specific job into a running state with a stale heartbeat.
    async with database.session() as s:
        jobs = JobRepository(s)
        claimed = await jobs.get(job_id)
        assert claimed is not None
        claimed.status = JOB_STATUS_RUNNING
        claimed.locked_by = "dead-worker"
        claimed.attempts = 1
        claimed.heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
        await s.flush()
    # Reap with a zero-second lease: the running job is immediately expired.
    async with database.session() as s:
        reaped = await JobRepository(s).reap_expired_leases(lease_timeout_seconds=0)
        requeued = await JobRepository(s).get(job_id)
    assert reaped >= 1
    assert requeued is not None
    assert requeued.status == JOB_STATUS_QUEUED
    assert requeued.locked_by is None


async def test_exhausted_job_is_failed_not_requeued(database: Database) -> None:
    async with database.session() as s:
        repo_id = await _seed_repo(s, "exhaust")
        job = await JobRepository(s).create(repo_id, max_attempts=1)
        job_id = job.id
    # Claim this specific job (the shared test DB may hold other queued jobs, so
    # claim_next is not guaranteed to return ours) and drive it to its attempt
    # ceiling, then a failure must be terminal rather than requeued.
    async with database.session() as s:
        jobs = JobRepository(s)
        claimed = await jobs.get(job_id)
        assert claimed is not None
        claimed.attempts = claimed.max_attempts  # simulate the claim increment
        await s.flush()
        result = await jobs.mark_failed_or_requeue(claimed, "boom")
    assert result == JOB_STATUS_FAILED
    async with database.session() as s:
        final = await JobRepository(s).get(job_id)
    assert final is not None
    assert final.status == JOB_STATUS_FAILED
    assert final.error == "boom"
