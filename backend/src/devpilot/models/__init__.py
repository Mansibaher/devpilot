"""SQLAlchemy ORM models.

Importing this package imports every model, which is what registers each table
on ``Base.metadata``. Alembic's autogenerate and the test schema creation both
depend on that side effect: a model that is never imported is invisible to
both, and its table silently goes missing from migrations.
"""

from devpilot.models.user import User

__all__ = ["User"]
