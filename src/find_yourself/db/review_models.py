"""P9 · 点哪评哪数据表（A-点哪评哪-05/06/07/08/09/10 + A-路线-03）。

评审意见**落库而非只躺在 localStorage**（这是本包的核心升级）：原来的
``web/src/components/ReviewMode.tsx`` 把意见存在浏览器本地，换台机器/换个浏览器
就没了，执行 Agent 也拿不到。落库后：

* 同一条意见带**服务端 id**，可被后端渲染成 Markdown 交给执行 Agent；
* 支持**跨设备**读取（同一 owner 的所有浏览器看到同一份意见）；
* 为 A-点哪评哪-05 的热刷新闭环提供「改完 → 意见状态回写」的服务端锚点。

两张表：

* ``review_sessions`` —— 一次评审会话（一次「点哪评哪」的记录区间）。
  承载 A-路线-03 的双路线标记（白盒 / Computer Use 兜底）与当前迭代轮次。
* ``review_notes`` —— 单条意见。承载三类目标定位：
  - 桌面点选（原有能力）：``selector`` + ``dom_path``
  - 圈选区域（A-点哪评哪-06）：``region``（归一化坐标矩形）
  - 画笔批注（A-点哪评哪-07）：``strokes``（画布笔迹）+ ````audio_ref``（语音附件）

TOKEN 优化三项也落在本表：
* A-点哪评哪-08 真写标记 —— ``marker``（二十来字符的短标记，替代整段选择器）
* A-点哪评哪-09 短代码哈希表 —— ``short_code``（意见的短码，会话内唯一）
* A-点哪评哪-10 视觉/DOM 差分 —— ``dom_digest``（该元素提交时的 DOM 指纹，
  配合 ``prev_dom_digest`` 判断「改完是否真的变了」）
"""

from datetime import datetime

from sqlalchemy import Index, Integer, JSON, String, Text, text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow


class ReviewSession(Base):
    """一次评审会话（A-点哪评哪-05 · A-路线-03）。"""

    __tablename__ = "review_sessions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: 被评审的页面路由（``window.location.pathname``）
    page: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: A-路线-03：白盒（default）/ computer_use（兜底）
    route: Mapped[str] = mapped_column(String(30), nullable=False, default="whitebox")
    #: 迭代轮次——每完成一轮「改完自动刷新」自增，用于闭环计数（A-点哪评哪-05）
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: 热刷新闭环状态：idle / refreshing / applied / failed
    refresh_state: Mapped[str] = mapped_column(String(20), nullable=False, default="idle")
    #: 最近一次热刷新的时间与说明（失败时写明原因，不假装成功）
    refreshed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    refresh_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_review_sessions_owner_created", "owner_id", "created_at"),
        Index("ix_review_sessions_page", "page"),
    )


class ReviewNote(Base):
    """单条「点哪评哪」意见（A-点哪评哪-05/06/07/08/09/10）。"""

    __tablename__ = "review_notes"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    page: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    # --- 目标定位（三种玩法共用）---
    #: 定位方式：dom（桌面点选）/ region（圈选区域）/ freehand（画笔批注）
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="dom")
    #: 元素标签 / id / class（dom 模式；圈选模式可为空）
    tag: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    element_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    element_class: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    #: 元素可见文本摘要（截断，避免整段入 token）
    text: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    #: CSS 选择器（dom 模式）
    selector: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: A-点哪评哪-10：DOM 路径（自 body 起的有序段），供结构化差分
    dom_path: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: A-点哪评哪-06：归一化圈选区域 {x,y,w,h}（0~1，相对视口），跨分辨率稳定
    region: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: A-点哪评哪-07：画布笔迹 [{points:[{x,y}...],color,width}]（同上归一化）
    strokes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: A-点哪评哪-07：语音/音频附件引用（存 storage key，不存二进制）
    audio_ref: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    #: 语音转写文本（有则一并落库，供无音频环境下的执行 Agent 使用）
    audio_transcript: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # --- 意见正文 ---
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # --- TOKEN 优化三项 ---
    #: A-点哪评哪-08 真写标记：二十来字符的短标记（``<button#save>`` 之类），
    #: 给 Agent 看这个比看整条选择器省 token
    marker: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    #: A-点哪评哪-09 短代码哈希表：会话内唯一的短码（``r3``），引用时可只写短码
    short_code: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    #: A-点哪评哪-10 当前 DOM 指纹（sha256 前 16 位）与上次指纹，用于差分
    dom_digest: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    prev_dom_digest: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    #: 意见状态：open（待改）/ resolved（已改完）/ dismissed（不采纳）
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    #: A-点哪评哪-05：本条意见在最近一轮热刷新后是否被确认已落地
    applied_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        # A-点哪评哪-09：短码在**同一会话内唯一**。用**部分唯一索引**（仅约束
        # short_code <> ''），这样未分配短码的行（空串默认值）不会互相冲突——
        # 普通 UNIQUE 会让所有空串行撞车。
        Index(
            "uq_review_notes_session_short_code", "session_id", "short_code",
            unique=True,
            sqlite_where=sa_text("short_code <> ''"),
            postgresql_where=sa_text("short_code <> ''"),
        ),
        Index("ix_review_notes_session_created", "session_id", "created_at"),
        Index("ix_review_notes_owner_state", "owner_id", "state"),
    )
