"""Shared pytest fixtures.

Tests run on an isolated in-memory SQLite database. The schema is created from
the same ORM metadata the Alembic migration builds, so CHECK/FK/unique/version
constraints under test mirror production (FROZEN_CONTRACT §3.2: "test fixture
must be demonstrably consistent with migration constraints"). Production itself
never calls create_all.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.db.base import Base
from find_yourself.db import models  # noqa: F401
from find_yourself.services.audit import AuditService
from find_yourself.services.actor import Actor


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture()
def session(engine):
    sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = sm()
    # SQLite needs FK enforcement per connection.
    s.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=ON"))
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def audit(session):
    return AuditService(session)


@pytest.fixture()
def owner():
    return Actor.owner("owner-1", csrf_token="")
