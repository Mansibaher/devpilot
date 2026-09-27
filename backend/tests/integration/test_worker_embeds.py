"""End-to-end test: the worker chunks and embeds a repository.

Drives the real Worker over a local git fixture with the fake embedding
provider (pinned in integration_settings). Proves the M4 stage the worker gained
-- chunk -> embed -> persist -- runs inside the existing index job, produces
searchable vectors, honours the unchanged-HEAD short-circuit, and re-embeds only
changed files on re-index. No model download, no network.
"""

import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from devpilot.db.session import Database
from devpilot.models.index_job import JOB_STATUS_SUCCEEDED
from devpilot.providers.embeddings.fake import FakeEmbeddingProvider
from devpilot.repositories.chunk_repo import ChunkRepository
from devpilot.repositories.embedding_repo import EmbeddingRepository
from devpilot.repositories.job_repo import JobRepository
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.repositories.user_repo import UserRepository
from devpilot.worker import Worker

pytestmark = pytest.mark.integration

_DIM = 768


@pytest.fixture(autouse=True)
async def _clean_tables(database: Database):
    """Empty the queue and repository tables before each test.

    These tests drive the real Worker, whose ``claim_next`` takes the globally
    oldest queued job. Other integration tests commit jobs to the same shared
    database, so without a reset ``claim_next`` could pick up a leftover job
    instead of the one the test seeded. Truncating first guarantees the only
    claimable job is this test's.
    """
    async with database.session() as s:
        await s.execute(
            text(
                "TRUNCATE users, repositories, index_jobs, "
                "repository_files, chunks, chunk_embeddings, "
                "repository_events CASCADE"
            )
        )
    yield


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={"HOME": "/tmp", "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1"},
    )


@pytest.fixture
def code_repo(tmp_path: Path) -> Path:
    """A local git repo with a couple of source files worth chunking."""
    repo = tmp_path / "codebase"
    repo.mkdir()
    (repo / "auth.py").write_text("\n".join(f"def fn_{i}(): return {i}" for i in range(60)) + "\n")
    (repo / "README.md").write_text("# Codebase\n\nDocs here.\n")
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@t.com"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    return repo


async def _seed(database: Database, clone_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with database.session() as s:
        user = await UserRepository(s).create(
            email=f"we-{uuid.uuid4().hex[:6]}@x.com", password_hash="h"
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


async def test_worker_chunks_and_embeds(database: Database, code_repo: Path) -> None:
    repo_id, job_id = await _seed(database, f"file://{code_repo}")

    worker = Worker(database)
    claimed = await worker._claim()
    assert claimed is not None
    await worker._process(claimed)

    async with database.session() as s:
        job = await JobRepository(s).get(job_id)
        chunk_count = await ChunkRepository(s).count_for_repository(repo_id)
        emb_count = await EmbeddingRepository(s).count_for_repository(repo_id)
    assert job is not None
    assert job.status == JOB_STATUS_SUCCEEDED
    # auth.py (60 lines, window 40 / overlap 10 -> step 30: [1-40],[31-60]) = 2
    # README.md (3 lines) = 1  => 3 chunks total, each embedded.
    assert chunk_count == 3
    assert emb_count == 3


async def test_worker_result_is_searchable(database: Database, code_repo: Path) -> None:
    repo_id, _ = await _seed(database, f"file://{code_repo}")
    worker = Worker(database)
    claimed = await worker._claim()
    await worker._process(claimed)

    # A query equal to a chunk's content retrieves it first (deterministic fake).
    fake = FakeEmbeddingProvider(dim=_DIM)
    async with database.session() as s:
        chunks = await ChunkRepository(s).list_for_repository(repo_id)
        target = chunks[0]
        qvec = fake.embed_query(target.content)
        hits = await EmbeddingRepository(s).search(
            repository_id=repo_id, query_vector=qvec, top_k=5
        )
    assert hits
    assert hits[0].chunk_id == target.id
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)


async def test_unchanged_head_does_not_reembed(database: Database, code_repo: Path) -> None:
    repo_id, _ = await _seed(database, f"file://{code_repo}")
    worker = Worker(database)
    await worker._process(await worker._claim())

    async with database.session() as s:
        first = await EmbeddingRepository(s).count_for_repository(repo_id)
        await JobRepository(s).create(repo_id, 3)
    # Second run over an unchanged HEAD must short-circuit before chunk/embed.
    claimed2 = await worker._claim()
    await worker._process(claimed2)
    async with database.session() as s:
        second = await EmbeddingRepository(s).count_for_repository(repo_id)
    assert first == second == 3


async def test_changed_file_reembeds_incrementally(database: Database, code_repo: Path) -> None:
    repo_id, _ = await _seed(database, f"file://{code_repo}")
    worker = Worker(database)
    await worker._process(await worker._claim())
    async with database.session() as s:
        before = {c.content_sha256 for c in await ChunkRepository(s).list_for_repository(repo_id)}

    # Change one file and commit, then re-index.
    (code_repo / "README.md").write_text("# Codebase\n\nCompletely different docs now.\n")
    _git(["commit", "-aqm", "update readme"], code_repo)
    async with database.session() as s:
        await JobRepository(s).create(repo_id, 3)
    await worker._process(await worker._claim())

    async with database.session() as s:
        after = {c.content_sha256 for c in await ChunkRepository(s).list_for_repository(repo_id)}
        emb_count = await EmbeddingRepository(s).count_for_repository(repo_id)
    # The README chunk changed; the auth.py chunks did not.
    assert before != after
    assert emb_count == 3  # still 3 chunks, embeddings kept in sync
