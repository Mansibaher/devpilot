"""Tests that exercise a real PostgreSQL instance.

These assert the two facts the rest of the project rests on: migrations apply
cleanly, and pgvector is actually installed and functional. Both are invisible
to unit tests and both are load-bearing from M4 onward.
"""

import pytest
from sqlalchemy import text

from devpilot.db.session import Database

pytestmark = pytest.mark.integration


async def test_database_check_succeeds_against_live_postgres(database: Database) -> None:
    """The readiness probe's dependency check works against a real server."""
    assert await database.check() is True


async def test_migrations_have_been_applied(database: Database) -> None:
    """`alembic upgrade head` must have run; the version table records the head."""
    async with database.engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        versions = [row[0] for row in result]
    assert versions == ["0001"], "run `make migrate` before the integration suite"


async def test_pgvector_extension_is_installed(database: Database) -> None:
    """The vector type must exist before any embedding column can be declared."""
    async with database.engine.connect() as conn:
        result = await conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        version = result.scalar_one_or_none()
    assert version is not None, "pgvector missing; migration 0001 did not apply"


async def test_vector_distance_operator_is_functional(database: Database) -> None:
    """Prove the extension works, not merely that it is registered."""
    async with database.engine.connect() as conn:
        result = await conn.execute(text("SELECT '[1,0,0]'::vector(3) <=> '[1,0,0]'::vector(3)"))
        distance = result.scalar_one()
    assert distance == pytest.approx(0.0)
