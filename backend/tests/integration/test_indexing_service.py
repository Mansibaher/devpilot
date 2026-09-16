"""Integration tests for the indexing service against a real git repository.

No network: a fixture repository is created locally with ``git init`` and cloned
over ``file://``, which exercises the real GitClient, real filtering, real
checksums, and real persistence. This is the closest test to what the worker
does in production without reaching GitHub.
"""

import subprocess
import uuid
from pathlib import Path

import pytest

from devpilot.core.config import Settings
from devpilot.db.session import Database
from devpilot.providers.vcs.git_client import GitClient
from devpilot.repositories.event_repo import EventRepository
from devpilot.repositories.file_repo import FileRepository
from devpilot.repositories.job_repo import JobRepository
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.repositories.user_repo import UserRepository
from devpilot.services import events
from devpilot.services.indexing_service import IndexingService

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
def fixture_repo(tmp_path: Path) -> Path:
    """Create a local git repo covering every filter rule and return its path."""
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "node_modules" / "dep").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def hello():\n    return 1\n")
    (repo / "src" / "index.ts").write_text("export const x = 1;\n")
    (repo / "README.md").write_text("# Fixture\n")
    (repo / "LICENSE").write_text("MIT\n")
    (repo / "Dockerfile").write_text("FROM python:3.12\n")
    (repo / ".env").write_text("SECRET=real\n")  # dropped
    (repo / ".env.example").write_text("SECRET=example\n")  # kept
    (repo / "package-lock.json").write_text('{"lockfileVersion":3}\n')  # skipped
    (repo / "node_modules" / "dep" / "index.js").write_text("junk\n")  # excluded dir
    (repo / "src" / "logo.png").write_bytes(b"PNG\x00\x00binary")  # binary
    (repo / "mystery.xyz").write_text("unknown\n")  # unsupported
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@t.com"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "fixture"], repo)
    return repo


async def _seed_and_claim(database: Database, clone_url: str):
    async with database.session() as s:
        user = await UserRepository(s).create(
            email=f"ix-{uuid.uuid4().hex[:6]}@x.com", password_hash="h"
        )
        repo = await RepositoryRepository(s).create(
            owner_id=user.id,
            provider="github",
            owner_name="o",
            repo_name=f"r-{uuid.uuid4().hex[:6]}",
            clone_url=clone_url,
        )
        job = await JobRepository(s).create(repo.id, 3)
        repo_id = repo.id
        job_id = job.id
    # Claim this specific job rather than "the next queued job", so a leftover
    # job from another test in the shared database cannot be picked up here.
    async with database.session() as s:
        claimed = await _claim_specific(s, job_id)
    return repo_id, claimed


async def _claim_specific(session, job_id: uuid.UUID):
    """Claim one job by id, mirroring claim_next's state transition."""
    from datetime import UTC, datetime

    from devpilot.models.index_job import JOB_STATUS_RUNNING

    job = await JobRepository(session).get(job_id)
    assert job is not None
    now = datetime.now(UTC)
    job.status = JOB_STATUS_RUNNING
    job.locked_by = "test-worker"
    job.locked_at = now
    job.heartbeat_at = now
    job.started_at = now
    job.attempts += 1
    await session.flush()
    return job


def _service(session, settings: Settings) -> IndexingService:
    return IndexingService(
        RepositoryRepository(session),
        FileRepository(session),
        JobRepository(session),
        EventRepository(session),
        GitClient(settings.index_clone_timeout_seconds),
        settings,
    )


async def test_indexes_only_the_expected_files(
    database: Database, integration_settings: Settings, fixture_repo: Path
) -> None:
    repo_id, job = await _seed_and_claim(database, f"file://{fixture_repo}")
    async with database.session() as s:
        outcome = await _service(s, integration_settings).run(job)
        await JobRepository(s).mark_succeeded(job.id, outcome.commit_sha)

    assert outcome.files_indexed == 6
    assert not outcome.skipped_unchanged

    async with database.session() as s:
        files = await FileRepository(s).list_for_repository(repo_id)
    paths = {f.path for f in files}
    assert paths == {
        ".env.example",
        "Dockerfile",
        "LICENSE",
        "README.md",
        "src/app.py",
        "src/index.ts",
    }
    # Secrets, lockfiles, vendored code, binaries, unknown types all excluded.
    assert ".env" not in paths
    assert "package-lock.json" not in paths
    assert "node_modules/dep/index.js" not in paths
    assert "src/logo.png" not in paths
    assert "mystery.xyz" not in paths


async def test_records_language_and_checksum(
    database: Database, integration_settings: Settings, fixture_repo: Path
) -> None:
    repo_id, job = await _seed_and_claim(database, f"file://{fixture_repo}")
    async with database.session() as s:
        outcome = await _service(s, integration_settings).run(job)
        await JobRepository(s).mark_succeeded(job.id, outcome.commit_sha)
    async with database.session() as s:
        files = {f.path: f for f in await FileRepository(s).list_for_repository(repo_id)}
    assert files["src/app.py"].language == "python"
    assert files["src/index.ts"].language == "typescript"
    assert files["Dockerfile"].language == "dockerfile"
    # Checksum is a 64-char hex SHA-256.
    assert len(files["src/app.py"].checksum_sha256) == 64


async def test_unchanged_head_short_circuits(
    database: Database, integration_settings: Settings, fixture_repo: Path
) -> None:
    repo_id, job1 = await _seed_and_claim(database, f"file://{fixture_repo}")
    async with database.session() as s:
        o1 = await _service(s, integration_settings).run(job1)
        await JobRepository(s).mark_succeeded(job1.id, o1.commit_sha)

    # A second job over the same, unchanged repo must skip the scan.
    async with database.session() as s:
        job2_row = await JobRepository(s).create(repo_id, 3)
        job2_id = job2_row.id
    async with database.session() as s:
        job2 = await _claim_specific(s, job2_id)
    async with database.session() as s:
        o2 = await _service(s, integration_settings).run(job2)
        await JobRepository(s).mark_succeeded(job2.id, o2.commit_sha)

    assert o2.skipped_unchanged
    assert o2.files_indexed == 0
    async with database.session() as s:
        evt_types = [e.event_type for e in await EventRepository(s).list_for_repository(repo_id)]
    assert events.EVENT_INDEX_SKIPPED_UNCHANGED in evt_types


async def test_reindex_prunes_deleted_files(
    database: Database, integration_settings: Settings, fixture_repo: Path
) -> None:
    repo_id, job1 = await _seed_and_claim(database, f"file://{fixture_repo}")
    async with database.session() as s:
        o1 = await _service(s, integration_settings).run(job1)
        await JobRepository(s).mark_succeeded(job1.id, o1.commit_sha)

    # Remove a file and commit, so a re-index must drop it from storage.
    (fixture_repo / "src" / "index.ts").unlink()
    _git(["commit", "-aqm", "remove index.ts"], fixture_repo)

    async with database.session() as s:
        job2_row = await JobRepository(s).create(repo_id, 3)
        job2_id = job2_row.id
    async with database.session() as s:
        job2 = await _claim_specific(s, job2_id)
    async with database.session() as s:
        await _service(s, integration_settings).run(job2)
        await JobRepository(s).mark_succeeded(job2.id, "unused")
    async with database.session() as s:
        paths = {f.path for f in await FileRepository(s).list_for_repository(repo_id)}
    assert "src/index.ts" not in paths
    assert "src/app.py" in paths
