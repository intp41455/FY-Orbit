"""Alembic migration 0027: 团队级审批（需求 6）。

引入三张表：

* ``approval_teams`` —— 一个审批域。只表达「这批申请归谁管」。
* ``approval_team_members`` —— 谁在团队里、是什么角色。注意 role 是
  ``requester`` / ``approver`` / ``member`` 三值，``member`` 表示两者皆可；
  ``uq_atm_team_member`` 保证一个人在同队只有一行（资格是集合，不是可重复授予）。
* ``team_approval_requests`` —— 一次申请，**不含 status 列**。

为什么审批请求表没有 status
---------------------------
「等待中 vs 已决策」已经是 ``hitl_interrupts`` 的 schema 级不变量
（``ck_hitl_decided_shape``，由 0025 建立）。这里若再加一个 status，就会出现
两个真相源：HITL 认为已批准、审批表认为待批，而**没有任何约束**能阻止这种
分叉。所以 ``team_approval_requests`` 只存团队维度（归哪个队、谁提的、第几轮），
状态一律读 ``hitl_interrupts`` 那一行——通过 ``interrupt_id`` 外键指向它。
``interrupt_id`` 上的 UNIQUE 同时保证「一条 HITL 中断最多被一个团队申请引用」。

为什么「审批人不能审自己」不是 CHECK 约束
-----------------------------------------
决策人落在 ``hitl_interrupts.decided_by``，与 ``requester_id`` 分属两行。
SQLite 与 Postgres 的 CHECK 只能读同一行的列，跨表不变式无法用 CHECK 表达。
写一个恒真的 CHECK 只会让后来的人误以为「有表级兜底」。真正执行它的是
``TeamApprovalService._require_eligible_approver``——在拍板的前一刻拿着成员行判。

Idempotency: 建表由 ``sa.inspect(bind)`` 守卫，沿用 0005-0026 的写法。

Revision ID: 0027_team_approval
Revises: 0026_memory_tiers
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_team_approval"
down_revision: str | None = "0026_memory_tiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("approval_teams"):
        op.create_table(
            "approval_teams",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("created_by", sa.String(length=200), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint(
                "state IN ('active','archived')", name="ck_at_state"
            ),
            sa.CheckConstraint("version >= 1", name="ck_at_version_positive"),
        )
        op.create_index("ix_approval_teams_created_by", "approval_teams", ["created_by"])

    if not insp.has_table("approval_team_members"):
        op.create_table(
            "approval_team_members",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "team_id",
                sa.String(length=64),
                sa.ForeignKey("approval_teams.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", sa.String(length=200), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("granted_by", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint(
                "role IN ('requester','approver','member')", name="ck_atm_one_role"
            ),
            sa.CheckConstraint(
                "state IN ('active','revoked')", name="ck_atm_state"
            ),
            sa.CheckConstraint("version >= 1", name="ck_atm_version_positive"),
            sa.UniqueConstraint("team_id", "user_id", name="uq_atm_team_member"),
        )
        op.create_index(
            "ix_approval_team_members_team_id", "approval_team_members", ["team_id"]
        )
        op.create_index(
            "ix_approval_team_members_user_id", "approval_team_members", ["user_id"]
        )
        op.create_index(
            "ix_atm_team_role",
            "approval_team_members",
            ["team_id", "role", "state"],
        )

    if not insp.has_table("team_approval_requests"):
        op.create_table(
            "team_approval_requests",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "team_id",
                sa.String(length=64),
                sa.ForeignKey("approval_teams.id", ondelete="CASCADE"),
                nullable=False,
            ),
            # 状态在hitl_interrupts 那一行；这里只存团队维度。
            sa.Column(
                "interrupt_id",
                sa.String(length=64),
                sa.ForeignKey("hitl_interrupts.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("requester_id", sa.String(length=200), nullable=False),
            sa.Column(
                "required_role", sa.String(length=16), nullable=False,
                server_default="approver",
            ),
            sa.Column("round", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column("supersedes_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint(
                "required_role IN ('requester','approver','member')",
                name="ck_tar_required_role",
            ),
            sa.CheckConstraint("round >= 1", name="ck_tar_round_positive"),
            sa.CheckConstraint("version >= 1", name="ck_tar_version_positive"),
        )
        op.create_index(
            "ix_team_approval_requests_team_id", "team_approval_requests", ["team_id"]
        )
        op.create_index(
            "ix_team_approval_requests_interrupt_id",
            "team_approval_requests",
            ["interrupt_id"],
            unique=True,
        )
        op.create_index(
            "ix_team_approval_requests_requester_id",
            "team_approval_requests",
            ["requester_id"],
        )
        op.create_index(
            "ix_team_approval_requests_supersedes_id",
            "team_approval_requests",
            ["supersedes_id"],
        )
        op.create_index(
            "ix_tar_team_requester",
            "team_approval_requests",
            ["team_id", "requester_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("team_approval_requests"):
        op.drop_index("ix_tar_team_requester", table_name="team_approval_requests")
        op.drop_index(
            "ix_team_approval_requests_supersedes_id", table_name="team_approval_requests"
        )
        op.drop_index(
            "ix_team_approval_requests_requester_id", table_name="team_approval_requests"
        )
        op.drop_index(
            "ix_team_approval_requests_interrupt_id", table_name="team_approval_requests"
        )
        op.drop_index(
            "ix_team_approval_requests_team_id", table_name="team_approval_requests"
        )
        op.drop_table("team_approval_requests")

    if insp.has_table("approval_team_members"):
        op.drop_index("ix_atm_team_role", table_name="approval_team_members")
        op.drop_index(
            "ix_approval_team_members_user_id", table_name="approval_team_members"
        )
        op.drop_index(
            "ix_approval_team_members_team_id", table_name="approval_team_members"
        )
        op.drop_table("approval_team_members")

    if insp.has_table("approval_teams"):
        op.drop_index("ix_approval_teams_created_by", table_name="approval_teams")
        op.drop_table("approval_teams")
