"""A2A HTTP surface: Agent Card discovery and JSON-RPC endpoint (G7/A01)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...adapters.a2a import (
    AGENT_CARD_PATH,
    JSONRPC_PATH,
    A2ADispatcher,
    build_agent_card,
)
from ...db.models import Task
from ..deps import Services, get_actor, get_services, get_settings
from ...config import Settings
from ...services.actor import Actor

router = APIRouter(tags=["a2a"])


@router.get(AGENT_CARD_PATH)
async def agent_card(settings: Settings = Depends(get_settings)) -> dict:
    from ...db.models import Skill as SkillRow
    # Public card advertises capabilities only; no secrets.
    return build_agent_card(
        public_url=settings.public_url,
        agent_name="Find Yourself",
        version="0.1.0",
        description="Owner-personal agent surface; A2A-compatible discovery.",
        skills=[],
    )


@router.post(JSONRPC_PATH)
async def a2a_jsonrpc(request: Request,
                       actor: Actor = Depends(get_actor),
                       svc: Services = Depends(get_services),
                       settings: Settings = Depends(get_settings)) -> dict:
    body = await request.json()
    # Task lookup/cancel against local tasks, owner-scoped by the resolved actor.
    def lookup(task_id: str):
        t = svc.session.get(Task, task_id)
        if t is None:
            return None
        if actor.subject_type == "owner" and t.owner_id != actor.owner_id:
            return None
        return {"id": t.id, "status": {"state": t.status}, "title": t.goal}

    def cancel(task_id: str) -> bool:
        t = svc.session.get(Task, task_id)
        if t is None or t.status in ("completed", "cancelled", "failed"):
            return False
        if actor.subject_type == "owner" and t.owner_id != actor.owner_id:
            return False
        t.status = "cancelled"
        svc.session.commit()
        return True

    upstream = bool(getattr(settings, "a2a_upstream_url", None))
    dispatcher = A2ADispatcher(
        upstream_configured=upstream, draining=False,
        task_lookup=lookup, cancel_task=cancel,
    )
    return dispatcher.dispatch(body)


@router.get("/a2a/health")
async def a2a_health() -> dict:
    return {"status": "ok", "protocol": "a2a", "version": "0.3.0"}
