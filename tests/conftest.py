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
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401
import find_yourself.db.resilience_models  # noqa: F401  (T6 抗中断台账+流式落盘)
import find_yourself.db.claw_models  # noqa: F401  (Claw 治理域：把关/冲突/事实基线)
import find_yourself.db.fork_models  # noqa: F401  (P4 存档分叉 archive_forks)
import find_yourself.db.review_models  # noqa: F401  (P9 点哪评哪评审意见)
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
