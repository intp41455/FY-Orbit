"""Alembic migration 0045: P9 点哪评哪 —— review_sessions / review_notes 表.

两张新表（ORM 同构注册在 ``db/review_models.py``）：

* ``review_sessions`` —— 一次评审会话（A-点哪评哪-05 热刷新闭环 / A-路线-03 双路线）。
* ``review_notes`` —— 单条意见，承载 dom 点选 / 圈选区域 / 画笔+语音三类定位
  以及 TOKEN 优化三项（真写标记 / 短代码 / DOM 指纹差分）。

编号申领（主控 2026-10-07 裁定）：**P4=0044 / P9=0045 / P5=0046** 线性串链。
主控同时要求迁移**文件名不带包号标记**，故本文件为 ``0045_review_notes.py``
（非 ``0045_p9_review_notes``），``revision`` 同步为 ``0045_review_notes``。
下游：``0046_p5_task_claims`` 挂在本迁移之后。

Revision ID: 0045_review_notes
Revises: 0044_archive_forks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_review_notes"
down_revision: str | None = "0044_archive_forks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("review_sessions"):
        op.create_table(
            "review_sessions",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("page", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("route", sa.String(length=30), nullable=False, server_default="whitebox"),
            sa.Column("iteration", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("refresh_state", sa.String(length=20), nullable=False, server_default="idle"),
            sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("refresh_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_review_sessions_owner_created", "review_sessions",
            ["owner_id", "created_at"],
        )
        op.create_index("ix_review_sessions_page", "review_sessions", ["page"])

    if not insp.has_table("review_notes"):
        op.create_table(
            "review_notes",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("session_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("page", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("mode", sa.String(length=20), nullable=False, server_default="dom"),
            sa.Column("tag", sa.String(length=40), nullable=False, server_default=""),
            sa.Column("element_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("element_class", sa.String(length=300), nullable=False, server_default=""),
            sa.Column("text", sa.String(length=300), nullable=False, server_default=""),
            sa.Column("selector", sa.Text(), nullable=False, server_default=""),
            sa.Column("dom_path", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("region", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("strokes", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("audio_ref", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("audio_transcript", sa.Text(), nullable=False, server_default=""),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("marker", sa.String(length=60), nullable=False, server_default=""),
            sa.Column("short_code", sa.String(length=12), nullable=False, server_default=""),
            sa.Column("dom_digest", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("prev_dom_digest", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("state", sa.String(length=20), nullable=False, server_default="open"),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        # A-点哪评哪-09：会话内短码唯一（部分唯一索引，放过空串）
        op.create_index(
            "uq_review_notes_session_short_code", "review_notes",
            ["session_id", "short_code"], unique=True,
            sqlite_where=sa.text("short_code <> ''"),
            postgresql_where=sa.text("short_code <> ''"),
        )
        op.create_index(
            "ix_review_notes_session_created", "review_notes",
            ["session_id", "created_at"],
        )
        op.create_index(
            "ix_review_notes_owner_state", "review_notes", ["owner_id", "state"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("review_notes"):
        op.drop_index("ix_review_notes_owner_state", table_name="review_notes")
        op.drop_index("ix_review_notes_session_created", table_name="review_notes")
        op.drop_index("uq_review_notes_session_short_code", table_name="review_notes")
        op.drop_table("review_notes")
    if insp.has_table("review_sessions"):
        op.drop_index("ix_review_sessions_page", table_name="review_sessions")
        op.drop_index("ix_review_sessions_owner_created", table_name="review_sessions")
        op.drop_table("review_sessions")
