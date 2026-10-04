"""Alembic migration 0030: 多人协作闭环（需求 15 第一切片）。

引入三张表：

* ``collaboration_roles`` —— 把 ``admin`` / ``manager`` / ``viewer`` 能力分配给
  某人在**某一条 record** 上。``owner`` 是隐含角色（record 的 owner 本人），
  **不落行**；``expires_at`` **NOT NULL**，所以「永不过期的协作授权」在 schema
  层就不可能存在（与 grant 的「≤30 天」同一条纪律，30 天常量在 service 层复用
  ``grant.MAX_GRANT_SECONDS``）。
* ``comments`` —— 挂在 record 上的评论。``owner_id`` 冗余存的是 **record 的 owner**，
  让 owner 隔离成为一条可查询的 WHERE 谓词。
* ``notifications`` —— ``@`` 提及的站内通知。``owner_id`` 是 **收件人**；
  ``summary`` **不含评论正文**。``uq_notif_recipient_comment`` 让重复解析幂等。

约束名用**短名**（如 ``ck_cr_role``）：``db/base.py::NAMING_CONVENTION`` 会把它
解析成 ``ck_collaboration_roles_ck_cr_role``，与 ``Base.metadata.create_all`` 建出
的名字逐字相同——fresh-DB 与 migrated-DB 的 schema 不允许长期分叉（与 0029 同一纪律）。

Idempotency: 建表/建索引由 ``sa.inspect(bind)`` 守卫，沿用 0005-0029 的写法。

Revision ID: 0030_collaboration
Revises: 0029_task_stage_check
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_collaboration"
down_revision: str | None = "0029_task_stage_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(bind, table: str, index_name: str) -> bool:
    return any(i.get("name") == index_name for i in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("collaboration_roles"):
        op.create_table(
            "collaboration_roles",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("record_kind", sa.String(length=16), nullable=False),
            sa.Column("record_id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("user_id", sa.String(length=200), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="viewer"),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("granted_by", sa.String(length=200), nullable=False, server_default=""),
            # 非空 = 不存在永久协作授权。
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint("role IN ('admin','manager','viewer')", name="ck_cr_role"),
            sa.CheckConstraint("state IN ('active','revoked')", name="ck_cr_state"),
            sa.CheckConstraint("version >= 1", name="ck_cr_version_positive"),
            sa.CheckConstraint("length(record_id) > 0", name="ck_cr_record_nonempty"),
            sa.CheckConstraint("length(user_id) > 0", name="ck_cr_user_nonempty"),
            sa.UniqueConstraint("record_kind", "record_id", "user_id", name="uq_cr_record_user"),
        )
        op.create_index("ix_collaboration_roles_owner_id", "collaboration_roles", ["owner_id"])
        op.create_index("ix_collaboration_roles_user_id", "collaboration_roles", ["user_id"])
        op.create_index(
            "ix_cr_record_state", "collaboration_roles", ["record_kind", "record_id", "state"]
        )
        op.create_index("ix_cr_user_state", "collaboration_roles", ["user_id", "state"])

    if not insp.has_table("comments"):
        op.create_table(
            "comments",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("record_kind", sa.String(length=16), nullable=False),
            sa.Column("record_id", sa.String(length=64), nullable=False),
            sa.Column("author_id", sa.String(length=200), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("mentions", sa.JSON(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint(
                "record_kind IN ('task','canvas','artifact','memory')", name="ck_comment_kind"
            ),
            sa.CheckConstraint("length(body) > 0", name="ck_comment_body_nonempty"),
            sa.CheckConstraint("length(record_id) > 0", name="ck_comment_record_nonempty"),
            sa.CheckConstraint("version >= 1", name="ck_comment_version_positive"),
        )
        op.create_index("ix_comments_owner_id", "comments", ["owner_id"])
        op.create_index("ix_comments_author_id", "comments", ["author_id"])
        op.create_index(
            "ix_comment_record_created", "comments", ["record_kind", "record_id", "created_at"]
        )
        op.create_index(
            "ix_comment_owner_record", "comments", ["owner_id", "record_kind", "record_id"]
        )

    if not insp.has_table("notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("kind", sa.String(length=24), nullable=False, server_default="mention"),
            sa.Column("record_kind", sa.String(length=16), nullable=False),
            sa.Column("record_id", sa.String(length=64), nullable=False),
            sa.Column("comment_id", sa.String(length=64), nullable=False),
            sa.Column("author_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("summary", sa.String(length=240), nullable=False, server_default=""),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint("kind IN ('mention')", name="ck_notif_kind"),
            sa.CheckConstraint("version >= 1", name="ck_notif_version_positive"),
            sa.CheckConstraint("length(record_id) > 0", name="ck_notif_record_nonempty"),
            sa.UniqueConstraint(
                "owner_id", "comment_id", "kind", name="uq_notif_recipient_comment"
            ),
        )
        op.create_index("ix_notifications_owner_id", "notifications", ["owner_id"])
        op.create_index("ix_notifications_comment_id", "notifications", ["comment_id"])
        op.create_index("ix_notif_owner_read", "notifications", ["owner_id", "read_at"])
        op.create_index("ix_notif_owner_created", "notifications", ["owner_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("notifications"):
        for name in (
            "ix_notif_owner_created", "ix_notif_owner_read",
            "ix_notifications_comment_id", "ix_notifications_owner_id",
        ):
            if _has_index(bind, "notifications", name):
                op.drop_index(name, table_name="notifications")
        op.drop_table("notifications")

    if insp.has_table("comments"):
        for name in (
            "ix_comment_owner_record", "ix_comment_record_created",
            "ix_comments_author_id", "ix_comments_owner_id",
        ):
            if _has_index(bind, "comments", name):
                op.drop_index(name, table_name="comments")
        op.drop_table("comments")

    if insp.has_table("collaboration_roles"):
        for name in (
            "ix_cr_user_state", "ix_cr_record_state",
            "ix_collaboration_roles_user_id", "ix_collaboration_roles_owner_id",
        ):
            if _has_index(bind, "collaboration_roles", name):
                op.drop_index(name, table_name="collaboration_roles")
        op.drop_table("collaboration_roles")
