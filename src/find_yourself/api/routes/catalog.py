"""Read-only catalog: agents, skills, artifacts and model providers (FROZEN_CONTRACT §5.3).

Writes to agents/skills go through proposals only — the API never exposes a direct
"enable" path that bypasses approval. Artifact download is owner-authenticated
and served from the private local store; S3 presigned URLs are short-lived and
never public.

W4 additions: ``GET /api/models/catalog`` (multi-provider registry with real
health state) and ``POST /api/models/health-check`` (live probe). Both are
read-only with respect to state but issue genuine outbound requests to whatever
endpoint the owner configured — never to a provider the owner did not configure.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from ...adapters.artifacts import build_artifact_store
from ...db.models import Agent, Artifact, Skill
from ..deps import get_actor, get_services, get_settings, Services, csrf_protected
from ...config import Settings
from ...services.actor import Actor
from ...services.errors import NotFound
from ...services.model_catalog import ModelCatalog
from ...db.types import utcnow

log = logging.getLogger("find_yourself.api.catalog")

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


@router.get("/artifacts/{artifact_id}/presigned")
async def get_artifact_presigned(artifact_id: str,
                                 expires_in: int = 300,
                                 actor: Actor = Depends(get_actor),
                                 svc: Services = Depends(get_services),
                                 settings: Settings = Depends(get_settings)) -> dict:
    art = svc.session.get(Artifact, artifact_id)
    if art is None or art.deleted_at is not None:
        raise NotFound("artifact_not_found", "Artifact not found")
    store = build_artifact_store(settings)
    url = store.presigned_get(artifact_id, expires_in_seconds=expires_in)
    return {"artifact_id": artifact_id, "url": url, "expires_in": expires_in}


# --------------------------------------------------------------------------- #
# W4 · 模型接入（多 Provider 目录与健康探测）
# --------------------------------------------------------------------------- #

_HEALTH_TIMEOUT_DEFAULT = 4.0
_HEALTH_TIMEOUT_MAX = 20.0


class HealthCheckRequest(BaseModel):
    """Empty body = probe everything configured. Otherwise explicit ids.

    ``timeout_seconds`` tolerates a malformed value by falling back to the
    default instead of rejecting the request: a settings page probe should not
    be blocked by a stray form value.
    """

    providers: list[str] = Field(default_factory=list)
    timeout_seconds: float | str = _HEALTH_TIMEOUT_DEFAULT

    def bounded_timeout(self) -> float:
        try:
            value = float(self.timeout_seconds)
        except (TypeError, ValueError):
            return _HEALTH_TIMEOUT_DEFAULT
        if value <= 0 or value != value:  # NaN guard
            return _HEALTH_TIMEOUT_DEFAULT
        return min(value, _HEALTH_TIMEOUT_MAX)


@router.get("/models/catalog")
async def models_catalog(probe: bool = True,
                         timeout_seconds: float = 2.5,
                         actor: Actor = Depends(get_actor),
                         settings: Settings = Depends(get_settings)) -> dict:
    """Providers actually known to the runtime plus real configuration state.

    ``probe`` defaults to true so the UI can show a genuine health badge; only
    endpoints the owner configured are contacted. Set ``probe=false`` for a
    pure configuration listing.
    """
    timeout = min(max(float(timeout_seconds), 0.1), _HEALTH_TIMEOUT_MAX) if probe else 0.0
    catalog_local = ModelCatalog(settings=settings)
    if not probe:
        return catalog_local.gateway_summary(probe=False)
    # Probing is blocking network I/O — keep it off the event loop.
    return await run_in_threadpool(
        lambda: catalog_local.gateway_summary(probe=True, timeout_seconds=timeout))


@router.post("/models/health-check")
async def models_health_check(body: HealthCheckRequest | None = None,
                              actor: Actor = Depends(csrf_protected),
                              settings: Settings = Depends(get_settings)) -> dict:
    """Live probe of the configured endpoints: real latency or the real error.

    A provider we cannot reach comes back ``ok=false`` with the sanitised
    transport reason (URLs stripped), never a fabricated success.
    """
    payload = body or HealthCheckRequest()
    timeout = payload.bounded_timeout()
    catalog_local = ModelCatalog(settings=settings)
    report = await run_in_threadpool(
        lambda: catalog_local.health_report(payload.providers or None, timeout_seconds=timeout))
    return {"checked_at": utcnow().isoformat(), **report}


@router.get("/settings")
async def settings_summary(actor: Actor = Depends(get_actor),
                           settings: Settings = Depends(get_settings)) -> dict:
    """Non-secret configuration summary for the settings page (主控纠错 2026-10-04).

    The page previously called a ``/api/settings`` endpoint that never existed
    (pre-existing 404 banner). Fields are honest booleans derived from the live
    settings — never a fabricated "everything is fine".
    """
    from ...runtime.providers import infer_provider_id

    provider = infer_provider_id(api_key=settings.model_api_key, base_url=settings.model_base_url)
    if provider == "ollama":
        model_configured = bool(settings.model_base_url)
    else:
        model_configured = bool(settings.model_api_key and settings.model_base_url)
    return {
        "model_configured": model_configured,
        "oidc_configured": bool(settings.oidc_issuer and settings.oidc_client_id and settings.oidc_owner_sub),
        "local_dev_token_allowed": settings.environment != "production" and bool(settings.local_token),
        "data_domains": ["personal", "work", "shared"],
        "export_status": None,
    }
