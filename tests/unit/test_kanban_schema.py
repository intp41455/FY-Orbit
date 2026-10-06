"""任务看板 K1 · 数据模型与迁移 0041 的结构测试（A-任务看板-01～13 底座）。

这些用例只测**结构**，不测行为；行为在 ``tests/unit/test_kanban_board.py``
（服务层）与 ``tests/api/test_kanban_api.py``（HTTP 层）。

本文件最关键的一条是 ``test_fresh_create_all_db_and_migrated_db_agree``：
``0001_initial.py:37`` 用 ``Base.metadata.create_all(bind)`` 建全表，因此**新建库**
上 0041 的表/列护栏全部命中跳过，而**已迁移库**上 0041 真正执行建表。两条路径
必须收敛到同一个 schema，否则「全新安装」与「升级安装」会得到不同的库——这类
漂移在本仓已经发生过至少一次（0026 的 ``ix_memories_owner_tier``），故钉死。
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from find_yourself.db.base import Base
from find_yourself.db.models import Task, TaskDependency, TaskEvent

#: 0041 之前的 ``tasks`` 形状。
#:
#: 🔴 这个 fixture 必须**忠实**：``owner_id`` / ``root_task_id`` 上的
#: ``ix_tasks_*`` 索引在 0041 之前就已经存在（ORM 早就声明了 ``index=True``，
#: 由 0001 的 ``create_all`` 建出）。漏掉它们会让「已迁移库」假装成一张
#: 历史上不存在的库，于是 agree 用例报出的是 fixture 的错，不是迁移的错。
_LEGACY_TASKS_DDL = """
CREATE TABLE tasks (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(200),
    parent_task_id VARCHAR(64),
    root_task_id VARCHAR(64),
    goal TEXT,
    domain VARCHAR(16),
    mode VARCHAR(16),
    strategy VARCHAR(16),
    status VARCHAR(24),
    stage VARCHAR(40),
    depth INTEGER,
    steps INTEGER,
    max_steps INTEGER,
    max_depth INTEGER,
    result JSON,
    failure JSON,
    deadline DATETIME,
    idempotency_key VARCHAR(100),
    created_at DATETIME,
    updated_at DATETIME,
    version INTEGER
);
CREATE INDEX ix_tasks_owner_id ON tasks (owner_id);
CREATE INDEX ix_tasks_root_task_id ON tasks (root_task_id);
"""

NEW_TASK_COLUMNS = {
    "progress_percent", "weight", "critical",
    "blocked_reason", "blocked_since", "planned_start", "planned_end",
}

_DEP_INDEXES = {
    "ix_task_dependencies_owner_id",
    "ix_task_dependencies_depends_on_task_id",
}


def _migration_0041():
    return importlib.import_module("migrations.versions.0041_kanban_board")


def _run(conn, mod) -> None:
    mod.op = Operations(MigrationContext.configure(conn))
    mod.upgrade()


def _create_legacy_tasks(conn) -> None:
    """Build the pre-0041 ``tasks`` table, statement by statement.

    ``Connection.execute(text(...))`` only accepts a single statement, so the
    table and its two indexes have to be issued separately.
    """
    for statement in _LEGACY_TASKS_DDL.split(";"):
        if statement.strip():
            conn.exec_driver_sql(statement)


def _snapshot(conn) -> dict:
    insp = sa.inspect(conn)
    out = {
        "task_cols": sorted(c["name"] for c in insp.get_columns("tasks")),
        "task_idx": sorted(i["name"] for i in insp.get_indexes("tasks")),
    }
    for table in ("task_dependencies", "task_events"):
        if not insp.has_table(table):
            out[table] = None
            continue
        out[table] = {
            "cols": sorted(c["name"] for c in insp.get_columns(table)),
            "idx": sorted(i["name"] for i in insp.get_indexes(table)),
            "fk": sorted(f["name"] for f in insp.get_foreign_keys(table)),
        }
    return out


def _fk_on_engine() -> sa.Engine:
    """Engine with SQLite FK enforcement switched on.

    SQLite ignores ``ON DELETE CASCADE`` unless ``PRAGMA foreign_keys=ON``.
    Production is PostgreSQL (cascades always enforced), so a cascade test that
    silently did nothing would be worse than no test at all.
    """
    eng = sa.create_engine("sqlite://")

    @sa.event.listens_for(eng, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover - driver callback
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return eng


def _add_task(session: Session, task_id: str, *, owner: str = "o1", **kw) -> Task:
    """Insert a Task through the ORM so Python-side defaults are applied.

    Raw INSERTs would have to spell out every NOT NULL column (``depth``,
    ``steps``, ...), which makes the fixture silently rot the next time someone
    adds a column to the frozen Task shape.
    """
    task = Task(
        id=task_id,
        owner_id=owner,
        goal=kw.pop("goal", f"goal of {task_id}"),
        deadline=kw.pop("deadline", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        idempotency_key=kw.pop("idempotency_key", f"idem-{task_id}"),
        **kw,
    )
    session.add(task)
    session.flush()
    return task


# --------------------------------------------------------------------------- #
# 两条建表路径必须收敛
# --------------------------------------------------------------------------- #
def test_fresh_create_all_db_and_migrated_db_agree():
    """全新安装（0001 的 create_all 抢先建好）与升级安装必须得到同一个 schema。"""
    mod = _migration_0041()

    fresh = sa.create_engine("sqlite://")
    with fresh.begin() as conn:
        Base.metadata.create_all(bind=conn)   # what 0001 does
        _run(conn, mod)
        fresh_snap = _snapshot(conn)

    legacy = sa.create_engine("sqlite://")
    with legacy.begin() as conn:
        _create_legacy_tasks(conn)   # pre-0041 shape
        _run(conn, mod)
        legacy_snap = _snapshot(conn)

    assert fresh_snap == legacy_snap
    for snap in (fresh_snap, legacy_snap):
        assert NEW_TASK_COLUMNS <= set(snap["task_cols"])
        assert _DEP_INDEXES <= set(snap["task_dependencies"]["idx"])


def test_orm_metadata_declares_every_index_the_migration_builds():
    """``create_all`` 必须能建出迁移建的每个索引，否则全新安装会静默缺索引。

    索引名按 ``db/base.py`` 的约定 ``ix_%(table_name)s_%(column_0_N_name)s`` 推出；
    迁移里写死了同样的名字，两边对不上就是漂移。
    """
    assert {i.name for i in TaskDependency.__table__.indexes} == _DEP_INDEXES
    assert {i.name for i in TaskEvent.__table__.indexes} == {
        "ix_task_events_task_id",
        "ix_task_events_owner_id",
        "ix_task_events_kind",
        "ix_task_events_created_at",
    }


def test_migration_0041_index_names_match_the_naming_convention():
    """迁移里写死的索引名必须与 ORM 约定逐字一致（防同一列上留下重复索引）。"""
    mod = _migration_0041()
    expected = {i.name for i in TaskDependency.__table__.indexes}
    expected |= {i.name for i in TaskEvent.__table__.indexes}
    assert {name for name, _table, _cols in mod._INDEXES} == expected


def test_migration_0041_column_nullability_matches_the_orm():
    """迁移加的列，nullable / server_default 必须与 ORM 声明一致。

    ORM 里 ``weight: Mapped[int]`` 是 NOT NULL，``progress_percent:
    Mapped[int | None]`` 可空。两边不一致 → 两条建表路径漂移。
    """
    mod = _migration_0041()
    by_name = {name: (nullable, default) for name, _t, nullable, default in mod._TASK_COLUMNS}
    assert by_name["progress_percent"] == (True, None)
    assert by_name["weight"] == (False, "1")
    assert by_name["critical"] == (False, "0")
    for name in ("blocked_reason", "blocked_since", "planned_start", "planned_end"):
        assert by_name[name] == (True, None)

    cols = {c.name: c for c in Task.__table__.columns}
    assert cols["progress_percent"].nullable is True
    assert cols["weight"].nullable is False
    assert cols["critical"].nullable is False


# --------------------------------------------------------------------------- #
# 幂等 / 无副作用
# --------------------------------------------------------------------------- #
def test_migration_0041_is_idempotent():
    """重跑不得报错，也不得改动 schema（护栏真的在护栏）。"""
    mod = _migration_0041()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _create_legacy_tasks(conn)
        _run(conn, mod)
        first = _snapshot(conn)
        _run(conn, mod)
        _run(conn, mod)
        assert _snapshot(conn) == first


def test_migration_0041_is_a_noop_without_the_tasks_table():
    """库里没有 tasks 时不得报错（与 0026/0010 的「缺基表即 no-op」规则一致）。"""
    mod = _migration_0041()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _run(conn, mod)  # must not raise
        assert not sa.inspect(conn).has_table("tasks")


def test_migration_0041_downgrade_is_complete_and_replayable():
    """downgrade 必须把 0041 的产物全部撤掉，且能重放。"""
    mod = _migration_0041()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _create_legacy_tasks(conn)
        _run(conn, mod)
        before = _snapshot(conn)

        mod.op = Operations(MigrationContext.configure(conn))
        mod.downgrade()

        insp = sa.inspect(conn)
        assert not insp.has_table("task_dependencies")
        assert not insp.has_table("task_events")
        assert not (NEW_TASK_COLUMNS & {c["name"] for c in insp.get_columns("tasks")})

        _run(conn, mod)
        assert _snapshot(conn) == before


def test_migration_0041_downgrade_does_not_drop_task_rows():
    """降级只撤看板能力，不得连带删掉用户任务。"""
    mod = _migration_0041()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _create_legacy_tasks(conn)
        _run(conn, mod)
        conn.execute(sa.text(
            "INSERT INTO tasks (id,owner_id,goal,domain,mode,strategy,status,stage,"
            "depth,steps,max_steps,max_depth,deadline,idempotency_key,version) VALUES "
            "('keepme','o1','a real goal','personal','listen','auto','queued',"
            "'requirements',0,0,8,2,'2026-01-01','k1',1)"
        ))
        mod.op = Operations(MigrationContext.configure(conn))
        mod.downgrade()
        assert conn.execute(sa.text("SELECT id, goal FROM tasks")).fetchall() == [
            ("keepme", "a real goal")
        ]


# --------------------------------------------------------------------------- #
# 约束必须真的咬人（不是只在 create_all 路径上）
# --------------------------------------------------------------------------- #
def test_self_dependency_is_rejected_by_the_database():
    """自依赖是环的最短形式，DB 层就该拦住。"""
    eng = _fk_on_engine()
    with Session(eng) as session:
        Base.metadata.create_all(bind=session.get_bind())
        _add_task(session, "t1")
        session.add(TaskDependency(task_id="t1", depends_on_task_id="t1", owner_id="o1"))
        with pytest.raises(sa.exc.IntegrityError):
            session.flush()
        session.rollback()


def test_dependency_primary_key_rejects_duplicates():
    """同一条依赖重复插入必须被主键挡住（否则依赖边会被重复计数）。"""
    eng = _fk_on_engine()
    with Session(eng) as session:
        Base.metadata.create_all(bind=session.get_bind())
        _add_task(session, "t1")
        _add_task(session, "t2")
        session.add(TaskDependency(task_id="t2", depends_on_task_id="t1", owner_id="o1"))
        session.flush()
        session.add(TaskDependency(task_id="t2", depends_on_task_id="t1", owner_id="o1"))
        with pytest.raises(sa.exc.IntegrityError):
            session.flush()
        session.rollback()


def test_task_event_kind_is_constrained_on_a_migrated_database():
    """事件类别必须在「已迁移库」上也被 CHECK 咬住，而不只是 create_all 库。"""
    mod = _migration_0041()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _create_legacy_tasks(conn)
        _run(conn, mod)
        conn.execute(sa.text(
            "INSERT INTO tasks (id,owner_id,goal,domain,mode,strategy,status,stage,"
            "depth,steps,max_steps,max_depth,deadline,idempotency_key,version) VALUES "
            "('t1','o1','g','personal','listen','auto','queued','requirements',"
            "0,0,8,2,'2026-01-01','k1',1)"
        ))
        conn.execute(sa.text(
            "INSERT INTO task_events (id,task_id,owner_id,kind,created_at) "
            "VALUES ('e1','t1','o1','status','2026-01-01')"
        ))
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text(
                "INSERT INTO task_events (id,task_id,owner_id,kind,created_at) "
                "VALUES ('e2','t1','o1','not_a_kind','2026-01-01')"
            ))


def test_task_event_cascade_deletes_with_the_task():
    """任务删掉，历史必须一起走（append-only 但不跨任务泄漏）。"""
    eng = _fk_on_engine()
    with Session(eng) as session:
        Base.metadata.create_all(bind=session.get_bind())
        _add_task(session, "t1")
        session.add(TaskEvent(id="e1", task_id="t1", owner_id="o1", kind="created"))
        session.flush()
        session.execute(sa.text("DELETE FROM tasks WHERE id='t1'"))
        left = session.execute(sa.text("SELECT COUNT(*) FROM task_events")).scalar_one()
        assert left == 0
        session.rollback()


# --------------------------------------------------------------------------- #
# 既有行升级后的默认值必须诚实
# --------------------------------------------------------------------------- #
def test_new_task_columns_default_sensibly_for_legacy_rows():
    """既有行升级后必须拿到**诚实**的默认值。

    ``progress_percent`` 必须是 NULL（= 未开始 / 未知）。填 0 等于替用户断言
    「已完成 0%」——对一个看板功能上线前就存在的任务，本产品并不知道它做到了
    哪一步，编一个 0 就是撒谎（诚实原则）。
    ``weight`` / ``critical`` 则必须有值：加权进度遇到 NULL 权重会直接算错，
    非关键事项也不该是 NULL。
    """
    mod = _migration_0041()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _create_legacy_tasks(conn)
        conn.execute(sa.text(
            "INSERT INTO tasks (id,owner_id,goal,domain,mode,strategy,status,stage,"
            "depth,steps,max_steps,max_depth,deadline,idempotency_key,version) VALUES "
            "('old1','o1','legacy row','personal','listen','auto','queued','requirements',"
            "0,0,8,2,'2026-01-01','k1',1)"
        ))
        _run(conn, mod)
        row = conn.execute(sa.text(
            "SELECT progress_percent, weight, critical, blocked_reason, blocked_since, "
            "planned_start, planned_end FROM tasks WHERE id='old1'"
        )).fetchone()
        assert row[0] is None       # 未开始 = NULL，语义正确（不是编出来的 0）
        assert row[1] == 1          # 权重必须有值，否则加权进度算不出来
        assert row[2] in (0, False)  # 非关键事项
        assert row[3] is None and row[4] is None   # 未阻塞
        assert row[5] is None and row[6] is None   # 未排期（甘特据此显示「未排期」）