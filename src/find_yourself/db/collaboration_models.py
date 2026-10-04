"""多人协作闭环的数据模型（需求 15，第一切片：评论 / @人 / 通知 / 角色）。

本模块只拥有协作自己的三张表，**不改** ``db/models.py``：

- :class:`Comment` —— 挂在某条 record（任务/画布/产物/记忆）上的一条评论。
  ``owner_id`` 冗余存的是 **record 的 owner**，不是作者：它让「这条评论属于
  谁的记录」成为可查询的归属谓词，owner 隔离因此是 **SQL WHERE 条件**，
  而不是靠调用方记得逐一比对（``audit.frames_for_message`` 的同一条纪律）。
- :class:`Notification` —— ``@`` 提及产生的站内通知。``owner_id`` 是 **收件人**。
  **刻意不存评论正文**（见 ``summary`` 列的说明）。
- :class:`CollaborationRole` —— 把 ``admin`` / ``manager`` / ``viewer`` 分配给
  某个 user_id 在**某一条 record** 上的能力。``owner`` 是隐含角色（就是 record
  的 owner 本人），**不落行**：给 owner 也存一行会让「谁是记录主人」出现两个
  真相源。

角色模型与 ``grant`` 的关系（不得绕过）
---------------------------------------
``grant`` 管的是「**跨域的数据可见性**」（``source_domain``/``consumer_domain``
/``record_ids``/``expires_at``，禁空、禁通配、≤30 天）。协作角色管的是「**在同一条
record 上能读还是能写**」——**另一个轴**。两者不互相冒充，且协作角色**叠加在**
grant 之上：跨域的服务身份（agent/worker）必须先过 ``GrantService.is_authorized``
这一关，角色才有机会被读到。所以在 ``collaboration.py`` 里，「服务身份」路径会
真的调用 ``grant.is_authorized``，不另写一份可见性判定。

三条由 schema 保证的不变量
--------------------------
1. ``expires_at`` **NOT NULL** —— 协作授权永不过期在物理上不可能（与 grant 的
   「≤30 天」同一条纪律；30 天这个数字在 service 层直接复用
   ``grant.MAX_GRANT_SECONDS``，不手抄第二份）。
2. ``length(record_id) > 0`` / ``length(user_id) > 0`` —— 禁止空 record / 空主体，
   即禁止「通配授权」。
3. ``uq_notif_recipient_comment`` —— 同一条评论对同一个人最多一条通知，
   重复解析 ``@`` 是幂等的。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow

#: 可被评论/提及的 record 种类。与 service 层的解析器一一对应。
RECORD_KINDS = ("task", "canvas", "artifact", "memory")

#: **可显式分配**的角色。``owner`` 不在其中：它是 record 的 owner 本人，
#: 隐含存在，不落行（见模块 docstring）。
ASSIGNABLE_ROLES = ("admin", "manager", "viewer")

#: 全部角色（含隐含的 owner），用于能力查表与测试断言。
ALL_ROLES = ("owner", "admin", "manager", "viewer")

#: 能力矩阵的**单一真源**。``delete_any`` 与「删自己的」是两回事：任何能写的
#: 角色都能删自己的评论，但只有 ``delete_any`` 能删别人的。
#: ``manage_roles`` 允许给 record 分配/撤销角色。
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "owner": frozenset({"read", "write", "delete_any", "manage_roles"}),
    "admin": frozenset({"read", "write", "delete_any", "manage_roles"}),
    "manager": frozenset({"read", "write"}),
    "viewer": frozenset({"read"}),
}

ROLE_STATES = ("active", "revoked")
#: 被 ``@`` 提及产生的通知。**当前唯一进白名单**的 kind。
NOTIFICATION_MENTION = "mention"
#: 回复评论触发的通知。**语义已在 ``services/collaboration.py`` 定义并实现
#: （纯函数 planner），但尚未进白名单**：扩白名单要同步扩 ``ck_notif_kind``
#: 的 DB CHECK，属 schema 变更，必须走迁移（待 0032）。在此之前任何尝试落库
#: 该 kind 的调用都会被服务层的白名单闸门拒绝（fail loud，不静默丢弃）。
NOTIFICATION_REPLY = "comment_reply"
#: **当前 DB CHECK 允许**的 kind。单一真源：``ck_notif_kind`` 的表达式由它派生，
#: 服务层也只引用这里的常量、不硬编码字面量。加一个 kind = 在此加值 + 一次迁移。
NOTIFICATION_KINDS = (NOTIFICATION_MENTION,)
#: ``ck_notif_kind`` 的表达式。**逐字**与 migration 0030 一致（``kind IN ('mention')``）：
#: 用 ``", ".join`` 而不是元组的 ``repr``，否则单元素元组会渲染成 ``('mention',)``，
#: 带尾逗号 → fresh-DB 与 migrated-DB 的 CHECK 文本分叉（DDL 语义相同但文本不同，
#: 正是迁移纪律要消除的漂移）。
NOTIFICATION_KIND_IN = "kind IN (" + ", ".join(f"'{k}'" for k in NOTIFICATION_KINDS) + ")"


class CollaborationRole(Base):
    """某位 user 在**某一条 record** 上的协作角色。

    ``(record_kind, record_id, user_id)`` 唯一：能力是集合，不是可重复的授予。
    ``expires_at`` 非空是刻意的——一条永不过期的协作授权就等于一个后门。
    """

    __tablename__ = "collaboration_roles"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    record_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: record 的 owner。用于审计与隔离校验，不参与能力判定（能力看 user_id）。
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    #: 被授予能力的 user_id（系统里每个真人主体都是 owner 身份，见 team_approval）。
    user_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="viewer")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    #: 谁给的。协作权是显式授予的，来源必须留痕。
    granted_by: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: 到期时间。非空 = 不存在永久协作授权。
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','manager','viewer')", name="ck_cr_role"
        ),
        CheckConstraint("state IN ('active','revoked')", name="ck_cr_state"),
        CheckConstraint("version >= 1", name="ck_cr_version_positive"),
        # 禁通配：record 与主体都必须非空。
        CheckConstraint("length(record_id) > 0", name="ck_cr_record_nonempty"),
        CheckConstraint("length(user_id) > 0", name="ck_cr_user_nonempty"),
        UniqueConstraint("record_kind", "record_id", "user_id", name="uq_cr_record_user"),
        Index("ix_cr_record_state", "record_kind", "record_id", "state"),
        Index("ix_cr_user_state", "user_id", "state"),
    )


class Comment(Base):
    """挂在一条 record 上的一条评论。

    ``owner_id`` = **record 的 owner**（不是作者）。这样「一条记录上的所有评论
    都归同一个归属域」是可查询的，隔离测试可以断言「换一个 owner 就查不到」。
    """

    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    #: record 的 owner —— 归属/隔离谓词所依据的列。
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    record_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 作者身份（owner_id 或 service_id）。
    author_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: 解析并**过滤后**的提及列表（只含对该 record 有可见权限的人）。
    mentions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: 软删除。保留行以便审计与「这段对话曾经存在」的可追溯性。
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "record_kind IN ('task','canvas','artifact','memory')", name="ck_comment_kind"
        ),
        CheckConstraint("length(body) > 0", name="ck_comment_body_nonempty"),
        CheckConstraint("length(record_id) > 0", name="ck_comment_record_nonempty"),
        CheckConstraint("version >= 1", name="ck_comment_version_positive"),
        Index("ix_comment_record_created", "record_kind", "record_id", "created_at"),
        Index("ix_comment_owner_record", "owner_id", "record_kind", "record_id"),
    )


class Notification(Base):
    """站内通知。第一切片只承载 ``@`` 提及。

    ``owner_id`` = **收件人**（隔离谓词列）。**不存评论正文**：``summary`` 是
    一句不携带任何评论内容的定位串（谁、在哪条 record 上提到了你）。收件人要
    看原文时通过 ``comment_id`` 走评论接口取——那条路径会重新做一遍可见性校验，
    于是「通知泄露正文」在数据层与接口层都不成立。
    """

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    #: 收件人（owner 身份）。owner 隔离即按此列过滤。
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default=NOTIFICATION_MENTION)
    record_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    comment_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    author_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: **不含评论正文**的定位串。长度上限与评论正文无关。
    summary: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        # 白名单从 NOTIFICATION_KINDS 派生（单一真源），表达式见 NOTIFICATION_KIND_IN：
        # 当前渲染为 ``kind IN ('mention')``，与已落盘的 migration 0030 逐字相同。
        CheckConstraint(NOTIFICATION_KIND_IN, name="ck_notif_kind"),
        CheckConstraint("version >= 1", name="ck_notif_version_positive"),
        CheckConstraint("length(record_id) > 0", name="ck_notif_record_nonempty"),
        # 同一条评论对同一个人最多一条同类通知：重复解析 @ 幂等。
        UniqueConstraint("owner_id", "comment_id", "kind", name="uq_notif_recipient_comment"),
        Index("ix_notif_owner_read", "owner_id", "read_at"),
        Index("ix_notif_owner_created", "owner_id", "created_at"),
    )
