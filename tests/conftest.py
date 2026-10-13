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

import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.claim_models  # noqa: F401  (P5 共享任务板 task_claims)
import find_yourself.db.claw_models  # noqa: F401  (Claw 治理域：把关/冲突/事实基线)
import find_yourself.db.fork_models  # noqa: F401  (P4 存档分叉 archive_forks)
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.resilience_models  # noqa: F401  (T6 抗中断台账+流式落盘)
import find_yourself.db.review_models  # noqa: F401  (P9 点哪评哪评审意见)
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
from find_yourself.db import models  # noqa: F401
from find_yourself.db.base import Base
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService


@pytest.fixture(autouse=True)
def _hermetic_env_file(monkeypatch):
    """测试进程**不得**读取仓库根 ``.env``。

    实测（2026-10-14，HEAD 5d800ee）：开发者机器配了真实模型 Key 的 ``.env``
    会让 26 条单元测试翻红（离线门默认 True / 未配置诚实 503 / provider 探测
    类断言），而 CI（无 ``.env``）全绿 —— 即"测试绿不绿取决于开发者机器，
    不取决于代码"。pydantic-settings 在**实例化时**从
    ``cls.model_config['env_file']`` 读文件（pydantic_settings/main.py:332），
    因此把该键指向空值即可切断，**零产品代码改动**；``_get_settings`` 是
    lru_cache，同一会话里先前实例会被缓存，故一并清缓存。

    测试若确需某配置，请用 ``monkeypatch.setenv("FY_...", ...)``（进程环境
    变量优先级高于 env_file，仍生效）。
    """
    from find_yourself.config import Settings, _get_settings

    cfg = dict(Settings.model_config)
    cfg["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", cfg, raising=True)
    _get_settings.cache_clear()
    yield
    _get_settings.cache_clear()


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
    # 必须 dispose：sqlite 连接不显式关闭会在 GC 时抛
    # `ResourceWarning: unclosed database`，全量回归里刷了 59 条。
    # 实测（tests/conftest.py + tests/api/conftest.py + test_kanban_board.py
    # 三处一起补）告警 70 -> 0。少补任何一处都会留下残余。
    eng.dispose()


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
