"""Version 1 of the public HTTP API.

Business endpoints live here and are served under ``/api/v1``. Health probes
are deliberately *not* versioned and are mounted at the root instead — an
orchestrator's probe configuration should not have to change when the API
contract does.
"""

from fastapi import APIRouter

from devpilot.api.v1.auth import router as auth_router

api_router = APIRouter()
api_router.include_router(auth_router)

__all__ = ["api_router"]
