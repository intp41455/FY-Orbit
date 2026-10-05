"""Alembic migration 0034: B 包生活模拟存档 (``life_saves``).

Introduces ``life_saves`` — the per-owner authoritative save backing the
像素风生活模拟 (B 包) loop: 时钟/天气 / 背包 / 金币 / 技能经验 / NPC 好感 /
店铺 / 任务日志 / 每日采集计数.

``0032`` is the parent, so this revision only becomes reachable once 0032 is in
the tree (``0033`` already revises it).

Design note — why this migration is the *only* creator of ``life_saves``
---------------------------------------------------------------------
``0001_initial.py:37`` calls ``Base.metadata.create_all(bind=bind)``, which builds
**every** table registered on ``Base.metadata`` at import time. If ``LifeSaveRow``
were imported from ``find_yourself.db.models``, a brand-new database would already
have ``life_saves`` after ``0001`` — and this migration would either fail
("table already exists") or, on a pre-existing database, silently degrade into a
no-op while ORM and schema drift apart.

We therefore take option **C** of the task book §2.3: the model lives in
``services/cabin_life/persistence.py`` and is deliberately **not** re-exported
from ``db/models.py``, exactly like W2's ``services/cabin_gameplay.py::CabinSave``
(a shape already proven in production). Consequence: ``create_all`` never sees
this table, so this migration is its sole source of truth.

As defence in depth — and because ``0016`` already established the idiom — table
creation is additionally guarded by ``sa.inspect(bind)``: if the table somehow
already exists we skip creation and then **backfill any missing columns** via
``ALTER TABLE ... ADD COLUMN``. That makes this migration correct on both paths:
a fresh database, and one that somehow already carries the table.

The ``ck_`` constraint names below are passed as **short** names, exactly like the
ORM model. Alembic builds the ``op.create_table`` Table from
``context.opts['target_metadata']`` -- i.e. ``Base.metadata``, which carries
``db/base.py``'s ``NAMING_CONVENTION`` -- so the convention *is* applied to
op-level DDL, expanding ``life_save_coins_nonnegative`` into
``ck_life_saves_life_save_coins_nonnegative``. Passing an already-expanded name
expands a second time (``ck_life_saves_ck_life_saves_*``) -- the exact
double-prefix defect P7 fixed in 0032. W2's 0016 passes short names for this reason.

Revision ID: 0034_cabin_life_save
Revises: 0033_preview_source_data_kind
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0034_cabin_life_save"
down_revision: str | None = "0033_preview_source_data_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "life_saves"

#: (列名, 列类型, server_default)。用于「表已存在」时的补列兜底。
#:
#: 每一列都必须带 server_default：SQLite 不允许对已有行的表执行
#: ``ALTER TABLE ... ADD COLUMN <col> NOT NULL`` 且不给默认值（报
#: "Cannot add a NOT NULL column with default value NULL"）。默认值也语义上对应
#: ORM 上的 ``default=dict`` / ``default=0`` / ``default=utcnow``，不是为了过关乱填的。
_COLUMNS: tuple[tuple[str, object, object | None], ...] = (
    ("theme", sa.String(length=32), sa.text("'forest'")),
    ("clock", sa.JSON(), sa.text("'{}'")),
    ("weather", sa.JSON(), sa.text("'{}'")),
    ("bag", sa.JSON(), sa.text("'{}'")),
    ("coins", sa.Integer(), sa.text("0")),
    ("skill_exp", sa.Integer(), sa.text("0")),
    ("affinity", sa.JSON(), sa.text("'{}'")),
    ("gifts_today", sa.JSON(), sa.text("'{}'")),
    ("shop", sa.JSON(), sa.text("'{}'")),
    ("quest_log", sa.JSON(), sa.text("'{}'")),
    ("gather_counts", sa.JSON(), sa.text("'{}'")),
    ("version", sa.Integer(), sa.text("1")),
    # 时间户用常量字面量作为默认值：SQLite 在 ADD COLUMN 时不接受
    # CURRENT_TIMESTAMP（报 "non-constant default"），而本补列路径只面向「表已存在」
    # 的异常情况，时间户取一个固定日期字面量已够——
    # 它只用于运维观察，不参与任何游戏规则（时间真源是 clock 块）。
    ("created_at", sa.DateTime(timezone=True), sa.text("'1970-01-01 00:00:00'")),
    ("updated_at", sa.DateTime(timezone=True), sa.text("'1970-01-01 00:00:00'")),
)


def _add_missing_columns(bind: sa.engine.Connection) -> list[str]:
    """表已存在时补齐缺列；返回实际补上的列名（便于测试断言）。"""
    insp = sa.inspect(bind)
    present = {c["name"] for c in insp.get_columns(TABLE)}
    added: list[str] = []
    for name, type_, default in _COLUMNS:
        if name in present:
            continue
        # 带默认值才能在「表已有数据」的情况下补列且不破坏 NOT NULL。
        op.add_column(
            TABLE,
            sa.Column(
                name, type_, nullable=False, server_default=default
            ),
        )
        added.append(name)
    return added


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("owner_id", sa.String(length=200), primary_key=True),
            sa.Column("theme", sa.String(length=32), nullable=False),
            sa.Column("clock", sa.JSON(), nullable=False),
            sa.Column("weather", sa.JSON(), nullable=False),
            sa.Column("bag", sa.JSON(), nullable=False),
            sa.Column("coins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skill_exp", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("affinity", sa.JSON(), nullable=False),
            sa.Column("gifts_today", sa.JSON(), nullable=False),
            sa.Column("shop", sa.JSON(), nullable=False),
            sa.Column("quest_log", sa.JSON(), nullable=False),
            sa.Column("gather_counts", sa.JSON(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            # 约束名传**短名**：op DDL 走 target_metadata（Base.metadata），会套用
            # NAMING_CONVENTION 展开一次。传展开名会得到双重前缀
            # `ck_life_saves_ck_life_saves_*`（与 0032 在 P7 修过的同一个坑）。
            sa.CheckConstraint("coins >= 0", name="life_save_coins_nonnegative"),
            sa.CheckConstraint("skill_exp >= 0", name="life_save_exp_nonnegative"),
            sa.CheckConstraint("version >= 1", name="life_save_version_positive"),
        )
        return

    # 表已存在（create_all 抢先建过，或本迁移重跑）：补齐缺列而不是报错退出。
    _add_missing_columns(bind)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table(TABLE):
        op.drop_table(TABLE)
