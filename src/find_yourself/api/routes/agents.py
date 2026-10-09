"""Agent lifecycle HTTP surface wrapping Core AgentService (G7/A03/A04).

Owner-only mutations go through CSRF-protected routes. Lease acquisition binds an
in-flight task to the agent's *current* version; after an upgrade, new tasks
acquire a lease on the new version while existing leases keep the old version.
Draining stops new assignments; revoke invalidates credentials and leases.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...db.models import Agent, AgentLease
from ...services.actor import Actor
from ..deps import Services, csrf_protected, get_services

router = APIRouter(prefix="/api/agents", tags=["agents-lifecycle"])


class RegisterBody(BaseModel):
    name: str
    semantic_version: str
    capabilities: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    endpoint_key: str
    max_concurrency: int = 1


@router.post("")
async def register(body: RegisterBody, actor: Actor = Depends(csrf_protected),
                   svc: Services = Depends(get_services)) -> dict:
    row = svc.agents.register(actor, name=body.name, semantic_version=body.semantic_version,
                              capabilities=body.capabilities, domains=body.domains,
                              endpoint_key=body.endpoint_key, max_concurrency=body.max_concurrency)
    svc.session.commit()
    return {"id": row.id, "state": row.state, "semantic_version": row.semantic_version}


@router.post("/{agent_id}/health")
async def set_health(agent_id: str, healthy: bool, actor: Actor = Depends(csrf_protected),
                     svc: Services = Depends(get_services)) -> dict:
    row = svc.agents.set_health(actor, agent_id, healthy)
    svc.session.commit()
    return {"id": row.id, "state": row.state, "healthy": row.healthy}


@router.post("/{agent_id}/enable")
async def enable(agent_id: str, actor: Actor = Depends(csrf_protected),
                svc: Services = Depends(get_services)) -> dict:
    row = svc.agents.enable(actor, agent_id)
    svc.session.commit()
    return {"id": row.id, "state": row.state}


@router.post("/{agent_id}/drain")
async def drain(agent_id: str, actor: Actor = Depends(csrf_protected),
               svc: Services = Depends(get_services)) -> dict:
    row = svc.agents.drain(actor, agent_id)
    svc.session.commit()
    active = svc.session.execute(
        select(AgentLease).where(AgentLease.agent_id == agent_id, AgentLease.state == "active")
    ).scalars().all()
    return {"id": row.id, "state": row.state, "active_leases": len(active)}


@router.post("/{agent_id}/revoke")
async def revoke(agent_id: str, actor: Actor = Depends(csrf_protected),
                svc: Services = Depends(get_services)) -> dict:
    row = svc.agents.revoke(actor, agent_id)
    svc.session.commit()
    return {"id": row.id, "state": row.state}


@router.post("/{agent_id}/leases")
async def acquire_lease(agent_id: str, task_id: str, actor: Actor = Depends(csrf_protected),
                       svc: Services = Depends(get_services)) -> dict:
    lease = svc.agents.acquire_lease(actor, agent_id, task_id)
    svc.session.commit()
    agent = svc.session.get(Agent, agent_id)
    # The lease records the agent version at assignment time (in-flight pin).
    return {"lease_id": lease.id, "agent_id": agent_id,
            "agent_version": agent.semantic_version, "task_id": task_id,
            "state": lease.state}
