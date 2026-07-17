"""FastAPI application factory.

This module wires the application together and does nothing else. There is no
business logic here, no route handlers, and no module-level side effects at
all -- importing this module must never read the environment or open a socket.

That is why no ``app`` global is defined. A module-level ``app = create_app()``
would validate settings at import time, so merely importing this module (as
the test suite and any tooling does) would fail unless the full production
environment were present. It would also make the ``settings`` argument to
``create_app`` a fiction, since the import-time instance would already have
been built from ``os.environ``.

Uvicorn is therefore pointed at the factory itself::

    uvicorn devpilot.main:create_app --factory
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from devpilot.api import health
from devpilot.api.v1 import api_router
from devpilot.core.config import Settings, get_settings
from devpilot.core.errors import install_error_handlers
from devpilot.core.logging import configure_logging, get_logger, request_id_var
from devpilot.db.session import Database

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage resources that outlive a single request.

    The database engine is created once at startup and disposed at shutdown.
    Creating it lazily per request would defeat connection pooling; leaking it
    at shutdown would hold Postgres connections open across a rolling deploy.

    Args:
        app: The application being started.

    Yields:
        None, once startup is complete.

    """
    settings: Settings = app.state.settings
    app.state.database = Database(settings)
    logger.info("application_startup", environment=settings.environment)
    try:
        yield
    finally:
        await app.state.database.dispose()
        logger.info("application_shutdown")


async def _request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Bind a request id for the duration of the request.

    The id is taken from the inbound header when present so that a trace can
    be followed across service boundaries, and generated otherwise. It is
    echoed back so clients can quote it in bug reports.

    Args:
        request: The incoming request.
        call_next: The next handler in the middleware chain.

    Returns:
        The downstream response, with the request id header attached.

    """
    request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        settings: Settings to use. Defaults to the process-wide singleton;
            tests pass an explicit instance.

    Returns:
        A configured, ready-to-serve FastAPI application.

    """
    settings = settings or get_settings()
    configure_logging(
        log_level=settings.log_level,
        json_output=settings.environment != "local",
    )

    app = FastAPI(
        title="DevPilot AI",
        version="0.1.0",
        description="Ask questions about a GitHub repository and get source-grounded answers.",
        lifespan=_lifespan,
        docs_url=None if settings.is_production else "/docs",
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings

    app.middleware("http")(_request_id_middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )

    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(api_router, prefix="/api/v1")
    return app
