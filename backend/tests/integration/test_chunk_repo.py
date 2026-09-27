"""Integration tests for chunk and embedding storage against real pgvector.

Uses the fake embedding provider -- deterministic, no model, no network -- so
the vector column, the HNSW-backed search, and the cascade behaviour are all
exercised against a real database without downloading anything.
"""

import uuid

import pytest

from devpilot.db.session import Database
from devpilot.providers.embeddings.fake import FakeEmbeddingProvider
from devpilot.repositories.chunk_repo import ChunkRepository
from devpilot.repositories.embedding_repo import EmbeddingRepository
from devpilot.repositories.file_repo import FileRepository
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.repositories.user_repo import UserRepository

pytestmark = pytest.mark.integration

_DIM = 768


async def _seed_file(session) -> tuple[uuid.UUID, uuid.UUID]:
    user = await UserRepository(session).create(
        email=f"ch-{uuid.uuid4().hex[:6]}@x.com", password_hash="h"
    )
    repo = await RepositoryRepository(session).create(
        owner_id=user.id,
        provider="github",
        owner_name="o",
        repo_name=f"r-{uuid.uuid4().hex[:6]}",
        clone_url="https://github.com/o/r.git",
    )
    await FileRepository(session).upsert_many(
        repo.id,
        [{"path": "a.py", "language": "python", "size_bytes": 10, "checksum_sha256": "x"}],
    )
    files = await FileRepository(session).list_for_repository(repo.id)
    return repo.id, files[0].id


async def test_replace_for_file_inserts_chunks(database: Database) -> None:
    async with database.session() as s:
        repo_id, file_id = await _seed_file(s)
        rows = await ChunkRepository(s).replace_for_file(
            repository_id=repo_id,
            file_id=file_id,
            chunks=[
                {
                    "chunk_index": 0,
                    "start_line": 1,
                    "end_line": 40,
                    "content": "chunk one",
                    "content_sha256": "aaa",
                },
                {
                    "chunk_index": 1,
                    "start_line": 31,
                    "end_line": 70,
                    "content": "chunk two",
                    "content_sha256": "bbb",
                },
            ],
        )
        assert len(rows) == 2
        count = await ChunkRepository(s).count_for_repository(repo_id)
    assert count == 2


async def test_replace_for_file_is_idempotent(database: Database) -> None:
    async with database.session() as s:
        repo_id, file_id = await _seed_file(s)
        repo = ChunkRepository(s)
        first = [
            {
                "chunk_index": 0,
                "start_line": 1,
                "end_line": 40,
                "content": "v1",
                "content_sha256": "a",
            }
        ]
        await repo.replace_for_file(repository_id=repo_id, file_id=file_id, chunks=first)
        # Re-run with different content: old chunk replaced, not duplicated.
        second = [
            {
                "chunk_index": 0,
                "start_line": 1,
                "end_line": 40,
                "content": "v2",
                "content_sha256": "b",
            }
        ]
        await repo.replace_for_file(repository_id=repo_id, file_id=file_id, chunks=second)
        chunks = await repo.list_for_repository(repo_id)
    assert len(chunks) == 1
    assert chunks[0].content == "v2"


async def test_embeddings_insert_and_search(database: Database) -> None:
    fake = FakeEmbeddingProvider(dim=_DIM)
    texts = ["def login(): ...", "def logout(): ...", "class Parser: ..."]
    async with database.session() as s:
        repo_id, file_id = await _seed_file(s)
        chunk_rows = await ChunkRepository(s).replace_for_file(
            repository_id=repo_id,
            file_id=file_id,
            chunks=[
                {
                    "chunk_index": i,
                    "start_line": i * 40 + 1,
                    "end_line": i * 40 + 40,
                    "content": t,
                    "content_sha256": f"h{i}",
                }
                for i, t in enumerate(texts)
            ],
        )
        vectors = fake.embed_documents(texts)
        inserted = await EmbeddingRepository(s).add_many(
            repository_id=repo_id,
            model=fake.model_name,
            dim=_DIM,
            items=list(zip((c.id for c in chunk_rows), vectors, strict=True)),
        )
        assert inserted == 3

        # A query equal to a chunk's text must retrieve that chunk at rank 1,
        # because the fake provider is deterministic.
        qvec = fake.embed_query("def login(): ...")
        hits = await EmbeddingRepository(s).search(
            repository_id=repo_id, query_vector=qvec, top_k=3
        )
    assert len(hits) == 3
    assert hits[0].content == "def login(): ..."
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)
    assert hits[0].path == "a.py"
    assert hits[0].start_line == 1


async def test_search_is_owner_scoped_by_repository(database: Database) -> None:
    fake = FakeEmbeddingProvider(dim=_DIM)
    async with database.session() as s:
        repo_a, file_a = await _seed_file(s)
        repo_b, _ = await _seed_file(s)
        rows = await ChunkRepository(s).replace_for_file(
            repository_id=repo_a,
            file_id=file_a,
            chunks=[
                {
                    "chunk_index": 0,
                    "start_line": 1,
                    "end_line": 5,
                    "content": "secret",
                    "content_sha256": "s",
                }
            ],
        )
        await EmbeddingRepository(s).add_many(
            repository_id=repo_a,
            model=fake.model_name,
            dim=_DIM,
            items=[(rows[0].id, fake.embed_query("secret"))],
        )
        # Searching repo_b must not see repo_a's chunk.
        hits = await EmbeddingRepository(s).search(
            repository_id=repo_b, query_vector=fake.embed_query("secret"), top_k=10
        )
    assert hits == []


async def test_cascade_delete_removes_chunks_and_embeddings(database: Database) -> None:
    fake = FakeEmbeddingProvider(dim=_DIM)
    async with database.session() as s:
        repo_id, file_id = await _seed_file(s)
        rows = await ChunkRepository(s).replace_for_file(
            repository_id=repo_id,
            file_id=file_id,
            chunks=[
                {
                    "chunk_index": 0,
                    "start_line": 1,
                    "end_line": 5,
                    "content": "x",
                    "content_sha256": "h",
                }
            ],
        )
        await EmbeddingRepository(s).add_many(
            repository_id=repo_id,
            model="fake",
            dim=_DIM,
            items=[(rows[0].id, fake.embed_query("x"))],
        )
        # Deleting the repository cascades to chunks and embeddings.
        from devpilot.models.repository import Repository

        repo = await s.get(Repository, repo_id)
        await s.delete(repo)
        await s.flush()
        remaining = await EmbeddingRepository(s).count_for_repository(repo_id)
        chunk_count = await ChunkRepository(s).count_for_repository(repo_id)
    assert remaining == 0
    assert chunk_count == 0
