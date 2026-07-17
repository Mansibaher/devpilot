"""SQLAlchemy declarative base.

The naming convention is set before any model is declared. Without it,
Postgres invents constraint names and Alembic autogenerate produces migrations
that cannot reliably drop what a previous migration created. Retrofitting a
convention later means renaming every constraint in production, so it is
fixed here at the first commit.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ORM model in the application."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
