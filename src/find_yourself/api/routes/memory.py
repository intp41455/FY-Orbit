"""Memory search (FROZEN_CONTRACT §5.3, §8.2).

Authorization-first retrieval: the Core MemoryService applies the authz
predicate in the query candidate set BEFORE ranking. The consumer domain is a
query parameter; an owner may query any domain. Caller-supplied records are not
trusted beyond that.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import get_actor, get_services, Services
from ...services.actor import Actor

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/search")
async def search_memory(
    q: str = Query(default="", max_length=500),
    domain: str = Query(default="personal", pattern="^(personal|work|shared)$"),
    limit: int = Query(default=8, ge=1, le=50),
    cursor: str = Query(default=""),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    results = svc.memory.search(consumer_domain=domain, query=q, limit=limit)
    return {"results": results, "consumer_domain": domain, "count": len(results)}
