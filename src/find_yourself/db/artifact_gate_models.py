"""产物版本门禁的数据模型（需求 7）。

两张表，把「产物」这件事拆成**可版本化、可冻结、可验证**的三段：

- :class:`ArtifactVersion` —— 「某个产物的第 N 版」。关键决定：
  **一版的��容在创建时就冻结**（``content_digest`` 由真实字节算出）。改内容
  不是「改这一版」，而是**建新的一版**。这条纪律是整个门禁能挡住东西的
  地基：若一版的内容可以悄悄变，那么「验证通过」证明的是哪个版本就成了
  不可知���，TOCTOU 直接把门禁变成装饰。
- :class:`ArtifactGateCheck` —— 一次独立检查的**证据**（不是布尔开关）。
  它记下检查当时**实际观测到的摘要**（``observed_digest``）与命令、退出码。
  判定时会重核这份证据是否仍与当前版本的 ``content_digest`` 一致——
  这让「先验证、后偷改内容」在读取侧被再次挡住。

**为什么必检项由服务端策略决定，而不是调用方声明**
--------------------------------------------------
本表最容易被绕过的一处是「这个产物需要满足哪些条件才算合格」。如果由调用方
在创建版本时传``required_checks=[]``，那么任何执行体都能通过「声明无需检查」
拿到一个「零条件即合格」的版本——门禁当场退化成装饰。

所以必检项来自 :data:`GATE_POLICY`：一张**按产物形态**写死在服务端的白名单。
调用方能表达的只有「我要给这个产物跑什么**额外**检查」，不能表达「哪些检查
是**必须**的」。这条边界由
``tests/unit/test_artifact_gate.py::test_caller_cannot_downgrade_required_checks``
钉住。

由 schema 保证的不变量
---------------------
1. ``ck_agv_state`` —— 状态只能是六个白名单值之一。
2. ``uq_agv_artifact_version`` —— 同一产物的同一版号只有一行。
3. ``ck_agv_decided_shape`` —— 非 ``draft`` 的行必须带 ``decided_at``，
   ``draft`` 的行必须不带。
4. ``ck_agv_submitted_shape`` —— 非 ``draft`` 的行必须真的被提交过
   （``submitted_at`` 非空）。没有这条，一个「未经提交就verified」的行能落库。
5. ``ck_agv_positive_version`` / ``ck_agv_positive_version_no`` —— 乐观锁与
   版号都从 1 起。
6. ``ck_agc_status`` + ``uq_agc_version_check`` —— 检查项状态白名单，且
   「一版一项」只留一行（重跑即覆盖，证据不并列，避免「取最新」这种
   需要额外规则才能不出错的语义）。
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

#: 受门禁管理的产物形态。刻意是**白名单**而不是任意字符串：一个新形态
#: 要先在这里立项、被赋予一套必检项，才谈得上「受门禁管理」。没有默认
#: 形态，就没有「忘了配策略」的产物。
ARTIFACT_KINDS = ("asset", "code_bundle")

#: 产物版本的状态机取值。
#:
#: - ``draft``      —— 已登记，内容已冻结，但还没提交检查。
#: - ``in_review``  —— 已提交，等待独立检查出结果。
#: - ``verified``   —— 全部必检项通过（可用，尚不可对外）。
#: - ``released``   —— 已放行（可用且可对外）。
#: - ``rejected``   —— 有必检项失败，或复核人明确否决。终态。
#: - ``blocked``    —— 被扣住（复核后发现问题、或被更高层叫停）。终态。
ARTIFACT_STATES = ("draft", "in_review", "verified", "released", "rejected", "blocked")

#: 检查项的结论。只有两个值——**没有**「跳过」。
#:
#: 「跳过」看起来方便，但它让「没跑检查」和「检查通过」在库里长得一样，
#: 而门禁最需要区分的恰恰是这两者。
CHECK_STATUSES = ("passed", "failed")

#: 必检项白名单（服务端策略，见模块 docstring）。
KNOWN_CHECKS = ("content_digest", "syntax_valid", "independent_test")

#: 每种产物形态的**必检项**。这张表是门禁的判据来源，调用方无法削弱它。
GATE_POLICY: dict[str, tuple[str, ...]] = {
    # 资产：字节本体必须对得上（防「验完偷改」），并且要真的过一道独立检查。
    "asset": ("content_digest", "independent_test"),
    # 代码包：在资产那两条之上再加语法检查——语法都过不了的代码包，
    # 让它去跑「独立测试」本身就是把失败的命令当成证据。
    "code_bundle": ("content_digest", "syntax_valid", "independent_test"),
}

#: 状态机：``{当前状态: {动作: 目标状态}}``。
#:
#: 显式枚举而不是「校验目标状态是否在某个集合里」，因为**同一个目标状态
#: 从不同起点到达，合法性不同**：``in_review → released`` 必须非法（跳过验证
#: 直接放行），而 ``verified → released`` 必须合法。写成目标态白名单
#: （``released in TERMINAL_OK``）会让前者变成合法——那正是门禁最该挡的
#: 一次跳跃。
GATE_TRANSITIONS: dict[str, dict[str, str]] = {
    # 提交：登记 -> 送检。不检查任何条件（此时还没有证据可查）。
    "draft": {"submit": "in_review"},
    # 判定：出结果。verify_pass 要求全部必检项通过，否则 verify_fail 落rejected。
    "in_review": {"verify_pass": "verified", "verify_fail": "rejected", "block": "blocked"},
    # 放行：只有已验证的才可能放行；也可直接扣住。
    "verified": {"release": "released", "block": "blocked"},
    # 已放行的仍可被收回（发现问题时的止损口）。
    "released": {"block": "blocked"},
    # 两个终态：不可再动。要再动只能建新的一版。
    "rejected": {},
    "blocked": {},
}

#: 终态：这两个状态没有任何出边。
TERMINAL_ARTIFACT_STATES = ("rejected", "blocked")

#: 「可用」的状态集合——门禁判定 ``usable`` 时用它。
USABLE_ARTIFACT_STATES = ("verified", "released")


def required_checks_for(kind: str) -> tuple[str, ...]:
    """该产物形态的必检项。

    形态不在白名单里 → :class:`ValueError`（由服务层转成 422）。
    刻意不返回空元组：**没有默认策略**。一个未知形态若悄悄拿到空必检项，
    就会变成「零条件即合格」——那与本模块存在的理由正好相反。
    """
    try:
        return GATE_POLICY[kind]
    except KeyError:
        raise ValueError(f"no gate policy for artifact kind {kind!r}") from None


class ArtifactVersion(Base):
    """"某个产物的第 N 版"。

    一行 = 一个不可变的内容快照 + 它的门禁状态。**内容冻结**是这张表的
    语义核心：``content_digest`` 在创建时由真实字节算出，之后不再更新。
    任何内容变化都必须表达成新的一行（新 ``version_no``）。

    ``state`` 是本表持有的状态，与 :mod:`find_yourself.services.hitl` /
    :mod:`find_yourself.services.team_approval` 无关——门禁管的是**产物**的
    合格性，不是一次执行的挂起。那两者各自有表，不合并（合并会出现两个
    真相源：HITL 认为已批准、门禁认为待验证，而没有约束能阻止分叉）。
    """

    __tablename__ = "artifact_versions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    #: asset | code_bundle。白名单由 ``ck_agv_kind`` 兜底。
    artifact_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 产物在其本形态表里的 id（``assets.id`` /代码包的标识）。刻意**不是**
    #: 外键：门禁要能覆盖到尚无对应行的形态（先立版本、后落本体），
    #: 写成外键会让「先登记后产出」这条正常路径直接不可表达。
    artifact_id: Mapped[str] = mapped_column(String(200), index=True)
    #: 第几版，从 1 起。
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: 这一版内容的摘要（真实字节算出，创建后不变）。检查证据会与它比对。
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 字节所在位置，供检查器重算摘要/ 跑命令。``{"dir": ..., "files": [...]}``。
    #: 相对路径，绝不存绝对路径（同assets 的纪律）。
    workspace_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    #: 提交送检的时刻；``draft`` 之外必须有。
    submitted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: 判定/放行/扣住的时刻；``draft`` 必须为空。
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: 谁做的判定/放行/扣住。留痕：门禁结论需要能追责。
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "artifact_kind IN ('asset','code_bundle')", name="ck_agv_kind"
        ),
        CheckConstraint(
            "state IN ('draft','in_review','verified','released','rejected','blocked')",
            name="ck_agv_state",
        ),
        CheckConstraint("version >= 1", name="ck_agv_positive_version"),
        CheckConstraint("version_no >= 1", name="ck_agv_positive_version_no"),
        # 摘要必须是 64 位十六进制。不校验它等于让「证据比对」退化成
        # 字符串相等——一个空摘要能和另一个空摘要「相等」。
        CheckConstraint(
            "length(content_digest) = 64", name="ck_agv_digest_is_sha256"
        ),
        # draft 必须「未提交、未判定」；其余状态必须「已提交、已判定」。
        # 这两条合起来表达：「不存在一个没经过 submit 就变成verified 的行」。
        CheckConstraint(
            "(state <> 'draft' AND decided_at IS NOT NULL) OR "
            "(state = 'draft' AND decided_at IS NULL)",
            name="ck_agv_decided_shape",
        ),
        CheckConstraint(
            "(state <> 'draft' AND submitted_at IS NOT NULL) OR "
            "(state = 'draft' AND submitted_at IS NULL)",
            name="ck_agv_submitted_shape",
        ),
        # 一版的定义就是「同一产物的同一个版号」，不能有两行。
        UniqueConstraint(
            "artifact_kind", "artifact_id", "version_no", name="uq_agv_artifact_version"
        ),
        Index("ix_agv_owner_kind", "owner_id", "artifact_kind"),
        Index("ix_agv_artifact", "artifact_kind", "artifact_id"),
        Index("ix_agv_state", "state"),
    )


class ArtifactGateCheck(Base):
    """一次独立检查的**证据**。

    刻意不是 ``ArtifactVersion`` 上的一个布尔列：布尔列只能回答「过了没」，
    而门禁要能回答「**凭什么**过的」——命令是什么、退出码多少、当时观测到的
    摘要是什么。``observed_digest`` 尤其关键：判定时会核对它是否仍等于版本
    的 ``content_digest``，所以「先跑检查通过、再改内容」在**读取侧**也会被
    挡住，而不只是写入侧。

    ``(artifact_version_id, check_name)`` 唯一：重跑覆盖旧证据。留着多行会
    逼着判定逻辑去回答「取哪一行」，而那个问题一旦答错（比如取任意一行）
    就会让一份陈旧的通过证据复活。
    """

    __tablename__ = "artifact_gate_checks"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    artifact_version_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="CASCADE"), index=True
    )
    #: content_digest | syntax_valid | independent_test |调用方自定的额外项
    check_name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: passed | failed。没有「跳过」。
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 检查当时**实际**观测到的摘要。对 ``content_digest`` 这一项，
    #: 它是重算出来与版本摘要比对的结果，不是抄版本上的值——抄上去就等于
    #: 自己判自己及格。
    observed_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 真实执行痕迹：命令、退出码、耗时、stdout/stderr 摘要。
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ran_by: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint("status IN ('passed','failed')", name="ck_agc_status"),
        CheckConstraint("version >= 1", name="ck_agc_positive_version"),
        CheckConstraint(
            "observed_digest IS NULL OR length(observed_digest) = 64",
            name="ck_agc_observed_digest_is_sha256",
        ),
        UniqueConstraint(
            "artifact_version_id", "check_name", name="uq_agc_version_check"
        ),
        Index("ix_agc_version_status", "artifact_version_id", "status"),
    )
