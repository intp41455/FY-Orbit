"""Read-only catalog: agents, skills and artifacts (FROZEN_CONTRACT §5.3).

Writes to agents/skills go through proposals only — the API never exposes a direct
"enable" path that bypasses approval. Artifact download is owner-authenticated
and served from the private local store; S3 presigned URLs are short-lived and
never public.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select

from ...adapters.artifacts import build_artifact_store
from ...db.models import Agent, Artifact, Skill
from ..deps import get_actor, get_services, get_settings, Services
from ...config import Settings
from ...services.actor import Actor
from ...services.errors import NotFound

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/agents")
async def list_agents(actor: Actor = Depends(get_actor),
                      svc: Services = Depends(get_services)) -> list[dict]:
    rows = svc.session.execute(select(Agent).order_by(Agent.name.asc())).scalars()
    return [{"id": a.id, "name": a.name, "semantic_version": a.semantic_version,
             "state": a.state, "healthy": a.healthy, "capabilities": a.capabilities,
             "domains": a.domains} for a in rows]


@router.get("/skills")
async def list_skills(actor: Actor = Depends(get_actor),
                       svc: Services = Depends(get_services)) -> list[dict]:
    rows = svc.session.execute(select(Skill).order_by(Skill.name.asc())).scalars()
    return [{"id": s.id, "name": s.name, "semantic_version": s.semantic_version,
             "state": s.state, "package_hash": s.package_hash, "domain": s.domain,
             "source": s.source, "license": s.license} for s in rows]


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, request: Request,
                       actor: Actor = Depends(get_actor),
                       svc: Services = Depends(get_services),
                       settings: Settings = Depends(get_settings)) -> Response:
    art = svc.session.get(Artifact, artifact_id)
    if art is None or art.deleted_at is not None:
        raise NotFound("artifact_not_found", "Artifact not found")
    store = build_artifact_store(settings)
    data = store.get(artifact_id)
    return Response(content=data, media_type=art.media_type,
                    headers={"Content-Disposition": f'inline; filename="{artifact_id}"'})
