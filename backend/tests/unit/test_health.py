"""Tests for the health and readiness probes."""

import pytest
from httpx import AsyncClient
from tests.conftest import StubDatabase


@pytest.mark.parametrize("path", ["/health", "/healthz"])
async def test_liveness_reports_ok(client: AsyncClient, path: str) -> None:
    """Both the canonical route and the orchestrator alias answer identically."""
    response = await client.get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "ci"}


@pytest.mark.parametrize("path", ["/health", "/healthz"])
async def test_liveness_does_not_depend_on_the_database(
    client: AsyncClient, stub_database: StubDatabase, path: str
) -> None:
    """A database outage must not fail liveness and trigger a restart storm."""
    stub_database.healthy = False
    response = await client.get(path)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_health_is_the_documented_route(client: AsyncClient) -> None:
    """/health is published in the schema; /healthz is a compatibility alias."""
    schema = (await client.get("/openapi.json")).json()
    assert "/health" in schema["paths"]
    assert "/healthz" not in schema["paths"]


async def test_readiness_is_ready_when_database_is_reachable(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": True}}


async def test_readiness_returns_503_when_database_is_unreachable(
    client: AsyncClient, stub_database: StubDatabase
) -> None:
    """Failing readiness sheds traffic without killing the process."""
    stub_database.healthy = False
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": False}}
