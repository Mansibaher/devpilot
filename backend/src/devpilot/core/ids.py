"""Identifier generation.

Every generated identifier in the application passes through a function here,
so the choice of UUID version and the library that produces it live in exactly
one place.

Why UUIDv7 rather than a serial or UUIDv4:

- Non-guessable in URLs, unlike a sequential integer, which leaks row counts
  and lets one id be incremented into another.
- Collision-free across processes and shards, which a serial is not. This
  matters because ``chunks`` is expected to partition by repository later, and
  a partitioned sequence is awkward.
- Time-ordered, unlike UUIDv4. The first bytes are a millisecond timestamp, so
  values generated in sequence sort in creation order and insert into the
  primary-key B-tree sequentially rather than scattering random pages -- the
  index stays dense and cache-friendly as the table grows.

Why ``uuid6.uuid7`` specifically: it returns a native ``uuid.UUID``, so it
drops into SQLAlchemy and asyncpg with no conversion layer, and it is pure
Python, so nothing has to compile in the slim image. Python 3.14 adds
``uuid.uuid7`` to the standard library; when the project moves to it, only this
module changes.
"""

import uuid

from uuid6 import uuid7


def new_user_id() -> uuid.UUID:
    """Return a fresh UUIDv7 for a user row.

    Returns:
        A version-7 UUID: time-ordered, non-guessable, a native
        ``uuid.UUID`` ready for a ``Mapped[uuid.UUID]`` column.

    """
    return uuid7()


def new_id() -> uuid.UUID:
    """Return a fresh UUIDv7 for any row.

    The general-purpose generator for tables added after users. Identical
    behaviour to :func:`new_user_id`; the two names exist only so call sites
    read clearly. Both collapse to ``uuid.uuid7`` when the project reaches
    Python 3.14.

    Returns:
        A version-7 UUID.

    """
    return uuid7()
