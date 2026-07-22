"""Integration tests for the user repository against a real database.

The repository is the only layer that emits SQL, so these confirm the queries
behave against Postgres: UUIDv7 ids are generated and time-ordered, citext
lookups are case-insensitive, and the unique constraint raises rather than
silently inserting a duplicate.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from devpilot.repositories.user_repo import UserRepository

pytestmark = pytest.mark.integration


async def test_create_assigns_a_uuid7_id(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await repo.create(email="repo1@example.com", password_hash="$argon2id$fake")
    assert user.id.version == 7


async def test_ids_are_time_ordered(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    first = await repo.create(email="order1@example.com", password_hash="h")
    second = await repo.create(email="order2@example.com", password_hash="h")
    # UUIDv7 encodes a timestamp prefix, so a later row sorts after an earlier one.
    assert str(first.id) < str(second.id)


async def test_get_by_email_is_case_insensitive(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    await repo.create(email="mixed@example.com", password_hash="h")
    found = await repo.get_by_email("MIXED@EXAMPLE.COM")
    assert found is not None
    assert found.email == "mixed@example.com"


async def test_get_by_id_round_trips(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    created = await repo.create(email="byid@example.com", password_hash="h")
    fetched = await repo.get_by_id(created.id)
    assert fetched is not None and fetched.id == created.id


async def test_duplicate_email_raises_integrity_error(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    await repo.create(email="unique@example.com", password_hash="h")
    with pytest.raises(IntegrityError):
        await repo.create(email="unique@example.com", password_hash="h")
