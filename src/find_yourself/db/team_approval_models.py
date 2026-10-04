"""团队级审批的数据模型（需求 6）。

三张表，刻意只表达「谁能批、批给谁、多人协作怎么流转」，**不含**组织管理
（建组/审批/邀请/组织树属需求 13，不在这里）：

- :class:`ApprovalTeam` —— 一个审批域。它只回答「这批申请归谁管」，
  不回答「这个团队是干什么的」。
- :class:`ApprovalTeamMember` —— 谁在这个团队里，以及他在队里是
  ``requester``（只能提申请）、``approver``（能拍板）还是两者兼具
  （``member`` = 可提可批）。角色用 **bitmask 式的 role字符串** 而不是
  两个布尔列：``requester`` / ``approver`` 两个正交能力，用 ``member``
  表示「都要」，避免出现「既不是申请者也不能批」的第三种无意义状态。
- :class:`TeamApprovalRequest` —— 一次申请。它**不自己存状态**：
  ``interrupt_id`` 指向 :class:`~find_yourself.db.hitl_models.HitlInterrupt`，
  审批的等待中/已决策状态机完全由 HITL 那张表负责（见
  :class:`~find_yourself.services.hitl.HitlInterruptService`）。本表只加
  团队维度：这条申请归哪个团队、谁提的、该走几个决策选项。

为什么审批状态不复制一份
------------------------
「等待中 vs 已决策」已经是 ``hitl_interrupts`` 的 **schema 级不变量**
（``ck_hitl_decided_shape``）。这里若再存一个 ``status``，就会出现两个真相：
HITL 认为已批准、审批表认为待批，而没有任何约束能阻止这种分叉。所以
``team_approval_requests`` 刻意**没有 status 列**——需要状态时一律读
HITL 那一行。团队维度是**加法**（谁有资格批），不是**替代**。

由schema 保证的三条不变量
-------------------------
1. ``ck_atm_one_role`` —— role 只能是白名单里的三值之一。
2. ``uq_atm_team_member`` —— 一个人在同一个团队里只有一行（不能既是
   approver 又是另一个 approver 的重复行）。
3. ``ck_tar_round_positive`` —— 轮次从 1 起。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow

#: 团队内角色。``requester`` 只能提申请，``approver`` 能拍板，
#: ``member`` 两者皆可（绝大多数人属于这一档）。
TEAM_ROLES = ("requester", "approver", "member")
#: 成员行状态。``revoked`` 保留行而不是删除：审批记录要能解释
#: 「当时这个人为什么有资格批」，删掉行就变成无法追溯的历史。
MEMBER_STATES = ("active", "revoked")
TEAM_STATES = ("active", "archived")


class ApprovalTeam(Base):
    """一个审批域。只表达归属，不表达组织结构。"""

    __tablename__ = "approval_teams"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    #: 建立者。它只是「建组人」，不是「当然的审批人」——审批资格一律
    #: 来自 ``approval_team_members``，避免「谁建了组谁能批」这种隐式授权。
    created_by: Mapped[str] = mapped_column(String(200), index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(f"state IN {TEAM_STATES}", name="ck_at_state"),
        CheckConstraint("version >= 1", name="ck_at_version_positive"),
    )


class ApprovalTeamMember(Base):
    """团队成员及其审批资格。

    ``user_id`` 就是 :class:`~find_yourself.services.actor.Actor` 里的
    ``owner_id``——系统里每个真人主体都已经是 owner 身份，团队角色是
    **叠加在**这个身份之上的能力，不是另立一套主体（对应架构文档
    §13「当前全系统的权利主体只有 owner 与 service identity」）。
    """

    __tablename__ = "approval_team_members"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("approval_teams.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    #: 谁给的资格。审批权是显式授予的，所以来源必须留痕。
    granted_by: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(f"role IN {TEAM_ROLES}", name="ck_atm_one_role"),
        CheckConstraint(f"state IN {MEMBER_STATES}", name="ck_atm_state"),
        CheckConstraint("version >= 1", name="ck_atm_version_positive"),
        # 同一个人在同一个团队里只能有一行：资格是集合，不是可重复的授予。
        UniqueConstraint("team_id", "user_id", name="uq_atm_team_member"),
        Index("ix_atm_team_role", "team_id", "role", "state"),
    )


class TeamApprovalRequest(Base):
    """一次团队审批申请。

    状态不落在这里——见模块 docstring。``interrupt_id`` 是外键指向
    ``hitl_interrupts.id``：审批的等待中/已决策由那一行说了算，本表加的是
    「这条申请归哪个团队、谁能批它」。

    ``required_role`` 记录申请时**声明的**门槛。资格在拍板那一刻按当时的
    成员行判定（而不是调用方自称的角色），但把声明值存下来，审计时才能
    回答「他申请时要的是什么级别」——成员行之后可以被改，只存快照会让
    「当时按谁判的」无从查证。
    """

    __tablename__ = "team_approval_requests"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("approval_teams.id", ondelete="CASCADE"), index=True
    )
    #: 指向 HITL 中断行：等待中/已决策、决策值、超时全在那一行。
    interrupt_id: Mapped[str] = mapped_column(
        ForeignKey("hitl_interrupts.id", ondelete="CASCADE"), unique=True, index=True
    )
    requester_id: Mapped[str] = mapped_column(String(200), index=True)
    #: 申请时声明的审批门槛，白名单同 ``TEAM_ROLES``。
    required_role: Mapped[str] = mapped_column(String(16), nullable=False, default="approver")
    #: 第几轮。退回修改后重新提交会 +1，让「改了几轮」可查。
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: 申请正文。刻意不进 hash 链（那是 HITL 的 context 职责），
    #: 这里只放给人看的说明。
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: 上一次被退回的申请 id；首轮为None。
    supersedes_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(f"required_role IN {TEAM_ROLES}", name="ck_tar_required_role"),
        CheckConstraint("round >= 1", name="ck_tar_round_positive"),
        CheckConstraint("version >= 1", name="ck_tar_version_positive"),
        # 注意：「审批人不能审自己」这条**不能**在这里用 CHECK 表达。
        # 决策人落在 ``hitl_interrupts.decided_by``，与requester_id 分属两行，
        # 跨表不变式不是 SQLite/Postgres 的 CHECK 能读的东西（CHECK 只能读
        # 同一行）。伪造一个恒真的 CHECK 只会让人误以为有表级兜底。
        # 真正执行它的是 :meth:`TeamApprovalService._require_eligible_approver`
        # 里的显式判定，且那条判定在拍板的前一刻、拿着成员行做。
        Index("ix_tar_team_requester", "team_id", "requester_id"),
    )


#: 团队审批提供的决策选项。HITL 的 ``_status_for`` 只显式认识
#: ``cancel/cancelled/abort``（→cancelled）与
#: ``approve/approved/continue/accept``（→approved），其余一律 rejected。
#: 所以「退回修改」用 ``return_for_change``：它会落到 rejected 终态
#: （保守默认正确——没批就是没批），而本表靠 ``round``/``supersedes_id``
#: 记录「这不是终审，是打回重提」。
TEAM_DECISIONS = ("approve", "reject", "return_for_change")
