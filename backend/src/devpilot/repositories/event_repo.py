"""Data access for repository events."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.models.repository_event import RepositoryEvent


class EventRepository:
    """Insert and read repository audit events."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The active async session.

        """
        self._session = session

    async def record(
        self,
        repository_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RepositoryEvent:
        """Append an event for a repository.

        Flushes but does not commit: the event shares the transaction of the
        state change it records, so the two are durable together or not at all.

        Args:
            repository_id: The repository the event concerns.
            event_type: A constant from ``services.events``.
            payload: Optional structured context.

        Returns:
            The persisted event.

        """
        event = RepositoryEvent(repository_id=repository_id, event_type=event_type, payload=payload)
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_for_repository(self, repository_id: uuid.UUID) -> list[RepositoryEvent]:
        """Return a repository's events, newest first.

        Args:
            repository_id: The repository to read events for.

        Returns:
            The events, ordered by creation time descending.

        """
        result = await self._session.execute(
            select(RepositoryEvent)
            .where(RepositoryEvent.repository_id == repository_id)
            .order_by(RepositoryEvent.created_at.desc())
        )
        return list(result.scalars().all())
