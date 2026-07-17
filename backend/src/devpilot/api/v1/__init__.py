"""Version 1 of the public HTTP API.

Business endpoints live here and are served under ``/api/v1``. Health probes
are deliberately *not* versioned and are mounted at the root instead — an
orchestrator's probe configuration should not have to change when the API
contract does.
"""

from fastapi import APIRouter

api_router = APIRouter()

__all__ = ["api_router"]
