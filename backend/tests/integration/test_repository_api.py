"""End-to-end tests for the repository API against a real database.

Cover the full contract: creation with URL validation, owner-scoped listing and
access (cross-tenant returns 404, not 403), idempotent enqueue returning 202
with the existing job, and job-status retrieval.
"""

import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

_PW = "a perfectly reasonable passphrase"


async def _register_and_token(client: AsyncClient, email: str) -> str:
    await client.post("/api/v1/auth/register", json={"email": email, "password": _PW})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": _PW})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_repo(client: AsyncClient, token: str, url: str):
    return await client.post("/api/v1/repositories", json={"url": url}, headers=_auth(token))


class TestCreate:
    async def test_valid_url_creates_repo(self, api_client: AsyncClient) -> None:
        token = await _register_and_token(api_client, f"c-{uuid.uuid4().hex[:6]}@x.com")
        resp = await _create_repo(api_client, token, "https://github.com/torvalds/linux")
        assert resp.status_code == 201
        body = resp.json()
        assert body["owner_name"] == "torvalds"
        assert body["repo_name"] == "linux"
        assert body["status"] == "pending"

    async def test_invalid_url_is_422(self, api_client: AsyncClient) -> None:
        token = await _register_and_token(api_client, f"c-{uuid.uuid4().hex[:6]}@x.com")
        resp = await _create_repo(api_client, token, "https://evil.com/o/r")
        assert resp.status_code == 422

    async def test_duplicate_for_same_user_is_409(self, api_client: AsyncClient) -> None:
        token = await _register_and_token(api_client, f"c-{uuid.uuid4().hex[:6]}@x.com")
        await _create_repo(api_client, token, "https://github.com/o/dup")
        resp = await _create_repo(api_client, token, "https://github.com/o/dup")
        assert resp.status_code == 409

    async def test_requires_auth(self, api_client: AsyncClient) -> None:
        resp = await api_client.post("/api/v1/repositories", json={"url": "https://github.com/o/r"})
        assert resp.status_code == 401


class TestOwnership:
    async def test_same_repo_two_users_is_allowed(self, api_client: AsyncClient) -> None:
        t1 = await _register_and_token(api_client, f"u1-{uuid.uuid4().hex[:6]}@x.com")
        t2 = await _register_and_token(api_client, f"u2-{uuid.uuid4().hex[:6]}@x.com")
        r1 = await _create_repo(api_client, t1, "https://github.com/shared/repo")
        r2 = await _create_repo(api_client, t2, "https://github.com/shared/repo")
        assert r1.status_code == 201
        assert r2.status_code == 201

    async def test_cross_tenant_access_is_404_not_403(self, api_client: AsyncClient) -> None:
        t1 = await _register_and_token(api_client, f"o1-{uuid.uuid4().hex[:6]}@x.com")
        t2 = await _register_and_token(api_client, f"o2-{uuid.uuid4().hex[:6]}@x.com")
        created = await _create_repo(api_client, t1, "https://github.com/o/private")
        repo_id = created.json()["id"]
        # user 2 tries to read user 1's repository
        resp = await api_client.get(f"/api/v1/repositories/{repo_id}", headers=_auth(t2))
        assert resp.status_code == 404

    async def test_list_is_owner_scoped(self, api_client: AsyncClient) -> None:
        t1 = await _register_and_token(api_client, f"l1-{uuid.uuid4().hex[:6]}@x.com")
        t2 = await _register_and_token(api_client, f"l2-{uuid.uuid4().hex[:6]}@x.com")
        await _create_repo(api_client, t1, "https://github.com/o/onlyt1")
        resp = await api_client.get("/api/v1/repositories", headers=_auth(t2))
        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestIndexing:
    async def test_enqueue_returns_202_with_job(self, api_client: AsyncClient) -> None:
        token = await _register_and_token(api_client, f"i-{uuid.uuid4().hex[:6]}@x.com")
        created = await _create_repo(api_client, token, "https://github.com/o/idx")
        repo_id = created.json()["id"]
        resp = await api_client.post(f"/api/v1/repositories/{repo_id}/index", headers=_auth(token))
        assert resp.status_code == 202
        assert resp.json()["status"] == "queued"

    async def test_enqueue_is_idempotent(self, api_client: AsyncClient) -> None:
        token = await _register_and_token(api_client, f"i-{uuid.uuid4().hex[:6]}@x.com")
        created = await _create_repo(api_client, token, "https://github.com/o/idem")
        repo_id = created.json()["id"]
        first = await api_client.post(f"/api/v1/repositories/{repo_id}/index", headers=_auth(token))
        second = await api_client.post(
            f"/api/v1/repositories/{repo_id}/index", headers=_auth(token)
        )
        assert first.status_code == second.status_code == 202
        # Same job returned both times: no duplicate enqueue.
        assert first.json()["id"] == second.json()["id"]

    async def test_job_status_is_retrievable(self, api_client: AsyncClient) -> None:
        token = await _register_and_token(api_client, f"i-{uuid.uuid4().hex[:6]}@x.com")
        created = await _create_repo(api_client, token, "https://github.com/o/stat")
        repo_id = created.json()["id"]
        job = (
            await api_client.post(f"/api/v1/repositories/{repo_id}/index", headers=_auth(token))
        ).json()
        resp = await api_client.get(
            f"/api/v1/repositories/{repo_id}/jobs/{job['id']}", headers=_auth(token)
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == job["id"]

    async def test_index_unknown_repo_is_404(self, api_client: AsyncClient) -> None:
        token = await _register_and_token(api_client, f"i-{uuid.uuid4().hex[:6]}@x.com")
        resp = await api_client.post(
            f"/api/v1/repositories/{uuid.uuid4()}/index", headers=_auth(token)
        )
        assert resp.status_code == 404
