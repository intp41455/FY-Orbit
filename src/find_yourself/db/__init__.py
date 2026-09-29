"""Core persistence layer: SQLAlchemy ORM models, engine/session and repositories.

Production path uses Alembic migrations only; ``create_all`` is reserved for
isolated unit-test fixtures that mirror the migration constraints (see
``find_yourself.db.fixtures``). Never call ``create_all`` against a production
database.
"""

from .base import Base
from .session import engine_from_url, session_factory

__all__ = ["Base", "engine_from_url", "session_factory"]
