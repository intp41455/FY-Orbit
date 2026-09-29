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

    conversations = svc.session.execute(
        select(Conversation).where(Conversation.owner_id == actor.owner_id,
                                    Conversation.deleted_at.is_(None))
    ).scalars()
    conv_list = []
    for c in conversations:
        msgs = svc.session.execute(
            select(Message).where(Message.conversation_id == c.id,
                                  Message.deleted_at.is_(None)).order_by(Message.created_at.asc())
        ).scalars()
        conv_list.append({
            "id": c.id, "title": c.title, "domain": c.domain, "mode": c.mode,
            "messages": [{"role": m.role, "content": m.content, "source": m.source} for m in msgs],
        })

    memories = svc.session.execute(
        select(Memory).where(Memory.owner_id == actor.owner_id, Memory.deleted_at.is_(None))
    ).scalars()
    grants = svc.session.execute(select(Grant)).scalars()

    return {
        "owner_id": actor.owner_id,
        "conversations": conv_list,
        "memories": [{"id": m.id, "domain": m.domain, "category": m.category,
                      "content": m.content, "version": m.version} for m in memories],
        "grants": [{"id": g.id, "source_domain": g.source_domain,
                    "consumer_domain": g.consumer_domain, "state": g.state} for g in grants],
        "contains_secrets": False,
    }


@router.get("/internal/audit/verify")
async def audit_verify(actor: Actor = Depends(get_actor),
                       svc: Services = Depends(get_services)) -> dict:
    actor.require_owner()
    result = svc.audit.verify()
    return {"ok": result.ok, "checked": result.checked, "problems": result.problems}
