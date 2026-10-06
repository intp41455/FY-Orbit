"""T6-E/G5 + T6-G：重连/重置后的续作编排与恢复中心服务层。

陛下的原话（任务书 T6 红线，不得打折）：
「下次重新接上网络或模型 API 重置接入的时候，要能**自动找到这些，并继续开工**。」

本模块把这句话落成三条可审计的机制：

* **扫描**（``scan``）：列出所有 open 中断事件，每条标注能否续作
  （持久检查点 / 已落盘流片段）与策略（auto/confirm/manual）；
* **续作**（``resume``）：按策略执行——auto 直接从持久检查点续跑（只续剩余
  步骤，红线 2：已完成不重跑不重复计费）；confirm（S5 换供应商）必须显式
  ``confirm_provider_change=True``；manual 拒绝并指路（S6 走快照回滚）；
* **启动扫描**（``startup_autoresume``）：进程重启后自动扫描，对 auto 策略
  且供应商指纹一致的事件自动续作（G5「自动找到并继续开工」）。

诚实边界（红线 5）：续作失败时事件**保持 open** 并记录失败原因，
绝不假装恢复成功；没有检查点就说没有检查点。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db.resilience_models import InterruptionEvent
from ..runtime.interruption import (
    INTERRUPTION_PLAYBOOK,
    mark_resumed,
    provider_fingerprint,
)
from .actor import Actor
from .audit import AuditService
from .errors import Conflict, NotFound
from .stream_persistence import get_segments


class RecoveryService:
    def __init__(self, session: Session, audit: AuditService, *, settings: Settings | None = None):
        self.s = session
        self.audit = audit
        self.settings = settings

    # -- 扫描 ---------------------------------------------------------------

    def scan(self, actor: Actor) -> dict:
        """列出 open 中断事件 + 每条的可续作性（恢复中心列表接口的服务半边）。"""
        rows = self.s.execute(
            select(InterruptionEvent)
            .where(InterruptionEvent.status == "open")
            .order_by(InterruptionEvent.created_at.desc())
            .limit(200)
        ).scalars().all()
        items = []
        for ev in rows:
            fp_now = provider_fingerprint(self.settings) if self.settings else ""
            provider_changed = bool(ev.provider_fp and fp_now and ev.provider_fp != fp_now)
            items.append({
                "id": ev.id,
                "interruption_class": ev.interruption_class,
                "task_id": ev.task_id,
                "thread_id": ev.thread_id,
                "message_id": ev.message_id,
                "stash_id": ev.stash_id,
                "detail": ev.detail,
                "resume_policy": ev.resume_policy,
                "status": ev.status,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
                "resume": {
                    "policy": ev.resume_policy,
                    "has_checkpoint": bool(ev.thread_id),
                    "has_stream_segments": bool(ev.message_id),
                    "provider_changed": provider_changed,
                    "effective_policy": "confirm" if provider_changed else ev.resume_policy,
                },
            })
        return {"open_count": len(items), "items": items}

    # -- 续作 ----------------------------------------------------------------

    def resume(
        self, actor: Actor, event_id: str, *, confirm_provider_change: bool = False
    ) -> dict:
        """按台账行的策略续作一条中断。

        * manual 策略 → 拒绝（S6 走快照回滚的专用流，不许在这里"一键继续"）；
        * confirm 策略（S5 换供应商 / 指纹变化）→ 必须显式确认（红线 6）；
        * auto 策略 → 直接续。
        """
        ev = self.s.get(InterruptionEvent, event_id)
        if ev is None:
            raise NotFound("interruption_not_found", "Interruption event not found", 404)
        if ev.status != "open":
            raise Conflict(
                "interruption_not_open",
                f"Interruption {event_id} is already {ev.status}; nothing to resume",
            )

        fp_now = provider_fingerprint(self.settings) if self.settings else ""
        provider_changed = bool(ev.provider_fp and fp_now and ev.provider_fp != fp_now)
        effective = "confirm" if provider_changed else ev.resume_policy

        if ev.resume_policy == "manual" and not provider_changed:
            raise Conflict(
                "resume_policy_manual",
                "This interruption is manual-only; use its dedicated recovery flow "
                "(e.g. snapshot restore for agent misuse), not the generic resume",
            )
        if effective == "confirm" and not confirm_provider_change:
            raise Conflict(
                "resume_confirmation_required",
                "Provider/model changed since the interruption (cost and output "
                "distribution will change). Pass confirm_provider_change=true to proceed",
            )

        result: dict = {"event_id": ev.id, "resumed": False, "actions": []}
        notes: list[str] = []

        # 1) 图检查点续作：只从最后完成的超步继续（红线 2）
        if ev.thread_id:
            action = self._resume_graph(ev)
            result["actions"].append(action)
            notes.append(action["note"])

        # 2) 流片段：断点信息一并回执（客户端凭 message_id/after_seq 取回）
        if ev.message_id:
            segs = get_segments(self.s, ev.message_id, after_seq=0, limit=5000)
            last_seq = segs[-1].seq if segs else 0
            result["actions"].append({
                "kind": "stream_segments_available",
                "message_id": ev.message_id,
                "segments": len(segs),
                "last_seq": last_seq,
                "note": "persisted frames available via GET /api/streaming/"
                        f"{ev.message_id}/segments",
            })
            notes.append(f"stream segments on disk: {len(segs)} (last_seq={last_seq})")

        if not result["actions"]:
            note = "no resumable artifacts recorded on this event (no checkpoint thread, no stream)"
            mark_resumed(self.s, self.audit, actor, ev, ok=False, note=note)
            self.s.commit()
            result["note"] = note
            return result

        failed = any(a.get("kind") == "graph_resume" and a.get("ok") is False
                     for a in result["actions"])
        if failed:
            mark_resumed(self.s, self.audit, actor, ev, ok=False, note="; ".join(notes)[:2000])
        else:
            mark_resumed(self.s, self.audit, actor, ev, ok=True, note="; ".join(notes)[:2000])
            result["resumed"] = True
        self.s.commit()
        result["note"] = "; ".join(notes)[:1000]
        return result

    def _resume_graph(self, ev: InterruptionEvent) -> dict:
        """从持久检查点续跑图。先查盘上最新检查点：已完成就**不**再 invoke（防重复计费）。"""
        import os

        from ..runtime.checkpoint_sqlite import SqliteCheckpointer
        from ..runtime.graph import compile_task_graph

        path = (
            os.environ.get("FY_CHECKPOINT_DB")
            or (getattr(self.settings, "checkpoint_db_path", "") if self.settings else "")
            or ".runtime/checkpoints/langgraph.sqlite"
        )
        saver = SqliteCheckpointer(path)
        config = {"configurable": {"thread_id": ev.thread_id}}
        try:
            latest = saver.get_tuple(config)
        except ValueError:
            latest = None
        if latest is None:
            return {"kind": "graph_resume", "ok": False, "thread_id": ev.thread_id,
                    "note": "no checkpoint on disk for this thread; cannot resume"}
        channel_values = (latest.checkpoint or {}).get("channel_values") or {}
        status = channel_values.get("status")
        if status == "completed":
            return {"kind": "graph_resume", "ok": True, "thread_id": ev.thread_id,
                    "already_complete": True,
                    "note": "checkpoint shows run already completed; not re-invoking "
                            "(no double billing)"}
        try:
            app = compile_task_graph()
            state = app.invoke(None, config=config)
            return {"kind": "graph_resume", "ok": True, "thread_id": ev.thread_id,
                    "status": state.get("status"),
                    "note": f"resumed from checkpoint; status={state.get('status')!r}"}
        except Exception as exc:  # noqa: BLE001 —— 诚实上报，不许静默
            return {"kind": "graph_resume", "ok": False, "thread_id": ev.thread_id,
                    "note": f"resume invoke failed: {type(exc).__name__}: {exc}"[:500]}


def startup_autoresume(session_maker: sessionmaker, settings: Settings) -> dict:
    """进程启动时的 G5 编排：扫描 open 事件，auto 策略且指纹一致的自动续作。

    任何一条失败都不阻断启动——结果如实返回给调用方（app lifespan 把它
    挂在 ``app.state.recovery_summary``，health 可见）。
    """
    if not getattr(settings, "recovery_autoresume", False):
        return {"enabled": False, "resumed": 0, "failed": 0, "open": None}
    session = session_maker()
    try:
        actor = Actor.owner(settings.owner_id)
        audit = AuditService(session)
        svc = RecoveryService(session, audit, settings=settings)
        scan = svc.scan(actor)
        fp_now = provider_fingerprint(settings)
        resumed = failed = 0
        details: list[dict] = []
        for item in scan["items"]:
            effective = item["resume"]["effective_policy"]
            if effective != "auto":
                continue
            if item["provider_fp"] and item["provider_fp"] != fp_now:
                continue
            try:
                out = svc.resume(actor, item["id"])
                if out.get("resumed"):
                    resumed += 1
                else:
                    failed += 1
                details.append({"event_id": item["id"], **{k: out.get(k) for k in ("resumed", "note")}})
            except Exception as exc:  # noqa: BLE001 —— 单条失败不拖垮启动
                failed += 1
                details.append({"event_id": item["id"], "resumed": False,
                                "note": f"{type(exc).__name__}: {exc}"[:300]})
        session.commit()
        return {"enabled": True, "resumed": resumed, "failed": failed,
                "open": scan["open_count"], "details": details[:20]}
    finally:
        session.close()


def playbook_view() -> dict:
    """处置矩阵的对外视图（T6-G 可发现性：让用户/前端知道每类中断怎么处理）。"""
    return {
        "classes": [
            {"interruption_class": key,
             **entry.__dict__}
            for key, entry in INTERRUPTION_PLAYBOOK.items()
        ]
    }
