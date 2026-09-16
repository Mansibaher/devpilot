"""Repository management business logic (API side).

Runs inside request handling: validating a submitted URL, enforcing that a user
only ever touches their own repositories, and enqueuing index jobs idempotently.
It never clones and never touches git -- that is the worker's job. What it does
own is the tenancy boundary: every lookup goes through the owner-scoped
repository methods, so a cross-tenant id returns nothing and the caller answers
404.
"""

import uuid

from devpilot.core.config import Settings
from devpilot.core.errors import ConflictError, NotFoundError
from devpilot.models.index_job import IndexJob
from devpilot.models.repository import Repository
from devpilot.providers.vcs.github import parse_github_url
from devpilot.repositories.event_repo import EventRepository
from devpilot.repositories.job_repo import JobRepository
from devpilot.repositories.repository_repo import RepositoryRepository
from devpilot.services import events


class RepositoryService:
    """Create, list, fetch, and enqueue indexing for repositories."""

    def __init__(
        self,
        repositories: RepositoryRepository,
        jobs: JobRepository,
        event_repo: EventRepository,
        settings: Settings,
    ) -> None:
        """Construct the service.

        Args:
            repositories: Repository data access.
            jobs: Job/queue data access.
            event_repo: Audit-event data access.
            settings: Supplies the job attempt ceiling.

        """
        self._repositories = repositories
        self._jobs = jobs
        self._events = event_repo
        self._settings = settings

    async def create_repository(self, owner_id: uuid.UUID, url: str) -> Repository:
        """Register a repository from a submitted GitHub URL.

        The URL is validated and normalised before any database work. A repeat
        registration by the same user is a conflict; the same URL registered by
        a different user is fine, since uniqueness is owner-scoped.

        Args:
            owner_id: The requesting user.
            url: The submitted repository URL.

        Returns:
            The created repository.

        Raises:
            ValidationError: If the URL is not a valid public GitHub HTTPS URL.
            ConflictError: If this user has already registered it.

        """
        parsed = parse_github_url(url)
        existing = await self._repositories.get_by_slug(
            owner_id, parsed.provider, parsed.owner_name, parsed.repo_name
        )
        if existing is not None:
            raise ConflictError("This repository is already registered.")

        repository = await self._repositories.create(
            owner_id=owner_id,
            provider=parsed.provider,
            owner_name=parsed.owner_name,
            repo_name=parsed.repo_name,
            clone_url=parsed.clone_url,
        )
        await self._events.record(
            repository.id,
            events.EVENT_REPOSITORY_CREATED,
            {"slug": f"{parsed.owner_name}/{parsed.repo_name}"},
        )
        return repository

    async def list_repositories(self, owner_id: uuid.UUID) -> list[Repository]:
        """Return the requesting user's repositories, newest first.

        Args:
            owner_id: The requesting user.

        Returns:
            The user's repositories.

        """
        return await self._repositories.list_for_owner(owner_id)

    async def get_repository(self, owner_id: uuid.UUID, repository_id: uuid.UUID) -> Repository:
        """Return one of the user's repositories.

        Args:
            owner_id: The requesting user.
            repository_id: The repository id.

        Returns:
            The repository.

        Raises:
            NotFoundError: If it does not exist or belongs to another user. The
                two cases are deliberately indistinguishable.

        """
        repository = await self._repositories.get_for_owner(repository_id, owner_id)
        if repository is None:
            raise NotFoundError("Repository not found.")
        return repository

    async def enqueue_index(self, owner_id: uuid.UUID, repository_id: uuid.UUID) -> IndexJob:
        """Enqueue an index job for a repository, idempotently.

        If a queued or running job already exists for the repository, that job
        is returned rather than a new one being created, so a repeated request
        (a double-click, a client retry) attaches to the in-flight work.

        Args:
            owner_id: The requesting user.
            repository_id: The repository to index.

        Returns:
            The active or newly created job.

        Raises:
            NotFoundError: If the repository is not the user's.

        """
        # Ownership is enforced first; a cross-tenant id is simply not found.
        await self.get_repository(owner_id, repository_id)

        active = await self._jobs.get_active_for_repository(repository_id)
        if active is not None:
            return active

        job = await self._jobs.create(repository_id, self._settings.job_max_attempts)
        await self._events.record(repository_id, events.EVENT_INDEX_QUEUED, {"job_id": str(job.id)})
        return job

    async def get_job(
        self, owner_id: uuid.UUID, repository_id: uuid.UUID, job_id: uuid.UUID
    ) -> IndexJob:
        """Return a job, checking both repository ownership and job linkage.

        Args:
            owner_id: The requesting user.
            repository_id: The repository the job must belong to.
            job_id: The job id.

        Returns:
            The job.

        Raises:
            NotFoundError: If the repository is not the user's, or the job does
                not belong to that repository.

        """
        await self.get_repository(owner_id, repository_id)
        job = await self._jobs.get_for_repository(job_id, repository_id)
        if job is None:
            raise NotFoundError("Job not found.")
        return job
