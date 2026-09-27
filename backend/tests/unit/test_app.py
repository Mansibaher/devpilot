"""Tests for application wiring: request correlation, error mapping, docs exposure."""

import os
import subprocess
import sys

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from devpilot.core.config import Settings
from devpilot.core.errors import ConflictError, NotFoundError
from devpilot.main import create_app


async def test_request_id_is_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers["X-Request-ID"]


async def test_browser_workspace_serves_assets(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "Repository explorer" in response.text
    for path in ("/workspace/app.js", "/workspace/style.css"):
        asset = await client.get(path)
        assert asset.status_code == 200


async def test_inbound_request_id_is_echoed(client: AsyncClient) -> None:
    """Correlation ids must survive across service boundaries."""
    response = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (NotFoundError("repository not found"), 404, "not_found"),
        (ConflictError("repository already imported"), 409, "conflict"),
    ],
)
async def test_domain_errors_map_to_http_status(
    settings: Settings,
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    """Services raise transport-agnostic errors; the API layer translates them."""
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise error

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


def test_importing_main_does_not_read_the_environment() -> None:
    """Importing the app module must not require configuration.

    A module-level ``app = create_app()`` would validate settings at import
    time, breaking every consumer that merely imports the module without a
    populated environment -- and quietly passing in CI, where the environment
    happens to be populated. Run in a subprocess with a scrubbed environment
    so no ambient variable can mask a regression.
    """
    env = {"PATH": os.defpath, "PYTHONPATH": "src"}
    # Windows needs SystemRoot to initialise networking during asyncio imports.
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    result = subprocess.run(
        [sys.executable, "-c", "import devpilot.main; print('ok')"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_docs_are_disabled_in_production() -> None:
    """The schema is an internal detail and is not published in production."""
    app: FastAPI = create_app(
        Settings(
            environment="production",
            jwt_secret="test-secret-not-used-in-production-min-32-chars",
            database_url="postgresql+asyncpg://u:p@db:5432/devpilot",  # type: ignore[arg-type]
        )
    )
    assert app.docs_url is None
    assert app.openapi_url is None
