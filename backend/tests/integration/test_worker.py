"""End-to-end test of the worker loop against real Postgres and real git.

Drives the actual Worker: it claims a queued job, clones a local fixture over
file://, filters and persists the files, and marks the job succeeded -- all
through the same code path production uses. This is the highest-level M3 test.
"""

import subprocess
import uuid
from pathlib import Path

import pytest

from devpilot.db.session import Database
from devpilot.models.index_job import JOB_STATUS_SUCCEEDED
from devpilot.repositories.file_repo import FileRepository
from devpilot.repositories.job_repo import JobRepository
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.repositories.user_repo import UserRepository
from devpilot.worker import Worker

pytestmark = pytest.mark.integration


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={"HOME": "/tmp", "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1"},
    )


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "small"
    repo.mkdir()
    (repo / "main.py").write_text("print('hi')\n")
    (repo / "README.md").write_text("# Small\n")
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@t.com"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    return repo


async def _seed(database: Database, clone_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with database.session() as s:
        user = await UserRepository(s).create(
            email=f"wk-{uuid.uuid4().hex[:6]}@x.com", password_hash="h"
        )
        repo = await RepositoryRepository(s).create(
            owner_id=user.id,
            provider="github",
            owner_name="o",
            repo_name=f"r-{uuid.uuid4().hex[:6]}",
            clone_url=clone_url,
        )
        job = await JobRepository(s).create(repo.id, 3)
        return repo.id, job.id


async def test_worker_processes_a_job_end_to_end(database: Database, small_repo: Path) -> None:
    repo_id, job_id = await _seed(database, f"file://{small_repo}")

    # One claim + process cycle, driven through the real Worker methods.
    worker = Worker(database)
    claimed = await worker._claim()
    assert claimed is not None
    await worker._process(claimed)

    async with database.session() as s:
        job = await JobRepository(s).get(job_id)
        files = await FileRepository(s).list_for_repository(repo_id)
    assert job is not None
    assert job.status == JOB_STATUS_SUCCEEDED
    assert job.commit_sha is not None
    assert job.current_file is None  # cleared on success
    assert {f.path for f in files} == {"main.py", "README.md"}


async def test_worker_fails_a_bad_clone_url(database: Database) -> None:
    # A well-formed but unreachable local path: the clone fails, and the worker
    # must record the failure rather than crash the loop.
    _, job_id = await _seed(database, "file:///nonexistent/path/repo")

    worker = Worker(database)
    claimed = await worker._claim()
    assert claimed is not None
    await worker._process(claimed)

    async with database.session() as s:
        job = await JobRepository(s).get(job_id)
    assert job is not None
    # max_attempts is 3, so a single failure requeues rather than fails.
    assert job.status != JOB_STATUS_SUCCEEDED
    assert job.error is not None


async def test_reap_runs_without_error(database: Database) -> None:
    worker = Worker(database)
    await worker._reap()  # no running jobs; must be a clean no-op
