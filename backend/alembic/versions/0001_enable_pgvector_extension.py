"""enable pgvector extension

The extension is enabled by a migration rather than by an init script baked
into the Postgres image. An init script only runs on first boot of a fresh
volume, so it would cover local development and silently skip CI, staging, and
any managed Postgres (RDS, Cloud SQL) that the team does not build an image
for. Schema state belongs in version control, and this is schema state: the
``vector`` type must exist before any migration can declare a ``vector(384)``
column, so it is the root revision and everything else depends on it.

``IF NOT EXISTS`` keeps the migration idempotent against a database where a
DBA has already installed the extension by hand -- common on managed
Postgres, where installing extensions may be a privileged operation performed
out of band.

Revision ID: 0001
Revises:
Create Date: 2026-07-17

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install the pgvector extension."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    """Remove the pgvector extension.

    Deliberately not ``CASCADE``: if a later migration left a ``vector``
    column behind, this must fail loudly rather than silently drop the
    column and its data.
    """
    op.execute("DROP EXTENSION IF EXISTS vector")
