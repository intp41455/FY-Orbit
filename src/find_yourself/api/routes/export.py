"""Owner data export and internal audit verification (FROZEN_CONTRACT §8.3, §9).

Export bundles the owner's conversations, messages, memories and grants
metadata. It NEVER includes system secrets, session cookies, service tokens or
private connection strings. The audit verify endpoint is owner-only and returns
only tamper-evidence results (seq count + problem strings), not event payloads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ...db.models import Conversation, Grant, Memory, Message
from ...services.export import ExportService
from ..deps import csrf_protected, get_actor, get_services, Services
from ..schemas import ExportRequest
from ...services.actor import Actor
from ...services.errors import PermissionDenied

router = APIRouter(prefix="/api", tags=["export"])


@router.post("/export")
async def export_data(body: ExportRequest, actor: Actor = Depends(csrf_protected),
                      svc: Services = Depends(get_services)) -> dict:
    actor.require_owner()
    if not body.confirm:
        raise PermissionDenied("export_confirm", "Data export requires explicit confirmation", 400)

    export_svc = ExportService(svc.session, svc.audit)
    bundle = export_svc.create_export_bundle(actor)
    token_info = export_svc.generate_download_token(actor, bundle)
    bundle["download_token"] = token_info["download_token"]
    bundle["expires_at"] = token_info["expires_at"]
    return bundle


@router.get("/export/download/{token}")
async def download_export(token: str, actor: Actor = Depends(get_actor),
                          svc: Services = Depends(get_services)) -> dict:
    actor.require_owner()
    export_svc = ExportService(svc.session, svc.audit)
    return export_svc.consume_download_token(token)


@router.get("/internal/audit/verify")
async def audit_verify(actor: Actor = Depends(get_actor),
                       svc: Services = Depends(get_services)) -> dict:
    actor.require_owner()
    result = svc.audit.verify()
    return {"ok": result.ok, "checked": result.checked, "problems": result.problems}
