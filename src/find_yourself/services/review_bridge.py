"""P9 · 点哪评哪桥接服务（A-点哪评哪-05/06/07/08/09/10 + A-路线-03）。

需求现场
--------

原来 ``web/src/components/ReviewMode.tsx`` 的评审意见**只活在 localStorage**：
换浏览器就丢、执行 Agent 拿不到、也没法追踪「这条意见改完没有」。本服务把
它升级成**服务端一等公民**，同时补齐三种进阶玩法与三项 TOKEN 优化。

本模块**不重建**前端点选（已存在），只做桥接：

* 前端把「选中了什么 + 意见内容」POST 上来 → 落库为 ``review_notes``；
* 后端把全部意见渲染成**紧凑 Markdown**（给执行 Agent 照单改造）；
* 记录热刷新闭环状态（A-点哪评哪-05 的「改完自动刷新」服务端锚点）。

设计约束（违反即返工）
----------------------

1. **落库为准，localStorage 只作离线缓存**——写入必须先过服务端；服务端不可达时
   前端保留内存态并**显式提示未同步**，绝不假装已保存。
2. **坐标归一化**——圈选/笔迹一律存 ``0~1`` 相对视口坐标（A-点哪评哪-06/07），
   跨分辨率、跨设备可复原；存像素会随窗口大小漂移。
3. **短码会话内唯一**——A-点哪评哪-09 用**写时分配**：``session_id`` 下自增
   ``r1,r2,…``，冲突时重试。唯一性由 DB 部分唯一索引兜底。
4. **差分不猜**——A-点哪评哪-10 的 ``changed`` 判定基于**真实指纹比对**
   （``dom_digest`` vs ``prev_dom_digest``），不回显调用方传入的期望值。
5. **诚实边界**：语音只存**附件引用与转写文本**，不做语音识别（那需外部服务，
   属 BLOCKED_EXTERNAL）；画笔只存**矢量笔迹**，不存位图截图。
6. **双路线显式标记**——A-路线-03 的 ``route`` 必须显式写入；Computer Use 是
   **兜底**而非默认，切换需记明理由（``refresh_note``），不静默降级。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.review_models import ReviewNote, ReviewSession
from ..db.types import utcnow
from .actor import Actor
from .audit import AuditService
from .errors import NotFound, ValidationFailed

# ---------------------------------------------------------------------------
# 常量（单一定义处；DB CHECK 不约束这几列，避免迁移锁死枚举）
# ---------------------------------------------------------------------------

#: A-路线-03 双路线：白盒为主（DOM 精确定位）
ROUTE_WHITEBOX = "whitebox"
#: A-路线-03 双路线：Computer Use 兜底（白盒定位不到时）
ROUTE_COMPUTER_USE = "computer_use"
ROUTES = (ROUTE_WHITEBOX, ROUTE_COMPUTER_USE)

#: 定位方式
MODE_DOM = "dom"            # 桌面点选（原有能力）
MODE_REGION = "region"      # A-点哪评哪-06 圈选区域
MODE_FREEHAND = "freehand"  # A-点哪评哪-07 画笔/语音批注
MODES = (MODE_DOM, MODE_REGION, MODE_FREEHAND)

#: 意见状态
NOTE_OPEN = "open"
NOTE_RESOLVED = "resolved"
NOTE_DISMISSED = "dismissed"
NOTE_STATES = (NOTE_OPEN, NOTE_RESOLVED, NOTE_DISMISSED)

#: 热刷新闭环状态（A-点哪评哪-05）
REFRESH_IDLE = "idle"
REFRESH_REFRESHING = "refreshing"
REFRESH_APPLIED = "applied"
REFRESH_FAILED = "failed"
REFRESH_STATES = (REFRESH_IDLE, REFRESH_REFRESHING, REFRESH_APPLIED, REFRESH_FAILED)


# ---------------------------------------------------------------------------
# 纯函数：TOKEN 优化三项（A-点哪评哪-08/09/10）—— 独立可测，不碰 DB
# ---------------------------------------------------------------------------


def domain_digest(dom_path: Iterable[str], tag: str = "", cls: str = "") -> str:
    """A-点哪评哪-10：DOM 指纹（sha256 前 16 位）。

    指纹由**结构化输入**拼算（路径段 + 标签 + class），不含文本内容——
    文本一变就换指纹会让「改文案」误判为「结构变了」。
    """
    parts = list(dom_path)
    payload = "|".join([*parts, tag.strip().lower(), _normalize_cls(cls)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_cls(cls: str) -> str:
    return ".".join(sorted(c for c in re.split(r"\s+", cls.strip()) if c))


def build_marker(tag: str, element_id: str = "", cls: str = "") -> str:
    """A-点哪评哪-08：真写标记——**二十来字符**的短标记。

    比整条 CSS 选择器（动辄上百字符）省 token；形如 ``<button#save.primary>``。
    超长 class 只取前两个，确保结果稳定地落在 ~30 字符内。
    """
    seg = f"<{tag.strip().lower() or 'el'}"
    if element_id:
        seg += f"#{element_id.strip()}"
    classes = [c for c in re.split(r"\s+", cls.strip()) if c][:2]
    if classes:
        seg += "." + ".".join(classes)
    seg += ">"
    return seg[:60]


def short_code_for(seq: int) -> str:
    """A-点哪评哪-09：第 ``seq`` 条意见的短码（``r1`` / ``r2`` …）。

    短码让「引用某条意见」只需两三个字符（``见 r3``），不必重述选择器。
    """
    if seq < 1:
        raise ValidationFailed("invalid_short_code_seq", "短码序号必须 >= 1")
    return f"r{seq}"


def region_is_normalized(region: dict) -> bool:
    """A-点哪评哪-06：圈选区域是否已归一化（0~1 且 w/h 为正）。"""
    try:
        x, y = float(region["x"]), float(region["y"])
        w, h = float(region["w"]), float(region["h"])
    except (KeyError, TypeError, ValueError):
        return False
    return all(0.0 <= v <= 1.0 for v in (x, y, w, h)) and w > 0 and h > 0


# ---------------------------------------------------------------------------
# 数据载体
# ---------------------------------------------------------------------------


@dataclass
class NoteDraft:
    """一条待落库的意见（前端 POST 上来的内容）。"""

    page: str = ""
    mode: str = MODE_DOM
    tag: str = ""
    element_id: str = ""
    element_class: str = ""
    text: str = ""
    selector: str = ""
    dom_path: list[str] = field(default_factory=list)
    region: dict = field(default_factory=dict)
    strokes: list[dict] = field(default_factory=list)
    audio_ref: str = ""
    audio_transcript: str = ""
    note: str = ""


@dataclass
class NoteView:
    """一条意见的对外视图（前端与 Markdown 渲染共用）。"""

    id: str
    session_id: str
    page: str
    mode: str
    tag: str
    element_id: str
    element_class: str
    text: str
    selector: str
    dom_path: list[str]
    region: dict
    strokes: list[dict]
    audio_ref: str
    audio_transcript: str
    note: str
    marker: str
    short_code: str
    dom_digest: str
    prev_dom_digest: str
    state: str
    applied_at: datetime | None
    created_at: datetime

    @property
    def changed(self) -> bool:
        """A-点哪评哪-10：该元素自上次提交以来**结构是否真的变了**。

        只在两次指纹都非空且不相等时为真——缺指纹时**不猜**（返 False），
        宁可不报也不误报。
        """
        return bool(self.dom_digest and self.prev_dom_digest
                    and self.dom_digest != self.prev_dom_digest)

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "page": self.page,
            "mode": self.mode,
            "tag": self.tag,
            "element_id": self.element_id,
            "element_class": self.element_class,
            "text": self.text,
            "selector": self.selector,
            "dom_path": self.dom_path,
            "region": self.region,
            "strokes": self.strokes,
            "audio_ref": self.audio_ref,
            "audio_transcript": self.audio_transcript,
            "note": self.note,
            "marker": self.marker,
            "short_code": self.short_code,
            "dom_digest": self.dom_digest,
            "prev_dom_digest": self.prev_dom_digest,
            "changed": self.changed,
            "state": self.state,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "created_at": self.created_at.isoformat(),
        }


def _to_view(row: ReviewNote) -> NoteView:
    return NoteView(
        id=row.id, session_id=row.session_id, page=row.page, mode=row.mode,
        tag=row.tag, element_id=row.element_id, element_class=row.element_class,
        text=row.text, selector=row.selector, dom_path=list(row.dom_path or []),
        region=dict(row.region or {}), strokes=list(row.strokes or []),
        audio_ref=row.audio_ref, audio_transcript=row.audio_transcript,
        note=row.note, marker=row.marker, short_code=row.short_code,
        dom_digest=row.dom_digest, prev_dom_digest=row.prev_dom_digest,
        state=row.state, applied_at=row.applied_at, created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Markdown 渲染（给执行 Agent 照单改造）
# ---------------------------------------------------------------------------


def render_markdown(session: ReviewSession, notes: list[NoteView]) -> str:
    """把一次评审会话渲染成**紧凑 Markdown**（这是给执行 Agent 的最终产物）。

    紧凑设计（对冲 TOKEN 优化三项）：
    * 每条用 ``短码`` 起头，正文用 ``marker``（二十来字符）而非全选择器；
    * 选择器放进反引号，Agent 需要时可展开；简短模式（``compact=True``）下省略；
    * 圈选/画笔意见给出归一化坐标与笔迹点数，不做像素换算。
    """
    lines: list[str] = [
        f"# 点哪评哪 · 评审意见（{session.page}）",
        "",
        f"- 会话：`{session.id}`",
        f"- 路线：{session.route}" + ("（白盒）" if session.route == ROUTE_WHITEBOX else "（Computer Use 兜底）"),
        f"- 迭代轮次：第 {session.iteration} 轮 · 热刷新：{session.refresh_state}",
        f"- 意见总数：{len(notes)}（待改 "
        f"{sum(1 for n in notes if n.state == NOTE_OPEN)} 条）",
        "",
    ]
    if not notes:
        lines.append("_本轮暂无意见。_")
        return "\n".join(lines)

    for n in notes:
        state_zh = {NOTE_OPEN: "待改", NOTE_RESOLVED: "已改完", NOTE_DISMISSED: "不采纳"}.get(n.state, n.state)
        head = f"## [{n.short_code}] {n.marker} · {state_zh}"
        lines.append(head)
        if n.mode == MODE_REGION and n.region:
            r = n.region
            lines.append(
                f"- 定位：圈选区域 x={_pct(r.get('x'))} y={_pct(r.get('y'))} "
                f"w={_pct(r.get('w'))} h={_pct(r.get('h'))}（归一化 0~1）"
            )
        elif n.mode == MODE_FREEHAND:
            pts = sum(len(s.get("points") or []) for s in n.strokes)
            lines.append(f"- 定位：画笔批注（{len(n.strokes)} 笔 / {pts} 点）")
            if n.audio_ref:
                lines.append(f"- 语音附件：`{n.audio_ref}`")
            if n.audio_transcript:
                lines.append(f"- 语音转写：{n.audio_transcript}")
        else:
            lines.append(f"- 定位：DOM 点选 `{n.selector}`")
        if n.text:
            lines.append(f"- 元素文本：{n.text}")
        if n.changed:
            lines.append(f"- ⚠️ 结构已变：{n.prev_dom_digest} → {n.dom_digest}")
        lines.append(f"- 意见：{n.note.strip() or '（未填写）'}")
        lines.append("")
    return "\n".join(lines)


def _pct(v: Any) -> str:
    try:
        return f"{float(v):.3f}"
    except (TypeError, ValueError):
        return "?"


def render_compact(session: ReviewSession, notes: list[NoteView]) -> str:
    """A-点哪评哪-08/09 落地形态：**极简清单**——每条一行。

    格式：``[短码] marker = 意见``。这是 TOKEN 优化的对外可验证产物：
    相比 ``render_markdown`` 的逐条多行，短码 + marker 能省掉大部分定位描述。
    """
    if not notes:
        return f"# 点哪评哪（{session.page}）\n\n（无意见）"
    lines = [f"# 点哪评哪（{session.page}）· {len(notes)} 条"]
    for n in notes:
        flag = "!" if n.state == NOTE_OPEN else "v"
        lines.append(f"{flag}[{n.short_code}] {n.marker}={n.note.strip() or '（未填写）'}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------


class ReviewBridge:
    """点哪评哪的持久化桥接（A-点哪评哪-05/06/07/08/09/10 + A-路线-03）。

    典型用法::

        svc = ReviewBridge(session, audit)
        s = svc.open_session(actor, page="/chat", route=ROUTE_WHITEBOX)
        n = svc.add_note(actor, s.id, NoteDraft(page="/chat", tag="button",
                                               selector="body > div > button",
                                               note="按钮太大"))
        md = svc.export(s.id, actor)          # 给执行 Agent
        svc.mark_refreshed(actor, s.id, state=REFRESH_APPLIED, note="HMR 已热更新")
    """

    def __init__(self, session: Session, audit: AuditService) -> None:
        self.s = session
        self.audit = audit

    # -- 会话 ---------------------------------------------------------------

    def open_session(
        self,
        actor: Actor,
        *,
        page: str = "",
        title: str = "",
        route: str = ROUTE_WHITEBOX,
    ) -> ReviewSession:
        """开一次评审会话（A-路线-03 在此显式记路线，不静默默认）。"""
        if route not in ROUTES:
            raise ValidationFailed(
                "invalid_route",
                f"route 必须是 {ROUTES} 之一（A-路线-03 要求显式标记双路线）",
            )
        now = utcnow()
        row = ReviewSession(
            id=uuid.uuid4().hex,
            owner_id=self._owner(actor),
            page=page,
            title=title,
            route=route,
            iteration=1,
            refresh_state=REFRESH_IDLE,
            created_at=now,
            updated_at=now,
        )
        self.s.add(row)
        self.s.flush()
        self.audit.append(
            actor, "review.session_opened", target=row.id,
            details={"page": page, "route": route},
        )
        return row

    def get_session(self, session_id: str, actor: Actor) -> ReviewSession:
        row = self.s.get(ReviewSession, session_id)
        if row is None or row.owner_id != self._owner(actor):
            raise NotFound("review_session_not_found", "评审会话不存在或不属于当前主体")
        return row

    # -- 意见 ---------------------------------------------------------------

    def add_note(self, actor: Actor, session_id: str, draft: NoteDraft) -> NoteView:
        """新增一条意见（A-点哪评哪-06/07 的几何在此校验）。"""
        sess = self.get_session(session_id, actor)
        self._validate_draft(draft)

        seq = self._next_seq(session_id)
        now = utcnow()
        row = ReviewNote(
            id=uuid.uuid4().hex,
            session_id=session_id,
            owner_id=self._owner(actor),
            page=draft.page or sess.page,
            mode=draft.mode,
            tag=draft.tag,
            element_id=draft.element_id,
            element_class=draft.element_class,
            text=draft.text[:300],
            selector=draft.selector,
            dom_path=list(draft.dom_path),
            region=dict(draft.region),
            strokes=list(draft.strokes),
            audio_ref=draft.audio_ref,
            audio_transcript=draft.audio_transcript,
            note=draft.note,
            # A-点哪评哪-08：真写标记
            marker=build_marker(draft.tag, draft.element_id, draft.element_class),
            # A-点哪评哪-09：短代码哈希表
            short_code=short_code_for(seq),
            # A-点哪评哪-10：当前指纹；上次指纹来自同元素的前一条意见
            dom_digest=domain_digest(draft.dom_path, draft.tag, draft.element_class),
            prev_dom_digest=self._prev_digest(session_id, draft),
            state=NOTE_OPEN,
            created_at=now,
            updated_at=now,
        )
        self.s.add(row)
        try:
            self.s.flush()
        except IntegrityError as exc:  # 短码撞车（并发下极小概率）→ 显式重试提示
            self.s.rollback()
            raise ValidationFailed(
                "review_note_short_code_conflict",
                "短码在会话内冲突，请重试（A-点哪评哪-09 要求会话内唯一）",
            ) from exc
        self.audit.append(
            actor, "review.note_added", target=row.id,
            details={"session_id": session_id, "mode": row.mode, "short_code": row.short_code},
        )
        return _to_view(row)

    def list_notes(
        self, actor: Actor, session_id: str, *, state: str | None = None,
    ) -> list[NoteView]:
        self.get_session(session_id, actor)
        stmt = select(ReviewNote).where(ReviewNote.session_id == session_id)
        if state is not None:
            if state not in NOTE_STATES:
                raise ValidationFailed("invalid_state", f"state 必须是 {NOTE_STATES} 之一")
            stmt = stmt.where(ReviewNote.state == state)
        stmt = stmt.order_by(ReviewNote.created_at.asc(), ReviewNote.id.asc())
        return [_to_view(r) for r in self.s.execute(stmt).scalars().all()]

    def update_note(
        self, actor: Actor, note_id: str, *, note: str | None = None,
        state: str | None = None,
    ) -> NoteView:
        row = self._owned_note(actor, note_id)
        if note is not None:
            row.note = note
        if state is not None:
            if state not in NOTE_STATES:
                raise ValidationFailed("invalid_state", f"state 必须是 {NOTE_STATES} 之一")
            row.state = state
            # A-点哪评哪-05：标记「已改完」时落 applied_at，作为闭环的完成锚点
            row.applied_at = utcnow() if state == NOTE_RESOLVED else None
        row.updated_at = utcnow()
        self.s.flush()
        self.audit.append(actor, "review.note_updated", target=row.id,
                          details={"state": row.state})
        return _to_view(row)

    def delete_note(self, actor: Actor, note_id: str) -> None:
        row = self._owned_note(actor, note_id)
        self.s.delete(row)
        self.s.flush()
        self.audit.append(actor, "review.note_deleted", target=note_id)

    # -- 热刷新闭环（A-点哪评哪-05）------------------------------------------

    def mark_refreshed(
        self, actor: Actor, session_id: str, *, state: str, note: str = "",
    ) -> ReviewSession:
        """记录一次「改完自动刷新预览」的结果（A-点哪评哪-05）。

        ``state=applied`` 时轮次 +1（一轮闭环完成）；``failed`` 时**如实记录
        失败原因**，不推进轮次、不假装成功。
        """
        if state not in REFRESH_STATES:
            raise ValidationFailed("invalid_refresh_state",
                                   f"refresh_state 必须是 {REFRESH_STATES} 之一")
        sess = self.get_session(session_id, actor)
        sess.refresh_state = state
        sess.refresh_note = note
        sess.refreshed_at = utcnow()
        if state == REFRESH_APPLIED:
            sess.iteration += 1
        sess.updated_at = utcnow()
        self.s.flush()
        self.audit.append(
            actor, "review.refreshed", target=session_id,
            details={"state": state, "iteration": sess.iteration, "note": note},
        )
        return sess

    # -- 导出 ---------------------------------------------------------------

    def export(self, actor: Actor, session_id: str, *, compact: bool = False) -> str:
        """渲染 Markdown 给执行 Agent（``compact`` 走 TOKEN 优化形态）。"""
        sess = self.get_session(session_id, actor)
        notes = self.list_notes(actor, session_id)
        return render_compact(sess, notes) if compact else render_markdown(sess, notes)

    def session_summary(self, actor: Actor, session_id: str) -> dict[str, Any]:
        sess = self.get_session(session_id, actor)
        notes = self.list_notes(actor, session_id)
        return {
            "session_id": sess.id,
            "page": sess.page,
            "route": sess.route,
            "iteration": sess.iteration,
            "refresh_state": sess.refresh_state,
            "refresh_note": sess.refresh_note,
            "refreshed_at": sess.refreshed_at.isoformat() if sess.refreshed_at else None,
            "total": len(notes),
            "open": sum(1 for n in notes if n.state == NOTE_OPEN),
            "resolved": sum(1 for n in notes if n.state == NOTE_RESOLVED),
            "dismissed": sum(1 for n in notes if n.state == NOTE_DISMISSED),
        }

    # -- 内部 ---------------------------------------------------------------

    def _owner(self, actor: Actor) -> str:
        return actor.owner_id or actor.service_id or "anonymous"

    def _owned_note(self, actor: Actor, note_id: str) -> ReviewNote:
        row = self.s.get(ReviewNote, note_id)
        if row is None or row.owner_id != self._owner(actor):
            raise NotFound("review_note_not_found", "意见不存在或不属于当前主体")
        return row

    def _next_seq(self, session_id: str) -> int:
        existing = self.s.execute(
            select(func.count()).select_from(ReviewNote).where(ReviewNote.session_id == session_id)
        ).scalar_one()
        return int(existing) + 1

    def _prev_digest(self, session_id: str, draft: NoteDraft) -> str:
        """A-点哪评哪-10：找**同一元素**的上一条意见的指纹作为对比基准。

        「同一元素」判定用 ``selector``（点选/圈选都可能为空，则退化为 marker），
        找不到即返回空串——**不猜**，返回空让 ``changed`` 判为假。
        """
        key_selector = draft.selector
        key_marker = build_marker(draft.tag, draft.element_id, draft.element_class)
        stmt = (
            select(ReviewNote)
            .where(ReviewNote.session_id == session_id)
            .where(
                (ReviewNote.selector == key_selector)
                if key_selector else (ReviewNote.marker == key_marker)
            )
            .order_by(ReviewNote.created_at.desc())
            .limit(1)
        )
        prev = self.s.execute(stmt).scalar_one_or_none()
        return prev.dom_digest if prev is not None and prev.dom_digest else ""

    def _validate_draft(self, draft: NoteDraft) -> None:
        if draft.mode not in MODES:
            raise ValidationFailed("invalid_mode", f"mode 必须是 {MODES} 之一")
        if draft.mode == MODE_REGION:
            # A-点哪评哪-06：圈选必须有合法归一化矩形——否则坐标无从复原
            if not region_is_normalized(draft.region):
                raise ValidationFailed(
                    "invalid_region",
                    "圈选区域必须是归一化矩形 {x,y,w,h}，各值 0~1 且 w,h > 0"
                    "（A-点哪评哪-06 要求坐标可跨分辨率复原）",
                )
        if draft.mode == MODE_FREEHAND and not draft.strokes and not draft.audio_ref:
            # A-点哪评哪-07：画笔模式至少要留下笔迹或语音，否则等于空批注
            raise ValidationFailed(
                "empty_freehand",
                "画笔/语音批注至少要提供 strokes 或 audio_ref"
                "（A-点哪评哪-07 不允许空批注）",
            )
        if draft.mode == MODE_DOM and not draft.selector and not draft.tag:
            raise ValidationFailed(
                "empty_dom_target", "DOM 点选至少要有 selector 或 tag",
            )
