"""人工介入投票表决持久化（A-人工介入-03 · P17）。

HITL 主体（``db/hitl_models.py``）解决的是「一个人拍板选一个选项」。本模块解决
的是**上一层的争执**：多个 Agent 对同一个决定各执一词，谁的权重高都说不服谁，
于是把选项拿出来**投票**，票散完仍平手时由**用户定音**（需求原文「Agent 争执
用户定音」）。

三张表，形状与 ``hitl_interrupts`` 刻意不同：

1. ``hitl_vote_sessions`` —— 一次投票。**必须**挂在一条 ``hitl_interrupts`` 上
   （``interrupt_id``），因为投票的结论要回灌给那条中断，否则投票就是一个
   跟执行流脱钩的、谁也看不见的孤儿记录。``.interrupt_id`` 不做外键，理由与
   ``HitlInterrupt.execution_id`` 相同：HITL 挂在多个执行面上，硬 FK 会在
   其中一类上留下悬挂。
2. ``hitl_vote_candidates`` —— 候选人。**主键是 (session_id, option_value)**，
   同一个选项在同一场投票里只能有一个候选人（有名字有权重的实体，不是行）。
   票只能投给已登记的候选人——投给不存在的选项就是无效票，服务层拒。
3. ``hitl_vote_ballots`` —— 票根。**主键是 (session_id, voter)**，即一人一票。
   重复投票走 UPDATE 而不是插第二行，所以「票数」永远等于「投票人数」，
   不会出现一个人刷出十票。改票是允许的（人改主意很正常），但每次都进审计链。

状态机（写在 CHECK 里，不靠服务层自觉）::

    open ──► resolved   （有唯一最高票，或用户定音）
          └─► cancelled

    resolved 行**必须**有 winner_option；open 行**必须**没有。

权重用 ``Integer`` 而不是浮点：平手判定要精确相等，浮点会把「看起来一样」的
两个值判成不等，而「平手 → 用户定音」正是这个功能存在的理由。

本模块拥有自己的表并注册到共享 ``Base`` metadata，**不修改** ``db/models.py``
（沿用 ``db/hitl_models.py`` 的做法）。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow

#: ``open`` = 还在收票 / 等定音；``resolved`` = 有结论；``cancelled`` = 作废。
VOTE_STATUSES = ("open", "resolved", "cancelled")
#: 终态：已经有结论（或明确作废），不能再改票。
VOTE_CLOSED_STATUSES = ("resolved", "cancelled")
#: 会话「怎么结束的」。``tally`` = 票数自然胜出；``owner_tiebreak`` = 平手，
#: 用户定音；``cancelled`` = 投票被撤销（此时**没有**胜出选项）。
VOTE_RESOLVED_BY = ("tally", "owner_tiebreak", "cancelled")
#: 只有这两种结尾才有「谁赢」。
VOTE_WINNING_RESOLVED_BY = ("tally", "owner_tiebreak")

#: 单场投票的规模护栏。不是业务限制，是防止「一次请求塞进十万个候选人」把表
#: 撑爆；真要更大的场面应该分批。
MAX_CANDIDATES = 64
MAX_BALLOT_WEIGHT = 1000


class HitlVoteSession(Base):
    """一次投票。结论必须落回它所挂的那条 HITL 中断。"""

    __tablename__ = "hitl_vote_sessions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    #: 这次投票是为哪条中断开的。回灌时用它调 ``HitlInterruptService.decide``。
    interrupt_id: Mapped[str] = mapped_column(String(64), index=True)
    #: 冗余一份执行 id，方便按执行维度查「这场执行上发生过哪些争执」。
    execution_id: Mapped[str] = mapped_column(String(200), index=True)
    #: 给投票者看的问题（「这两条路线你站哪条」）。
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    #: 胜出选项。open 行必为 NULL，resolved 行必有值。
    winner_option: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``tally`` = 票数自然胜出；``owner_tiebreak`` = 平手，用户定音。
    resolved_by: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: 定音时的理由 / 审计补充。
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 平手时并列的选项（JSON 数组），让前端能直接渲染「二选一」而不是重算。
    tie_options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','resolved','cancelled')", name="ck_vote_status"
        ),
        # 三种结尾各自的状态形状，一次性说清（而不是给 cancelled 编一个假的
        # 胜出选项来骗过 CHECK）：
        #   open      → 没有结论
        #   resolved  → 有胜出选项，且说清是「票数决定」还是「用户定音」
        #   cancelled → 明确没有胜出选项
        CheckConstraint(
            "(status = 'open' AND winner_option IS NULL AND resolved_by IS NULL) "
            "OR (status = 'resolved' AND winner_option IS NOT NULL "
            "AND resolved_by IN ('tally','owner_tiebreak')) "
            "OR (status = 'cancelled' AND resolved_by = 'cancelled' "
            "AND winner_option IS NULL)",
            name="ck_vote_resolution_shape",
        ),
        CheckConstraint(
            "(status = 'open' AND closed_at IS NULL) OR "
            "(status <> 'open' AND closed_at IS NOT NULL)",
            name="ck_vote_closed_at_shape",
        ),
        CheckConstraint("version >= 1", name="ck_vote_version_positive"),
        Index("ix_hitl_vote_owner_status", "owner_id", "status"),
    )


class HitlVoteCandidate(Base):
    """一个候选选项。票只能投给已登记的候选。"""

    __tablename__ = "hitl_vote_candidates"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    option_value: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: 谁提出的这个选项（Agent id / 角色名）。用于事后追责「是谁要走的路」。
    proposer: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: 候选项的固有权重（例如「主线重构」比「改文案」重）。整数，见模块 docstring。
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("weight >= 0", name="ck_vote_candidate_weight_nonneg"),
        Index("ix_hitl_vote_candidate_session", "session_id"),
    )


class HitlVoteBallot(Base):
    """一张票根。一人一票，重复投票走 UPDATE。"""

    __tablename__ = "hitl_vote_ballots"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: 谁投的（Agent id / 角色名）。
    voter: Mapped[str] = mapped_column(String(200), primary_key=True)
    #: 投给了哪个候选人。服务层校验它在本场的 candidates 里；这里只保形。
    option_value: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 这一票的权重。整数权重让「平手」可精确判定。
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("weight >= 1", name="ck_vote_ballot_weight_positive"),
        CheckConstraint(f"weight <= {MAX_BALLOT_WEIGHT}", name="ck_vote_ballot_weight_cap"),
        Index("ix_hitl_vote_ballot_session", "session_id"),
    )