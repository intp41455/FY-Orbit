"""Tasks: idempotent create, cancel and SSE event stream (FROZEN_CONTRACT §5.2).

Task creation is idempotent on ``(owner_id, idempotency_key)``: the same key with
the same payload returns the same task; the same key with a different payload is
rejected. Cancellation propagates to children and releases unsettled budget via
BudgetService. The SSE stream sends only stage/state/redacted references.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ...db.models import Task
from ...db.types import utcnow
from ...runtime.sse import bus
from ...runtime.temporal import TemporalRuntime, workflow_id_for
from ..deps import csrf_protected, get_actor, get_services, Services
from ..schemas import TaskCreate
from ...services.actor import Actor
from ...services.errors import Conflict, NotFound, PermissionDenied
from ...services.task_reaper import degraded_markers

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _build_workflow_input(t: Task) -> dict:
    deadline = t.deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return {
        "task_id": t.id,
        "owner_id": t.owner_id,
        "goal": t.goal,
        "domain": t.domain,
        "mode": t.mode,
        "depth": t.depth,
        "idempotency_key": t.idempotency_key,
        "limits": {
            "max_steps": t.max_steps,
            "max_depth": 2,
            "max_retries": 2,
            "max_cost_usd": 0.5,
            "deadline": deadline.isoformat(),
        },
        "context": {"strategy": t.strategy},
    }


def _serialize(t: Task) -> dict:
    return {
        "id": t.id, "owner_id": t.owner_id, "goal": t.goal, "domain": t.domain,
        "mode": t.mode, "strategy": t.strategy, "status": t.status, "stage": t.stage,
        "depth": t.depth, "steps": t.steps, "max_steps": t.max_steps,
        "idempotency_key": t.idempotency_key,
        "deadline": t.deadline.isoformat() if t.deadline else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "version": t.version,
    }


@router.post("")
async def create_task(body: TaskCreate, request: Request,
                      actor: Actor = Depends(csrf_protected),
                      svc: Services = Depends(get_services)) -> dict:
    # Idempotency-Key header overrides body when present.
    key = request.headers.get("idempotency-key") or body.idempotency_key
    existing = svc.session.execute(
        select(Task).where(Task.owner_id == actor.owner_id, Task.idempotency_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.goal != body.goal:
            raise Conflict("idempotency_mismatch",
                           "Idempotency-Key already used with a different payload")
        # Replay: the workflow was already started on first creation; do not
        # start a duplicate. DB row is the source of truth.
        out = _serialize(existing)
        out["workflow"] = {"id": workflow_id_for(existing.id), "started": False}
        return out

    deadline = body.deadline or (utcnow() + timedelta(hours=1))
    t = Task(id=uuid4().hex, owner_id=actor.owner_id, goal=body.goal, domain=body.domain,
             mode=body.mode, strategy=body.strategy, status="queued", stage="requirements",
             deadline=deadline, idempotency_key=key)
    svc.session.add(t)
    svc.session.flush()
    svc.audit.append(actor, "task.created", t.id, {"mode": t.mode})
    svc.session.commit()
    bus.publish(t.id, "queued", {"task_id": t.id, "stage": t.stage, "status": t.status})

    # Start the durable workflow only when Temporal is wired. A start failure
    # must not 500 the API; surface it in the response but keep the DB row.
    tr: TemporalRuntime = request.app.state.temporal
    wf = {"id": workflow_id_for(t.id), "started": False, "status": "not_run"}
    if tr.is_enabled():
        try:
            run_id = await tr.start_task_workflow(_build_workflow_input(t))
            wf = {"id": workflow_id_for(t.id), "started": True, "run_id": run_id,
                  "status": "started"}
        except Exception as exc:  # duplicate-start / temporal hiccup
            wf = {"id": workflow_id_for(t.id), "started": False,
                  "status": f"start_failed:{type(exc).__name__}"}
    out = _serialize(t)
    out["workflow"] = wf
    # R-13: stop answering 200 that looks identical to "running". When Temporal is
    # not wired the task will never start, so name that explicitly instead of
    # leaving the client to render an eternal "进行中".
    degraded = degraded_markers(tr.is_enabled())
    if degraded:
        out["degraded"] = degraded
    return out


@router.get("/{task_id}")
async def get_task(task_id: str, actor: Actor = Depends(get_actor),
                   svc: Services = Depends(get_services)) -> dict:
    t = svc.session.get(Task, task_id)
    if t is None:
        raise NotFound("task_not_found", "Task not found")
    if actor.subject_type == "owner" and t.owner_id != actor.owner_id:
        raise NotFound("task_not_found", "Task not found")
    return _serialize(t)


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request,
                      actor: Actor = Depends(csrf_protected),
                      svc: Services = Depends(get_services)) -> dict:
    t = svc.session.get(Task, task_id)
    if t is None:
        raise NotFound("task_not_found", "Task not found")
    if actor.subject_type == "owner" and t.owner_id != actor.owner_id:
        raise NotFound("task_not_found", "Task not found")
    released = svc.budget.cancel_task(actor, task_id)
    svc.session.commit()
    bus.publish(task_id, "cancelled", {"task_id": task_id, "released": released})

    tr: TemporalRuntime = request.app.state.temporal
    sig = {"id": workflow_id_for(task_id), "sent": False, "status": "not_run"}
    if tr.is_enabled():
        try:
            await tr.send_cancel(task_id)
            sig = {"id": workflow_id_for(task_id), "sent": True, "status": "signal_sent"}
        except Exception as exc:
            sig = {"id": workflow_id_for(task_id), "sent": False,
                   "status": f"signal_failed:{type(exc).__name__}"}
    # Final state is always read back from the DB.
    fresh = svc.session.get(Task, task_id)
    return {"id": task_id, "status": fresh.status, "stage": fresh.stage,
            "released_reservations": released, "workflow": sig}


@router.get("/{task_id}/events")
async def task_events(task_id: str, request: Request,
                      actor: Actor = Depends(get_actor),
                      svc: Services = Depends(get_services)) -> StreamingResponse:
    t = svc.session.get(Task, task_id)
    if t is None:
        raise NotFound("task_not_found", "Task not found")
    if actor.subject_type == "owner" and t.owner_id != actor.owner_id:
        raise NotFound("task_not_found", "Task not found")

    last_id = int(request.headers.get("last-event-id") or 0)

    async def gen():
        async for ev in bus.subscribe(task_id, last_event_id=last_id):
            yield ev.to_sse()

    return StreamingResponse(gen(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
