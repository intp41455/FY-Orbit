"""Memory search (FROZEN_CONTRACT §5.3, §8.2).

Authorization-first retrieval: the Core MemoryService applies the authz
predicate in the query candidate set BEFORE ranking. The consumer domain is a
query parameter; an owner may query any domain. Caller-supplied records are not
trusted beyond that.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/memory", tags=["memory"])


class DenyHypothesisRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    message_id: str | None = None


@router.get("/search")
async def search_memory(
    q: str = Query(default="", max_length=500),
    domain: str = Query(default="personal", pattern="^(personal|work|shared)$"),
    limit: int = Query(default=8, ge=1, le=50),
    cursor: str = Query(default=""),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    results = svc.memory.search(consumer_domain=domain, query=q, limit=limit, owner_id=actor.owner_id)
    return {"results": results, "consumer_domain": domain, "count": len(results)}


@router.get("/{id}/revisions")
async def get_memory_revisions(
    id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    revisions = svc.memory.get_revisions(actor, id)
    return {"memory_id": id, "revisions": revisions, "count": len(revisions)}


@router.post("/{id}/deny")
async def deny_memory_hypothesis(
    id: str,
    body: DenyHypothesisRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    mem = svc.memory.deny_hypothesis(actor, id, body.reason, body.message_id)
    svc.session.commit()
    return {
        "id": mem.id,
        "active": mem.active,
        "endorsed": mem.endorsed,
        "hypothesis_status": mem.hypothesis_status,
        "version": mem.version,
    }
