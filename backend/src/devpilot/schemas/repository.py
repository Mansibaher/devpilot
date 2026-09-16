"""Request and response schemas for repositories and index jobs."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RepositoryCreate(BaseModel):
    """Request to register a repository.

    Only the URL is accepted; owner, name, and provider are derived from it by
    the validator, so a client cannot assert ownership of a mismatched slug.
    """

    url: str = Field(min_length=1, max_length=2048)


class RepositoryRead(BaseModel):
    """Public representation of a repository."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    owner_name: str
    repo_name: str
    clone_url: str
    default_branch: str | None
    last_indexed_sha: str | None
    status: str
    created_at: datetime


class RepositoryList(BaseModel):
    """A page of repositories."""

    items: list[RepositoryRead]


class JobRead(BaseModel):
    """Public representation of an index job.

    Surfaces progress and the last error, but not the lease internals
    (``locked_by``, ``heartbeat_at``): those are queue mechanics, not something
    a client needs or should depend on.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    status: str
    commit_sha: str | None
    attempts: int
    max_attempts: int
    files_total: int | None
    files_done: int | None
    current_file: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
