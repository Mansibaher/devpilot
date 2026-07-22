"""User ORM model.

This is the account table and the owner every future user-scoped row will
reference. Its identifier is a UUIDv7 (see ``core.ids``), generated in Python
so the value exists before the row is flushed.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from devpilot.core.ids import new_user_id
from devpilot.db.base import Base


class User(Base):
    """A registered account.

    Attributes:
        id: UUIDv7 primary key, generated application-side by ``new_user_id``.
        email: Case-insensitive address, stored as ``citext`` so uniqueness is
            enforced without case folding at the query site. The application
            still normalises before insert; the type is the backstop.
        password_hash: Argon2 encoded hash. Never a plaintext password, and
            never exposed by any response schema.
        is_active: Deactivation seam. Always true today; nothing sets it false
            until a later milestone adds account suspension.
        created_at: Row creation time, set by the database.
        updated_at: Last modification time, refreshed by the database on update.

    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_user_id,
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        """Return a debug representation that never includes the password hash."""
        return f"User(id={self.id!r}, email={self.email!r}, is_active={self.is_active!r})"
