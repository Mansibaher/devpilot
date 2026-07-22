"""Data access for users.

This is the only module that emits SQL for the users table. Services depend on
it through its methods, never on SQLAlchemy directly, so a future change of
persistence touches this file alone. The repository does not commit: it flushes
so the caller sees generated values and constraint violations, and lets the
session lifecycle owned by ``Database.session`` decide the transaction boundary.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.models.user import User


class UserRepository:
    """CRUD operations for :class:`~devpilot.models.user.User`."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The request-scoped async session. Supplied by dependency
                injection; the repository never creates its own.

        """
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Return the user with this id, or None.

        Args:
            user_id: The user's UUID.

        Returns:
            The matching ``User`` or ``None``.

        """
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Return the user with this email, or None.

        The comparison is case-insensitive because the column is ``citext``;
        the caller is still expected to pass a normalised address.

        Args:
            email: The email to look up.

        Returns:
            The matching ``User`` or ``None``.

        """
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def create(self, *, email: str, password_hash: str) -> User:
        """Insert a new user and return it, populated with generated values.

        Flushes so that the database-assigned ``created_at`` and the
        application-assigned ``id`` are available on the returned instance, and
        so that a duplicate-email violation surfaces here rather than later at
        commit. Does not commit: the surrounding session context owns that.

        Args:
            email: The normalised email address.
            password_hash: The Argon2 hash. A hash, never a plaintext password.

        Returns:
            The persisted ``User``.

        """
        user = User(email=email, password_hash=password_hash)
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user
