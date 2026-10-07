"""点哪评哪 HTTP 面（A-点哪评哪-05/06/07/08/09/10 + A-路线-03）。

评审意见落库（不再只躺 localStorage）+ 三种进阶玩法 + TOKEN 优化三项 + 双路线。

* ``POST   /api/review/sessions``                     — 开评审会话（记页面 / 双路线）
* ``GET    /api/review/sessions/{id}``                — 会话详情（含计数汇总）
* ``POST   /api/review/sessions/{id}/notes``          — 新增意见（dom / region / freehand）
* ``GET    /api/review/sessions/{id}/notes``          — 列意见（可按 state 过滤）
* ``PATCH  /api/review/notes/{id}``                   — 改意见正文 / 状态（含「已改完」）
* ``DELETE /api/review/notes/{id}``                   — 删意见
* ``POST   /api/review/sessions/{id}/refresh``        — 记录热刷新闭环结果（A-点哪评哪-05）
* ``GET    /api/review/sessions/{id}/export``         — 导出 Markdown（``compact`` 走 TOKEN 优化形态）

A-路线-03 另有 ``POST /api/review/route`` —— 纯策略决策（白盒优先 / Computer Use 兜底），
无副作用、不落库，供前端在采集到定位信息后先问「该走哪条路线」。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...services.audit import AuditService
from ...services.review_bridge import (
    MODE_DOM,
    REFRESH_STATES,
    ROUTE_WHITEBOX,
    ROUTES,
    NoteDraft,
    ReviewBridge,
)
from ...services.review_routes import Locator, decide_route, summarize_routes
from ..deps import csrf_protected, get_db

router = APIRouter(tags=["review"])


# --------------------------------------------------------------------------- #
# 请求体
# --------------------------------------------------------------------------- #


class SessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: str = Field(default="", max_length=500)
    title: str = Field(default="", max_length=200)
    route: str = Field(default=ROUTE_WHITEBOX, max_length=30)


class NoteBody(BaseModel):
    """一条意见的提交体（三种玩法共用；按 ``mode`` 校验必填项）。"""

    model_config = ConfigDict(extra="forbid")

    page: str = Field(default="", max_length=500)
    mode: str = Field(default=MODE_DOM, max_length=20)
    # dom 点选
    tag: str = Field(default="", max_length=40)
    element_id: str = Field(default="", max_length=200)
    element_class: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=300)
    selector: str = Field(default="", max_length=2000)
    dom_path: list[str] = Field(default_factory=list)
    # A-点哪评哪-06 圈选区域（归一化 0~1）
    region: dict[str, Any] = Field(default_factory=dict)
    # A-点哪评哪-07 画笔批注 + 语音附件
    strokes: list[dict[str, Any]] = Field(default_factory=list)
    audio_ref: str = Field(default="", max_length=500)
    audio_transcript: str = Field(default="", max_length=4000)
    # 意见正文
    note: str = Field(default="", max_length=8000)


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=8000)
    state: str | None = Field(default=None, max_length=20)


class RefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(max_length=20)
    note: str = Field(default="", max_length=2000)


class RouteBody(BaseModel):
    """A-路线-03 策略决策输入（可单条，也可批量）。"""

    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default=MODE_DOM, max_length=20)
    selector: str = Field(default="", max_length=2000)
    dom_path: list[str] = Field(default_factory=list)
    tag: str = Field(default="", max_length=40)
    region: dict[str, Any] = Field(default_factory=dict)
    viewport: dict[str, Any] = Field(default_factory=dict)
    dom_reachable: bool = True
    fallback_reason: str | None = Field(default=None, max_length=40)

    def to_locator(self) -> Locator:
        return Locator(
            mode=self.mode, selector=self.selector, dom_path=list(self.dom_path),
            tag=self.tag, region=dict(self.region), viewport=dict(self.viewport),
            dom_reachable=self.dom_reachable,
        )


def _bridge(db: Session) -> ReviewBridge:
    return ReviewBridge(db, AuditService(db))


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


@router.post("/api/review/sessions")
async def open_session(
    body: SessionBody,
    actor: object = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """开一次评审会话（A-路线-03：``route`` 显式标记双路线）。"""
    svc = _bridge(db)
    sess = svc.open_session(
        actor, page=body.page, title=body.title, route=body.route,  # type: ignore[arg-type]
    )
    db.commit()
    return {
        "status": "ok",
        "session": {
            "id": sess.id, "page": sess.page, "title": sess.title, "route": sess.route,
            "iteration": sess.iteration, "refresh_state": sess.refresh_state,
        },
    }


@router.get("/api/review/sessions/{session_id}")
async def get_session(
    session_id: str,
    actor: object = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """会话详情 + 计数汇总。"""
    svc = _bridge(db)
    return {"status": "ok", "summary": svc.session_summary(actor, session_id)}  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 意见
# --------------------------------------------------------------------------- #


@router.post("/api/review/sessions/{session_id}/notes")
async def add_note(
    session_id: str,
    body: NoteBody,
    actor: object = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """新增意见（A-点哪评哪-06/07 的几何在服务层强校验）。"""
    svc = _bridge(db)
    draft = NoteDraft(
        page=body.page, mode=body.mode, tag=body.tag, element_id=body.element_id,
        element_class=body.element_class, text=body.text, selector=body.selector,
        dom_path=body.dom_path, region=body.region, strokes=body.strokes,
        audio_ref=body.audio_ref, audio_transcript=body.audio_transcript, note=body.note,
    )
    view = svc.add_note(actor, session_id, draft)  # type: ignore[arg-type]
    db.commit()
    return {"status": "ok", "note": view.to_public()}


@router.get("/api/review/sessions/{session_id}/notes")
async def list_notes(
    session_id: str,
    state: str | None = Query(default=None),
    actor: object = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    svc = _bridge(db)
    notes = svc.list_notes(actor, session_id, state=state)  # type: ignore[arg-type]
    return {"status": "ok", "notes": [n.to_public() for n in notes], "count": len(notes)}


@router.patch("/api/review/notes/{note_id}")
async def patch_note(
    note_id: str,
    body: NotePatch,
    actor: object = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """改意见正文或状态（``state=resolved`` 落 applied_at，闭环锚点）。"""
    svc = _bridge(db)
    view = svc.update_note(actor, note_id, note=body.note, state=body.state)  # type: ignore[arg-type]
    db.commit()
    return {"status": "ok", "note": view.to_public()}


@router.delete("/api/review/notes/{note_id}")
async def delete_note(
    note_id: str,
    actor: object = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    svc = _bridge(db)
    svc.delete_note(actor, note_id)  # type: ignore[arg-type]
    db.commit()
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# 热刷新闭环（A-点哪评哪-05）
# --------------------------------------------------------------------------- #


@router.post("/api/review/sessions/{session_id}/refresh")
async def mark_refreshed(
    session_id: str,
    body: RefreshBody,
    actor: object = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """记录一次「改完自动刷新预览」的结果（A-点哪评哪-05）。

    ``state=applied`` 推进迭代轮次；``failed`` 如实记录原因、不推进。
    """
    svc = _bridge(db)
    sess = svc.mark_refreshed(actor, session_id, state=body.state, note=body.note)  # type: ignore[arg-type]
    db.commit()
    return {
        "status": "ok",
        "session": {
            "id": sess.id, "iteration": sess.iteration,
            "refresh_state": sess.refresh_state, "refresh_note": sess.refresh_note,
            "refreshed_at": sess.refreshed_at.isoformat() if sess.refreshed_at else None,
        },
    }


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #


@router.get("/api/review/sessions/{session_id}/export")
async def export_notes(
    session_id: str,
    compact: bool = Query(default=False),
    actor: object = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """导出给执行 Agent 的 Markdown（``compact=true`` 走 TOKEN 优化形态）。"""
    svc = _bridge(db)
    md = svc.export(actor, session_id, compact=compact)  # type: ignore[arg-type]
    return {"status": "ok", "markdown": md, "compact": compact}


# --------------------------------------------------------------------------- #
# 双路线策略（A-路线-03）—— 纯决策，无副作用
# --------------------------------------------------------------------------- #


@router.post("/api/review/route")
async def decide(body: RouteBody, actor: object = Depends(csrf_protected)) -> dict:
    """决定白盒 / Computer Use 兜底（白盒优先；兜底必须给理由）。"""
    decision = decide_route(body.to_locator(), fallback_reason=body.fallback_reason)
    return {"status": "ok", "decision": decision.to_public()}


@router.post("/api/review/route/batch")
async def decide_batch(
    items: list[RouteBody] = Body(...),
    actor: object = Depends(csrf_protected),
) -> dict:
    """批量决策 + 兜底占比汇总（防止兜底被滥用，A-路线-03 可观测性）。"""
    decisions = [
        decide_route(it.to_locator(), fallback_reason=it.fallback_reason) for it in items
    ]
    return {
        "status": "ok",
        "decisions": [d.to_public() for d in decisions],
        "summary": summarize_routes(decisions),
    }


#: 供路由自检/文档使用（避免 ``ROUTES`` / ``REFRESH_STATES`` 被闲置的 lint 噪音）
KNOWN_ROUTES = ROUTES
KNOWN_REFRESH_STATES = REFRESH_STATES
