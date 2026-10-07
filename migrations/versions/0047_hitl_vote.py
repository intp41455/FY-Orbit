"""Alembic migration 0047: 人工介入投票表决表（P17 · A-人工介入-03）。

三张表，理由与形状见 ``src/find_yourself/db/hitl_vote_models.py`` 的模块 docstring。

🔴 与 ``0001_initial`` 的关键交互（这是本仓最容易踩的坑，0026/0034/0041 都有
同样的坑注释）：``0001_initial.py:37`` 用 ``Base.metadata.create_all(bind)`` 建
全表。因此**新建库**上本迁移新建的表在 0001 阶段就已经由 ``create_all`` 建好，
下面的 ``has_table`` 护栏会正确跳过；**只有已迁移过的库**才真正执行建表。

由此产生两条硬要求，本迁移都遵守：

* **每个索引独立护栏**（``_has_index``），不能挂在建表分支里。否则新建库上
  ``create_all`` 抢先建过表 → 分支被跳过 → 索引永久缺失。
* **downgrade 同样逐个判存在性**。无条件 ``drop_index`` 会中断 downgrade。

ORM 侧（``db/hitl_vote_models.py``）声明的 ``index=True`` 与本迁移写死的索引名
按 ``db/base.py`` 的约定 ``ix_%(table_name)s_%(column_0_N_name)s`` 逐字对齐，
两条建表路径不漂移。

🔴 改号记录（主控 2026-10-07 裁定）：本文件原为 ``0044_hitl_vote``，与 P4 的
``0044_archive_forks`` **撞号**。主控统一发号后的终态链为：

    0043_claw_preferences_participation   （CLAW，集成线现有单头）
    → 0044_archive_forks                  （P4 存档回溯）
    → 0045_review_notes                   （P9 点哪评哪进阶）
    → 0046_p5_task_claims                 （P5 运行时认领）
    → 0047_hitl_vote                      （本文件，P17 人工介入投票表决）

故本迁移由 ``0044`` 改为 ``0047``，``down_revision`` 由
``0043_claw_preferences_participation`` 改为 ``0046_p5_task_claims``。

⚠️ 单树 ``alembic heads`` 会报 ``KeyError: '0046_p5_task_claims'`` —— 这是
worktree 隔离的正常现象（本树内没有 0044/0045/0046 三个文件），**不是断链**。
链完整性只能在集成线合并后验证。

Revision ID: 0047_hitl_vote
Revises: 0046_p5_task_claims
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_hitl_vote"
down_revision: str | None = "0046_p5_task_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SESSIONS = "hitl_vote_sessions"
CANDIDATES = "hitl_vote_candidates"
BALLOTS = "hitl_vote_ballots"

#: (索引名, 表, 列)。名字与 ``db/hitl_vote_models.py`` 里声明的**逐字一致**——
#: ``index=True`` 的列由 ``db/base.py`` 的约定 ``ix_%(table_name)s_%(column_0_N_name)s``
#: 推出，显式 ``Index(...)`` 用字面名。两边对不上就是同一列上留下两个重复索引，
#: 故此表与 ORM 必须同步维护，``tests/unit/test_hitl_vote.py`` 对其有断言。
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_hitl_vote_sessions_owner_id", SESSIONS, ("owner_id",)),
    ("ix_hitl_vote_sessions_interrupt_id", SESSIONS, ("interrupt_id",)),
    ("ix_hitl_vote_sessions_execution_id", SESSIONS, ("execution_id",)),
    ("ix_hitl_vote_owner_status", SESSIONS, ("owner_id", "status")),
    ("ix_hitl_vote_candidate_session", CANDIDATES, ("session_id",)),
    ("ix_hitl_vote_ballot_session", BALLOTS, ("session_id",)),
)


def _has_index(bind, table: str, index: str) -> bool:
    """Index existence probe, same shape as 0026's helper."""
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(i["name"] == index for i in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ---- 1. 投票会话 ----
    if not insp.has_table(SESSIONS):
        op.create_table(
            SESSIONS,
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            # 刻意不做 FK 到 hitl_interrupts：HITL 挂在多个执行面上，硬 FK 会在
            # 其中一类上留悬挂（理由同 HitlInterrupt.execution_id）。
            sa.Column("interrupt_id", sa.String(length=64), nullable=False),
            sa.Column("execution_id", sa.String(length=200), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("winner_option", sa.String(length=64), nullable=True),
            sa.Column("resolved_by", sa.String(length=24), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("tie_options", sa.JSON(), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_by", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                "status IN ('open','resolved','cancelled')", name="ck_vote_status"
            ),
            # 三种结尾各自的状态形状，一次性说清（不给 cancelled 编一个假的胜出选项
            # 来骗过 CHECK）：
            #   open      → 没有结论
            #   resolved  → 有胜出选项，且说清是「票数决定」还是「用户定音」
            #   cancelled → 明确没有胜出选项
            sa.CheckConstraint(
                "(status = 'open' AND winner_option IS NULL AND resolved_by IS NULL) "
                "OR (status = 'resolved' AND winner_option IS NOT NULL "
                "AND resolved_by IN ('tally','owner_tiebreak')) "
                "OR (status = 'cancelled' AND resolved_by = 'cancelled' "
                "AND winner_option IS NULL)",
                name="ck_vote_resolution_shape",
            ),
            sa.CheckConstraint(
                "(status = 'open' AND closed_at IS NULL) OR "
                "(status <> 'open' AND closed_at IS NOT NULL)",
                name="ck_vote_closed_at_shape",
            ),
            sa.CheckConstraint("version >= 1", name="ck_vote_version_positive"),
        )

    # ---- 2. 候选人 ----
    if not insp.has_table(CANDIDATES):
        op.create_table(
            CANDIDATES,
            sa.Column("session_id", sa.String(length=64), nullable=False),
            sa.Column("option_value", sa.String(length=64), nullable=False),
            sa.Column("label", sa.String(length=200), nullable=False),
            sa.Column("proposer", sa.String(length=200), nullable=False),
            sa.Column("weight", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("session_id", "option_value"),
            sa.CheckConstraint("weight >= 0", name="ck_vote_candidate_weight_nonneg"),
        )

    # ---- 3. 票根 ----
    if not insp.has_table(BALLOTS):
        op.create_table(
            BALLOTS,
            sa.Column("session_id", sa.String(length=64), nullable=False),
            sa.Column("voter", sa.String(length=200), nullable=False),
            sa.Column("option_value", sa.String(length=64), nullable=False),
            sa.Column("weight", sa.Integer(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("session_id", "voter"),
            # 一人一票：票数 == 投票人数。刷不出第二张票。
            sa.CheckConstraint("weight >= 1", name="ck_vote_ballot_weight_positive"),
            sa.CheckConstraint("weight <= 1000", name="ck_vote_ballot_weight_cap"),
        )

    # ---- 4. 索引逐个独立护栏 ----
    for name, table, cols in _INDEXES:
        if not _has_index(bind, table, name):
            op.create_index(name, table, list(cols))


def downgrade() -> None:
    bind = op.get_bind()

    for name, table, _cols in _INDEXES:
        if _has_index(bind, table, name):
            op.drop_index(name, table_name=table)

    insp = sa.inspect(bind)
    for table in (BALLOTS, CANDIDATES, SESSIONS):
        if insp.has_table(table):
            op.drop_table(table)