"""Alembic migration 0038: DSL 画布流程生命周期表（``dsl_flows`` / ``dsl_flow_versions``）——补齐包5.

Creates the two tables backing Chatflow/Workflow 双形态 + 草稿/发布版本化
（A-画布搭建器-05/06）:

* ``dsl_flows`` —— 一张画布流程：``flow_type``（``chatflow`` 会话型 /
  ``workflow`` 自动化型，CHECK 约束封闭）+ ``draft_doc``（草稿，随时可改）+
  ``published_doc``（已发布快照）+ ``published_version``（单调递增版本号）；
* ``dsl_flow_versions`` —— 一次发布的历史快照（append-only：``(flow_id, version)``
  唯一，发布历史只增不减；**回滚是把历史版本取回草稿**，不改写历史）。

与 0037 同款设计：ORM 模型（``DslFlowRow`` / ``DslFlowVersionRow``）放在
``services/dsl_canvas.py`` 而**不**导入 ``db/models.py``，因此 ``0001`` 的
``Base.metadata.create_all`` 永远看不到这两张表，本迁移是它们唯一的建表者。
测试侧 conftest 的 ``create_all`` 因测试模块 import 该服务模块而同样能看到，
保证测试约束与迁移约束一致（FROZEN_CONTRACT §3.2）。

编号说明：任务书原定「迁移号已用到 0037，只许用 0038」——链头正是 0037，
本迁移顺延为 **0038**，不碰任何他人迁移。

Revision ID: 0038_dsl_flow_lifecycle
Revises: 0037_capability_grants
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0038_dsl_flow_lifecycle"
down_revision: str | None = "0037_capability_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLOWS = "dsl_flows"
VERSIONS = "dsl_flow_versions"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(FLOWS):
        op.create_table(
            FLOWS,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("flow_type", sa.String(length=16),
                      server_default="workflow", nullable=False),
            sa.Column("draft_doc", sa.JSON(), nullable=True),
            sa.Column("published_doc", sa.JSON(), nullable=True),
            sa.Column("published_version", sa.Integer(),
                      server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "flow_type IN ('chatflow', 'workflow')",
                name="ck_dsl_flow_type"),
        )
        op.create_index("ix_dsl_flows_owner_id", FLOWS, ["owner_id"])

    if not insp.has_table(VERSIONS):
        op.create_table(
            VERSIONS,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("flow_id", sa.String(length=64),
                      sa.ForeignKey("dsl_flows.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("doc", sa.JSON(), nullable=False),
            sa.Column("note", sa.String(length=200),
                      server_default="", nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("flow_id", "version", name="uq_dsl_flow_version"),
        )
        op.create_index("ix_dsl_flow_versions_flow_id", VERSIONS, ["flow_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table(VERSIONS):
        op.drop_index("ix_dsl_flow_versions_flow_id", table_name=VERSIONS)
        op.drop_table(VERSIONS)
    if insp.has_table(FLOWS):
        op.drop_index("ix_dsl_flows_owner_id", table_name=FLOWS)
        op.drop_table(FLOWS)
