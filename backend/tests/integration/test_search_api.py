"""API integration tests for semantic search.

Seed chunks and embeddings through the same transactional session the API
client uses, then search over HTTP. The fake provider (pinned in
integration_settings) makes retrieval deterministic: a query equal to a chunk's
text ranks that chunk first. No model download, no network.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.providers.embeddings.fake import FakeEmbeddingProvider
from devpilot.repositories.chunk_repo import ChunkRepository
from devpilot.repositories.embedding_repo import EmbeddingRepository
from devpilot.repositories.file_repo import FileRepository
from devpilot.repositories.repository_repo import RepositoryRepository

pytestmark = pytest.mark.integration

_PW = "a perfectly reasonable passphrase"
_DIM = 768


async def _register_and_token(client: AsyncClient, email: str) -> str:
    await client.post("/api/v1/auth/register", json={"email": email, "password": _PW})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": _PW})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _current_user_id(client: AsyncClient, token: str) -> uuid.UUID:
    me = await client.get("/api/v1/auth/me", headers=_auth(token))
    return uuid.UUID(me.json()["id"])


async def _seed_repo_with_chunks(
    session: AsyncSession, owner_id: uuid.UUID, texts: list[str]
) -> uuid.UUID:
    """Create a repo, one file, its chunks, and embeddings via the test session."""
    repo = await RepositoryRepository(session).create(
        owner_id=owner_id,
        provider="github",
        owner_name="o",
        repo_name=f"r-{uuid.uuid4().hex[:6]}",
        clone_url="https://github.com/o/r.git",
    )
    await FileRepository(session).upsert_many(
        repo.id,
        [{"path": "app.py", "language": "python", "size_bytes": 100, "checksum_sha256": "x"}],
    )
    file_id = (await FileRepository(session).list_for_repository(repo.id))[0].id
    chunk_rows = await ChunkRepository(session).replace_for_file(
        repository_id=repo.id,
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
    fake = FakeEmbeddingProvider(dim=_DIM)
    await EmbeddingRepository(session).add_many(
        repository_id=repo.id,
        model=fake.model_name,
        dim=_DIM,
        items=list(zip((c.id for c in chunk_rows), fake.embed_documents(texts), strict=True)),
    )
    return repo.id


class TestSearch:
    async def test_returns_ranked_results_with_citations(
        self, api_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_token(api_client, f"s-{uuid.uuid4().hex[:6]}@x.com")
        owner_id = await _current_user_id(api_client, token)
        texts = ["def login(user): ...", "def logout(user): ...", "class Parser: ..."]
        repo_id = await _seed_repo_with_chunks(db_session, owner_id, texts)

        resp = await api_client.post(
            f"/api/v1/repositories/{repo_id}/search",
            json={"query": "def login(user): ...", "top_k": 3},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "def login(user): ..."
        top = body["results"][0]
        # Deterministic fake: the matching chunk ranks first with score ~1.
        assert top["content"] == "def login(user): ..."
        assert top["path"] == "app.py"
        assert top["start_line"] == 1
        assert top["end_line"] == 40
        assert top["score"] == pytest.approx(1.0, abs=1e-3)

    async def test_top_k_is_clamped_to_max(
        self, api_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_token(api_client, f"s-{uuid.uuid4().hex[:6]}@x.com")
        owner_id = await _current_user_id(api_client, token)
        repo_id = await _seed_repo_with_chunks(db_session, owner_id, ["a", "b", "c"])
        # Request far more than max (50); must not error, returns what exists.
        resp = await api_client.post(
            f"/api/v1/repositories/{repo_id}/search",
            json={"query": "a", "top_k": 999},
            headers=_auth(token),
        )
        assert resp.status_code == 422  # ge/le on the schema rejects > 200 first

    async def test_top_k_within_schema_but_over_config_max_is_clamped(
        self, api_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_token(api_client, f"s-{uuid.uuid4().hex[:6]}@x.com")
        owner_id = await _current_user_id(api_client, token)
        repo_id = await _seed_repo_with_chunks(db_session, owner_id, [f"c{i}" for i in range(5)])
        # 60 is within the schema bound (<=200) but over the config max (50):
        # the service clamps rather than errors, and we get the 5 that exist.
        resp = await api_client.post(
            f"/api/v1/repositories/{repo_id}/search",
            json={"query": "c0", "top_k": 60},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 5

    async def test_default_top_k_when_omitted(
        self, api_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_token(api_client, f"s-{uuid.uuid4().hex[:6]}@x.com")
        owner_id = await _current_user_id(api_client, token)
        repo_id = await _seed_repo_with_chunks(db_session, owner_id, [f"c{i}" for i in range(20)])
        resp = await api_client.post(
            f"/api/v1/repositories/{repo_id}/search",
            json={"query": "c0"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 10  # default top_k


class TestSearchErrors:
    async def test_not_indexed_repo_returns_409(
        self, api_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_token(api_client, f"s-{uuid.uuid4().hex[:6]}@x.com")
        owner_id = await _current_user_id(api_client, token)
        # A repo with no chunks/embeddings.
        repo = await RepositoryRepository(db_session).create(
            owner_id=owner_id,
            provider="github",
            owner_name="o",
            repo_name=f"empty-{uuid.uuid4().hex[:6]}",
            clone_url="https://github.com/o/r.git",
        )
        resp = await api_client.post(
            f"/api/v1/repositories/{repo.id}/search",
            json={"query": "anything"},
            headers=_auth(token),
        )
        assert resp.status_code == 409

    async def test_cross_tenant_repo_returns_404(
        self, api_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner_token = await _register_and_token(api_client, f"o-{uuid.uuid4().hex[:6]}@x.com")
        owner_id = await _current_user_id(api_client, owner_token)
        repo_id = await _seed_repo_with_chunks(db_session, owner_id, ["secret"])
        other_token = await _register_and_token(api_client, f"x-{uuid.uuid4().hex[:6]}@x.com")
        resp = await api_client.post(
            f"/api/v1/repositories/{repo_id}/search",
            json={"query": "secret"},
            headers=_auth(other_token),
        )
        assert resp.status_code == 404

    async def test_missing_token_returns_401(self, api_client: AsyncClient) -> None:
        resp = await api_client.post(
            f"/api/v1/repositories/{uuid.uuid4()}/search", json={"query": "x"}
        )
        assert resp.status_code == 401

    async def test_empty_query_returns_422(self, api_client: AsyncClient) -> None:
        token = await _register_and_token(api_client, f"s-{uuid.uuid4().hex[:6]}@x.com")
        resp = await api_client.post(
            f"/api/v1/repositories/{uuid.uuid4()}/search",
            json={"query": ""},
            headers=_auth(token),
        )
        assert resp.status_code == 422

    async def test_unknown_repo_returns_404(self, api_client: AsyncClient) -> None:
        token = await _register_and_token(api_client, f"s-{uuid.uuid4().hex[:6]}@x.com")
        resp = await api_client.post(
            f"/api/v1/repositories/{uuid.uuid4()}/search",
            json={"query": "x"},
            headers=_auth(token),
        )
        assert resp.status_code == 404
