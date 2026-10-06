"""Alembic migration 0041: 任务看板数据层（A-任务看板-01～13 后端底座）。

三件事：

1. ``tasks`` 扩列（看板所需的最小集，全部 nullable / 有默认值，不动既有行）：

   * ``progress_percent`` —— 本行**自身**的进度 0-100，NULL = 未开始。
     父任务的加权进度**不存冗余**，由服务层按子任务 ``weight`` 现算（需求 03
     明确要求「按子任务权重计算，非简单平均」，冗余缓存必然与子任务漂移）。
   * ``weight`` —— 加权权重，默认 1（需求 03 的权重字段定义）。
   * ``critical`` —— 关键事项标记（需求 02）。
   * ``blocked_reason`` / ``blocked_since`` —— 需求 11 要求红带带「阻塞原因 +
     阻塞时长」；时长必须有可信起点，故落两列而不是每次去翻事件表。
   * ``planned_start`` / ``planned_end`` —— 需求 13 甘特是**规划视图**，按起止
     时间排条。两列均可空，为空时前端诚实显示「未排期」，**不编造日期**。

2. ``task_dependencies`` —— 任务级依赖（A-任务看板-05「A 完成 B 才能开始」）。
   选型说明：既有 ``Task.parent_task_id`` 是**父子包含树**（一个父 many 子），
   与「A 完成才能开 B」的**前置约束图**语义不同（可以多前置、可跨树、成环检测
   对象也不同），因此**不复用** parent_task_id，另立关联表。
   ``(task_id, depends_on_task_id)`` 为主键，天然去重；两侧都带 owner_id 便于
   owner 隔离查询；环检测在服务层做（``services/kanban.py``），因为「成环」是
   跨行图性质，DB CHECK 表达不了。

3. ``task_events`` —— 任务状态/进度变更流水（A-任务看板-10「历史记录」）。
   append-only，只增不改；``from_status``/``to_status`` 可空（非状态类事件）。

🔴 与 ``0001_initial`` 的关键交互（这是本仓最容易踩的坑，0026/0034/0037 都有
同样的坑注释）：``0001_initial.py:37`` 用 ``Base.metadata.create_all(bind)`` 建
全表。因此**新建库**上，本迁移新增的表与列在 0001 阶段就已经由 ``create_all`` 建好，
下面所有 ``has_table`` / 缺列护栏都会正确跳过；**只有已迁移过的库**（0001 早就跑过、
当时 ORM 里还没有这些表）才会真正执行建表。

由此产生两条硬要求，本迁移都遵守：

* **每个索引独立护栏**，不能挂在建表分支里。否则新建库上 ``create_all`` 抢先建过表
  → 整个分支被跳过 → 索引永久缺失（这正是 0026 里 ``ix_memories_owner_tier`` 的注释
  所描述的同款事故）。所以用 ``_has_index`` 逐个判。
* **downgrade 同样逐个判存在性**。新建库上 ``create_all`` 建出的索引集合与迁移建出
  的可能不同，无条件 ``drop_index`` 会直接报错中断 downgrade。

ORM 侧（``db/models.py``）已给 ``TaskDependency.owner_id`` 与
``TaskDependency.depends_on_task_id`` 都声明了 ``index=True``，使 ``create_all``
建出的索引名与本迁移建出的**逐字一致**（约定 ``ix_%(table_name)s_%(column_0_N_name)s``），
两条建表路径不漂移——``tests/unit/test_kanban_board.py`` 对此有断言。

编号说明：链头是 ``0040_resilience_interruptions_streams``，本迁移顺延 0041，
不碰任何他人迁移。

Revision ID: 0041_kanban_board
Revises: 0040_resilience_interruptions_streams
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_kanban_board"
down_revision: str | None = "0040_resilience_interruptions_streams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASKS = "tasks"
DEPS = "task_dependencies"
EVENTS = "task_events"

#: 本迁移往 ``tasks`` 上加的列。downgrade 按同一清单逆序删，避免两处漂移。
#: 🔴 ``nullable`` 与 ``server_default`` **必须**与 ``db/models.py`` 的
#: ``Mapped[...]`` 声明逐字一致：新建库上列是 ``0001`` 的 ``create_all`` 建的
#: （按 ORM 注解决定 NOT NULL），已迁移库上列是本迁移加的。两边 nullable 或
#: default 不一致 → 两条路径 schema 漂移 → ``test_fresh_create_all_db_and_migrated_db_agree``
#: 会红。
#:
#: ``progress_percent`` 刻意**不给** server_default：既有行升级后必须是 NULL
#: （= 未开始 / 未知）。填 0 等于替用户断言「已完成 0%」，那是本产品不知道的事
#: （诚实原则）。``weight`` / ``critical`` 则必须有默认值——加权进度遇到 NULL
#: 权重会直接算错，非关键事项也不该是 NULL。
_TASK_COLUMNS: tuple[tuple[str, str, bool, str | None], ...] = (
    # (列名, 类型键, nullable, server_default 字面量)
    ("progress_percent", "Integer", True, None),
    ("weight", "Integer", False, "1"),
    ("critical", "Boolean", False, "0"),
    ("blocked_reason", "String(500)", True, None),
    ("blocked_since", "DateTime(timezone=True)", True, None),
    ("planned_start", "DateTime(timezone=True)", True, None),
    ("planned_end", "DateTime(timezone=True)", True, None),
)

#: (索引名, 表, 列)。``create_all`` 建不出来的那些也列在这里，由 _has_index 兜住。
#: 🔴 索引名必须与 ``db/base.py`` 的命名约定 ``ix_%(table_name)s_%(column_0_N_name)s``
#: 逐字一致，否则新建库上 ``create_all`` 会按约定建出 ``..._depends_on_task_id``，
#: 本迁移再建一个 ``..._depends_on``，同一列上留下两个重复索引。
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_task_dependencies_owner_id", DEPS, ("owner_id",)),
    ("ix_task_dependencies_depends_on_task_id", DEPS, ("depends_on_task_id",)),
    ("ix_task_events_task_id", EVENTS, ("task_id",)),
    ("ix_task_events_owner_id", EVENTS, ("owner_id",)),
    ("ix_task_events_kind", EVENTS, ("kind",)),
    # ORM 侧 ``TaskEvent.created_at`` 声明了 index=True，约定推出此名；迁移路径
    # 必须同样建出来，否则「降级后再升级」的库会比「全新库」少一个索引。
    ("ix_task_events_created_at", EVENTS, ("created_at",)),
)


def _has_index(bind, table: str, index: str) -> bool:
    """Index existence probe.

    Kept in the same shape as 0026's helper: on a fresh database 0001's
    ``create_all`` may already have produced the index, so every index is probed
    on its own rather than as part of a create-table branch.
    """
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(i["name"] == index for i in insp.get_indexes(table))


def _col_type(type_str: str) -> sa.types.TypeEngine:
    """Build a column type from a portable type key.

    SQLite has no native BOOLEAN, so both dialects go through the generic types
    here — that keeps SQLite (tests) and PG (production) running the *same* DDL.
    """
    return {
        "Integer": sa.Integer(),
        "Boolean": sa.Boolean(),
        "String(500)": sa.String(length=500),
        "DateTime(timezone=True)": sa.DateTime(timezone=True),
    }[type_str]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ---- 1. tasks 扩列（幂等：已存在的列跳过） ----
    if insp.has_table(TASKS):
        existing = {c["name"] for c in insp.get_columns(TASKS)}
        for name, type_str, nullable, default in _TASK_COLUMNS:
            if name in existing:
                continue
            op.add_column(
                TASKS,
                sa.Column(
                    name,
                    _col_type(type_str),
                    nullable=nullable,
                    # SQLite refuses ADD COLUMN ... NOT NULL without a constant
                    # default, so every NOT NULL column here carries one.
                    server_default=sa.text(default) if default is not None else None,
                ),
            )

    # ---- 2. task_dependencies ----
    if not insp.has_table(DEPS):
        op.create_table(
            DEPS,
            sa.Column("task_id", sa.String(length=64), nullable=False),
            sa.Column("depends_on_task_id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            # NOT NULL，与 ORM 的 ``Mapped[datetime]``（default=utcnow）一致；
            # nullable=True 会让两条建表路径漂移。
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["task_id"], ["tasks.id"], name="fk_task_dependencies_task_id_tasks",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["depends_on_task_id"], ["tasks.id"],
                name="fk_task_dependencies_depends_on_task_id_tasks",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("task_id", "depends_on_task_id"),
            # 自依赖是环的最短形式，直接在 DB 层封死（跨行成环留给服务层）
            sa.CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dep_no_self"),
        )

    # ---- 3. task_events ----
    if not insp.has_table(EVENTS):
        op.create_table(
            EVENTS,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("task_id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("from_status", sa.String(length=24), nullable=True),
            sa.Column("to_status", sa.String(length=24), nullable=True),
            sa.Column("detail", sa.JSON(), nullable=True),
            # NOT NULL，与 ORM 一致（见上）。
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            # Named to match Base.metadata's naming convention so a fresh
            # create_all database and a migrated one agree, byte for byte.
            sa.ForeignKeyConstraint(
                ["task_id"], ["tasks.id"], name="fk_task_events_task_id_tasks",
                ondelete="CASCADE",
            ),
            sa.CheckConstraint(
                "kind IN ('created', 'status', 'progress', 'plan', 'dependency')",
                name="ck_task_event_kind",
            ),
        )

    # ---- 4. 索引逐个独立护栏 ----
    # 不能挂在上面的建表分支里：新建库上 0001 的 create_all 已抢先建表，
    # 分支被跳过后索引会永久缺失（同 0026 ix_memories_owner_tier 的事故）。
    for name, table, cols in _INDEXES:
        if not _has_index(bind, table, name):
            op.create_index(name, table, list(cols))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 索引同样逐个判存在性：新建库上 create_all 建出的索引集合可能与迁移建的
    # 不同，无条件 drop_index 会中断 downgrade。
    for name, table, _cols in _INDEXES:
        if _has_index(bind, table, name):
            op.drop_index(name, table_name=table)

    if insp.has_table(EVENTS):
        op.drop_table(EVENTS)
    if insp.has_table(DEPS):
        op.drop_table(DEPS)

    if insp.has_table(TASKS):
        existing = {c["name"] for c in insp.get_columns(TASKS)}
        for name, _type_str, _nullable, _default in reversed(_TASK_COLUMNS):
            if name in existing:
                op.drop_column(TASKS, name)