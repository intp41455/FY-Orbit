"""Alembic migration 0032: 回复评论（需求 15 item 4 的剩余部分）。

两处 schema 变更：

1. **扩宽 ``notifications.kind`` 白名单**：``ck_notif_kind`` 由单值扩为两值
   （追加 ``comment_reply``）。表达式**不手抄**：直接
   ``from find_yourself.db.collaboration_models import NOTIFICATION_KIND_IN``，
   与 ORM 模型共用同一个单一真源；降级用的旧表达式也由 ``NOTIFICATION_MENTION``
   派生（不含 ``comment_reply``）。这样「模型改了但迁移没改」（或反之）在物理上
   不可能发生。凡本文件出现的白名单字符串都来自那两个常量，没有字面量。
2. **承载回复关系**：``comments.parent_comment_id VARCHAR(64) NULL``，自引用 FK
   ``comments.id ON DELETE SET NULL`` + 索引 ``ix_comments_parent_comment_id``。
   无数据回填：既有的 ``NULL`` 即「顶层评论」。

SQLite 的现实（为什么全程用 batch）
-----------------------------------
SQLite 不支持 ``ALTER TABLE ... ADD/DROP CONSTRAINT``，也不能给既有表补 FK；改
CHECK 或加自引用 FK 都要走 ``op.batch_alter_table``（copy-and-move）。实测确认：
batch 重建会**保留**表上其余 CHECK（``ck_comment_*`` / ``ck_notif_*``）与索引，
名字也保持不变。

约束名一律用**短名**（``ck_notif_kind``）传给 ``drop_constraint`` / ``create_check_constraint``
----------------------------------------------------------------------------------
这是本迁移最容易踩的坑，2026-10-05 实测定位：

* ``db/base.py::NAMING_CONVENTION`` 的 ck 规则是
  ``ck_%(table_name)s_%(constraint_name)s`` —— 它把**传入的名字**当
  ``%(constraint_name)s`` 再插值一次。所以：
    - 传短名 ``ck_notif_kind``          → 落盘 ``ck_notifications_ck_notif_kind`` ✅
    - 传展开名 ``ck_notifications_ck_notif_kind``
      → 落盘 ``ck_notifications_ck_notifications_ck_notif_kind`` ❌ 双重前缀
* 而 ``sa.inspect`` 反射回来的**永远是展开名**。若把反射名直接喂回
  ``drop_constraint``，必然双重前缀，报
  ``ValueError: No such constraint: 'ck_notifications_ck_notifications_ck_notif_kind'``。
* 对照：``fk`` 约定插值的是 ``%(column_0_name)s`` / ``%(referred_table_name)s``
  （与传入名无关），因此**天然幂等**，传展开名也不会双重加前缀——这正是本迁移
  只有 CHECK 挂、FK 不挂的原因。

另外注意：``0001_initial`` 用 ``Base.metadata.create_all`` 建全表，
所以全新库在 0031 时约束名**已经是展开名、且白名单已经是新值**，
本迁移的 ``upgrade`` 在 fresh-DB 上是 no-op（幂等守卫正确命中）；
真正会执行 drop/create 的是 ``downgrade``，也就是踩坑的那一侧。

幂等：每步都先 ``sa.inspect`` 判断当前状态再动手，重复执行不报错也不重复加东西。

Revision ID: 0032_collaboration_replies
Revises: 0031_plugin_ecosystem
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from find_yourself.db.collaboration_models import (
    NOTIFICATION_KIND_IN,
    NOTIFICATION_MENTION,
)

revision: str = "0032_collaboration_replies"
down_revision: str | None = "0031_plugin_ecosystem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 短名（与 0030 实际落盘的约束名一致；命名约定在 op.create_table 下未展开）。
_KIND_CONSTRAINT = "ck_notif_kind"
#: 降级要回到的历史表达式，从常量派生（= ``kind IN ('mention')``），不手抄。
_OLD_KIND_IN = "kind IN (" + ", ".join(f"'{k}'" for k in (NOTIFICATION_MENTION,)) + ")"
_PARENT_COL = "parent_comment_id"
_PARENT_FK = "fk_comments_parent_comment_id_comments"
_PARENT_INDEX = "ix_comments_parent_comment_id"


def _kind_check(bind) -> tuple[bool, str | None]:
    """探测 ``notifications`` 上 kind 白名单 CHECK 是否存在，返回 ``(存在, sqltext)``。

    按**后缀**匹配：真实 ``alembic`` 环境下 ``sa.inspect`` 反射出的是**展开名**
    ``ck_notifications_ck_notif_kind``；某些裸 ``Operations`` 路径没有命名约定，
    反射出的是短名 ``ck_notif_kind``。两者都应以 ``_KIND_CONSTRAINT`` 结尾。

    只回传 ``(存在, sqltext)``，**不回传名字** —— 因为反射名不能喂回
    ``drop_constraint``（见模块 docstring 的双重前缀分析）。
    调用方统一传短名 ``_KIND_CONSTRAINT``，由命名约定展开一次。
    """
    for c in sa.inspect(bind).get_check_constraints("notifications"):
        if (c.get("name") or "").endswith(_KIND_CONSTRAINT):
            return True, (c.get("sqltext") or "").strip()
    return False, None


def _has_index(bind, table: str, name: str) -> bool:
    return any(i.get("name") == name for i in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1) 扩宽 notifications.kind 白名单
    if insp.has_table("notifications"):
        found, current = _kind_check(bind)
        if not found:
            # 表在但白名单 CHECK 不在（手工改过库的库）：不猜，直接报。
            raise RuntimeError(
                "notifications 上找不到 kind 白名单 CHECK（短名 "
                f"{_KIND_CONSTRAINT!r}）。库可能被手工改过；"
                "本迁移不负责重建未知约束，请人工确认后再 upgrade。"
            )
        if current != NOTIFICATION_KIND_IN:
            with op.batch_alter_table("notifications") as bt:
                bt.drop_constraint(_KIND_CONSTRAINT, type_="check")
                bt.create_check_constraint(_KIND_CONSTRAINT, NOTIFICATION_KIND_IN)

    # 2) comments 增加 parent_comment_id + 自引用 FK + 索引
    if insp.has_table("comments"):
        columns = {c["name"] for c in insp.get_columns("comments")}
        if _PARENT_COL not in columns:
            with op.batch_alter_table("comments") as bt:
                bt.add_column(sa.Column(_PARENT_COL, sa.String(length=64), nullable=True))
                bt.create_foreign_key(
                    _PARENT_FK, "comments", [_PARENT_COL], ["id"], ondelete="SET NULL"
                )
        if not _has_index(bind, "comments", _PARENT_INDEX):
            op.create_index(_PARENT_INDEX, "comments", [_PARENT_COL])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 2') 回退 comments.parent_comment_id
    if insp.has_table("comments"):
        if _has_index(bind, "comments", _PARENT_INDEX):
            op.drop_index(_PARENT_INDEX, table_name="comments")
        columns = {c["name"] for c in insp.get_columns("comments")}
        if _PARENT_COL in columns:
            with op.batch_alter_table("comments") as bt:
                # 仅在 FK 真的存在时删（历史/异常链的兜底）。
                fk_names = {fk.get("name") for fk in insp.get_foreign_keys("comments")}
                if _PARENT_FK in fk_names:
                    bt.drop_constraint(_PARENT_FK, type_="foreignkey")
                bt.drop_column(_PARENT_COL)

    # 1') 收回 notifications.kind 白名单
    if insp.has_table("notifications"):
        found, current = _kind_check(bind)
        if not found:
            return
        if current != _OLD_KIND_IN:
            with op.batch_alter_table("notifications") as bt:
                bt.drop_constraint(_KIND_CONSTRAINT, type_="check")
                bt.create_check_constraint(_KIND_CONSTRAINT, _OLD_KIND_IN)
