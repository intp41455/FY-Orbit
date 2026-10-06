"""Alembic migration 0040: T6 抗中断韧性 —— 中断台账 + 流式片段落盘.

两张新表（模型同构注册在 ``db/resilience_models.py``）：

* ``interruption_events`` —— 统一中断台账（T6-A/G7）：六类中断场景 S1–S6
  每次一行，含处置策略（auto/confirm/manual）、可续作指针
  （thread_id/message_id/stash_id）、供应商指纹（S5 检测）与恢复状态。
  恢复中心 API（``api/routes/recovery.py``）由此驱动。
* ``stream_segments``    —— 流式产出逐帧落盘（T6-D/G3）：``(message_id, seq)``
  唯一，存 SSE 帧原文；断流后凭 Last-Event-ID 语义取回已产出片段。

架构铁律：两者都落主库、重启后仍在，**绝不允许内存态**（WorkStash P1-04
同款规矩）。中断/恢复动作同时往 AuditService 哈希链挂帧（红线 4）。

编号说明：链头实为 ``0039_rag_presets_and_graph``（单头），本迁移顺接；
0038 由包5 的 ``0038_dsl_flow_lifecycle`` 占用。

Revision ID: 0040_resilience_interruptions_streams
Revises: 0039_rag_presets_and_graph
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_resilience_interruptions_streams"
down_revision: str | None = "0039_rag_presets_and_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("interruption_events"):
        op.create_table(
            "interruption_events",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("interruption_class", sa.String(length=40), nullable=False),
            sa.Column("task_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("thread_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("message_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("stash_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("detail", sa.Text(), nullable=False, server_default=""),
            sa.Column("resume_policy", sa.String(length=20), nullable=False,
                      server_default="manual"),
            sa.Column("provider_fp", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
            sa.Column("last_resume_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_interruption_status_created", "interruption_events",
            ["status", "created_at"],
        )
        op.create_index(
            "ix_interruption_owner_created", "interruption_events",
            ["owner_id", "created_at"],
        )

    if not insp.has_table("stream_segments"):
        op.create_table(
            "stream_segments",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("message_id", sa.String(length=200), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("frame", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("message_id", "seq", name="uq_stream_segments_msg_seq"),
        )
        op.create_index(
            "ix_stream_segments_msg_seq", "stream_segments", ["message_id", "seq"],
        )


def downgrade() -> None:
    op.drop_index("ix_stream_segments_msg_seq", table_name="stream_segments")
    op.drop_table("stream_segments")
    op.drop_index("ix_interruption_owner_created", table_name="interruption_events")
    op.drop_index("ix_interruption_status_created", table_name="interruption_events")
    op.drop_table("interruption_events")
