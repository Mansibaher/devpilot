"""Health and readiness endpoints.

Liveness and readiness are two different questions and conflating them causes
outages.

``GET /health`` asks "is this process alive?" and touches no dependency. An
orchestrator restarts the container when it fails, so it must not fail
because Postgres is briefly unreachable -- that would turn a database blip
into a restart storm. It is also served at ``/healthz`` for orchestrators
that default to that convention.

``GET /readyz`` asks "should this process receive traffic?" and does check
dependencies. Failing it removes the instance from the load balancer without
killing it, which is the correct response to a transient outage.
"""

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from devpilot.api.deps import DatabaseDep, SettingsDep

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    """Response body for the liveness probe."""

    status: str
    environment: str


class ReadinessResponse(BaseModel):
    """Response body for the readiness probe.

    Attributes:
        status: ``"ready"`` when every dependency is usable.
        checks: Per-dependency results, keyed by dependency name.

    """

    status: str
    checks: dict[str, bool]


@router.get(
    "/health",
    response_model=LivenessResponse,
    summary="Liveness probe",
)
@router.get(
    "/healthz",
    response_model=LivenessResponse,
    include_in_schema=False,
)
async def liveness(settings: SettingsDep) -> LivenessResponse:
    """Report that the process is running.

    Served at two paths on purpose, with one handler and no duplicated logic.
    ``/health`` is the documented, canonical route. ``/healthz`` is the
    convention Kubernetes and most load balancers default to, and is kept as
    an undocumented alias so probe configuration never has to be special-cased
    -- and so nothing that already points at it breaks.

    Args:
        settings: Application settings.

    Returns:
        A payload indicating the process is alive.

    """
    return LivenessResponse(status="ok", environment=settings.environment)


@router.get("/readyz", response_model=ReadinessResponse)
async def readiness(database: DatabaseDep, response: Response) -> ReadinessResponse:
    """Report whether the process can serve traffic.

    Args:
        database: The application's database handle.
        response: The outgoing response, mutated to set a 503 when not ready.

    Returns:
        A payload describing each dependency check.

    """
    checks = {"database": await database.check()}
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
