"""Repository and indexing routes.

Thin handlers over ``RepositoryService``. Each validates input, calls one
service method, and shapes the result; ownership, idempotency, and queue
mechanics all live in the service and its repositories. The owner id always
comes from the authenticated token, never from the request body or path, so a
client cannot act on another user's behalf.
"""

import uuid

from fastapi import APIRouter, status

from devpilot.api.deps import CurrentUserDep, RepositoryServiceDep
from devpilot.schemas.repository import (
    JobRead,
    RepositoryCreate,
    RepositoryList,
    RepositoryRead,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.post(
    "",
    response_model=RepositoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a GitHub repository",
)
async def create_repository(
    payload: RepositoryCreate,
    current_user: CurrentUserDep,
    service: RepositoryServiceDep,
) -> RepositoryRead:
    """Register a repository for the authenticated user.

    Args:
        payload: The submitted URL.
        current_user: The authenticated user.
        service: The repository service.

    Returns:
        The created repository.

    """
    repository = await service.create_repository(current_user.id, payload.url)
    return RepositoryRead.model_validate(repository)


@router.get("", response_model=RepositoryList, summary="List your repositories")
async def list_repositories(
    current_user: CurrentUserDep, service: RepositoryServiceDep
) -> RepositoryList:
    """Return the authenticated user's repositories.

    Args:
        current_user: The authenticated user.
        service: The repository service.

    Returns:
        The user's repositories.

    """
    repositories = await service.list_repositories(current_user.id)
    return RepositoryList(items=[RepositoryRead.model_validate(r) for r in repositories])


@router.get(
    "/{repository_id}",
    response_model=RepositoryRead,
    summary="Fetch one of your repositories",
)
async def get_repository(
    repository_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: RepositoryServiceDep,
) -> RepositoryRead:
    """Return a single repository owned by the authenticated user.

    Returns 404 both when the repository does not exist and when it belongs to
    another user, so repository ids cannot be probed.

    Args:
        repository_id: The repository id.
        current_user: The authenticated user.
        service: The repository service.

    Returns:
        The repository.

    """
    repository = await service.get_repository(current_user.id, repository_id)
    return RepositoryRead.model_validate(repository)


@router.post(
    "/{repository_id}/index",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an indexing job",
)
async def index_repository(
    repository_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: RepositoryServiceDep,
) -> JobRead:
    """Enqueue indexing for a repository, idempotently.

    Returns 202 with the job. If a job is already queued or running for the
    repository, that existing job is returned rather than a new one created, so
    repeated calls are safe.

    Args:
        repository_id: The repository to index.
        current_user: The authenticated user.
        service: The repository service.

    Returns:
        The active or newly created job.

    """
    job = await service.enqueue_index(current_user.id, repository_id)
    return JobRead.model_validate(job)


@router.get(
    "/{repository_id}/jobs/{job_id}",
    response_model=JobRead,
    summary="Check an indexing job's status",
)
async def get_job(
    repository_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: RepositoryServiceDep,
) -> JobRead:
    """Return the status of an indexing job.

    Args:
        repository_id: The repository the job belongs to.
        job_id: The job id.
        current_user: The authenticated user.
        service: The repository service.

    Returns:
        The job, including progress and any error.

    """
    job = await service.get_job(current_user.id, repository_id, job_id)
    return JobRead.model_validate(job)
