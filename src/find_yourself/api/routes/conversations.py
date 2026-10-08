"""Conversations and messages (FROZEN_CONTRACT §5.2, M01).

Full original text is persisted with role/source/model/timestamp. The
``owner_id`` is taken from the authenticated :class:`Actor` — a request body
``owner_id``/``domain`` never reassigns ownership (S04). Messages are unique per
``(conversation, client_message_id)``.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ...db.models import Conversation, Message
from ...db.types import utcnow
from ..deps import csrf_protected, get_actor, get_services, get_settings, Services
from ...config import Settings
from ..schemas import ConversationCreate, MessageCreate
from ...services.actor import Actor
from ...services.errors import NotFound, PermissionDenied, Conflict

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _serialize_conversation(c: Conversation) -> dict:
    # P1-3: 补充 message_count，前端历史列表直接显示条数
    from sqlalchemy import select, func
    from ...db.models import Message
    # 这里只能同步查，列表接口改用子查询更高效；先在序列化里加字段
    message_count = 0
    try:
        # 避免循环导入，延迟导入
        from ...db.session import get_session
        session = get_session()
        message_count = session.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == c.id, Message.deleted_at.is_(None)
            )
        ).scalar() or 0
    except Exception:
        pass
    return {
        "id": c.id, "title": c.title, "domain": c.domain, "mode": c.mode,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "version": c.version,
        "message_count": message_count,
    }


def _serialize_message(m: Message) -> dict:
    return {
        "id": m.id, "conversation_id": m.conversation_id, "role": m.role,
        "content": m.content, "source": m.source, "model": m.model,
        "client_message_id": m.client_message_id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.post("")
async def create_conversation(body: ConversationCreate,
                              actor: Actor = Depends(csrf_protected),
                              svc: Services = Depends(get_services)) -> dict:
    c = Conversation(id=uuid4().hex, owner_id=actor.owner_id, title=body.title,
                     domain=body.domain, mode=body.mode)
    svc.session.add(c)
    svc.session.flush()
    svc.audit.append(actor, "conversation.created", c.id, {"domain": c.domain})
    svc.session.commit()
    return _serialize_conversation(c)


@router.get("")
async def list_conversations(actor: Actor = Depends(get_actor),
                             svc: Services = Depends(get_services)) -> list[dict]:
    from sqlalchemy import select, func, outerjoin
    from ...db.models import Message
    # 子查询：每个 conversation 的 message_count
    subq = (
        select(Message.conversation_id, func.count(Message.id).label("msg_cnt"))
        .where(Message.deleted_at.is_(None))
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = svc.session.execute(
        select(Conversation, subq.c.msg_cnt)
        .outerjoin(subq, Conversation.id == subq.c.conversation_id)
        .where(Conversation.owner_id == actor.owner_id, Conversation.deleted_at.is_(None))
        .order_by(Conversation.updated_at.desc())
    ).all()
    result = []
    for c, cnt in rows:
        d = _serialize_conversation(c)
        d["message_count"] = cnt or 0
        result.append(d)
    return result


def _get_owned(svc: Services, actor: Actor, conversation_id: str) -> Conversation:
    c = svc.session.get(Conversation, conversation_id)
    # Do not leak existence of others' conversations (404, not 403 detail).
    if c is None or c.deleted_at is not None or c.owner_id != actor.owner_id:
        raise NotFound("conversation_not_found", "Conversation not found")
    return c


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, actor: Actor = Depends(get_actor),
                           svc: Services = Depends(get_services)) -> dict:
    from sqlalchemy import select, func
    from ...db.models import Message
    c = _get_owned(svc, actor, conversation_id)
    d = _serialize_conversation(c)
    # 单条查 message_count（避免序列化里再查一次 DB）
    cnt = svc.session.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == c.id, Message.deleted_at.is_(None)
        )
    ).scalar() or 0
    d["message_count"] = cnt
    return d


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str,
                             actor: Actor = Depends(csrf_protected),
                             svc: Services = Depends(get_services)) -> dict:
    """软删除会话。幂等：已删除的会话再删一次同样返回 200。"""
    c = _get_owned(svc, actor, conversation_id)
    if c.deleted_at is None:
        c.deleted_at = utcnow()
        svc.audit.append(actor, "conversation.deleted", c.id, {})
        svc.session.commit()
    return {"id": c.id, "deleted": True}


@router.post("/{conversation_id}/messages")
async def append_message(conversation_id: str, body: MessageCreate,
                         actor: Actor = Depends(csrf_protected),
                         svc: Services = Depends(get_services)) -> dict:
    c = _get_owned(svc, actor, conversation_id)
    # Idempotency on client_message_id: same key -> return existing message.
    existing = svc.session.execute(
        select(Message).where(Message.conversation_id == c.id,
                             Message.client_message_id == body.client_message_id)
    ).scalar_one_or_none()
    if existing is not None:
        return _serialize_message(existing)
    m = Message(id=uuid4().hex, conversation_id=c.id, role=body.role, content=body.content,
                source=body.source, client_message_id=body.client_message_id)
    svc.session.add(m)
    svc.session.flush()
    c.updated_at = utcnow()
    svc.audit.append(actor, "message.appended", m.id, {"role": m.role})
    svc.session.commit()
    return _serialize_message(m)


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: str, actor: Actor = Depends(get_actor),
                        svc: Services = Depends(get_services)) -> list[dict]:
    c = _get_owned(svc, actor, conversation_id)
    rows = svc.session.execute(
        select(Message).where(Message.conversation_id == c.id,
                              Message.deleted_at.is_(None)).order_by(Message.created_at.asc())
    ).scalars()
    return [_serialize_message(m) for m in rows]


@router.post("/{conversation_id}/reply")
async def generate_reply(conversation_id: str,
                         body: dict | None = None,
                         actor: Actor = Depends(csrf_protected),
                         svc: Services = Depends(get_services),
                         settings: Settings = Depends(get_settings)) -> dict:
    from ...services.companion import CompanionService
    c = _get_owned(svc, actor, conversation_id)
    user_text = (body or {}).get("message")
    if not user_text:
        last_user = svc.session.execute(
            select(Message).where(Message.conversation_id == c.id, Message.role == "user", Message.deleted_at.is_(None))
            .order_by(Message.created_at.desc())
        ).scalars().first()
        user_text = last_user.content if last_user else ""

    companion = CompanionService(svc.session, svc.audit, settings=settings, budget=svc.budget)
    resp = companion.respond(actor, c, user_text)

    m = Message(
        id=uuid4().hex,
        conversation_id=c.id,
        role="assistant",
        content=resp["content"],
        source="companion",
        client_message_id=uuid4().hex,
    )
    svc.session.add(m)
    c.updated_at = utcnow()
    svc.session.flush()
    svc.audit.append(actor, "companion.replied", m.id, {"mode": c.mode, "is_crisis": resp.get("is_crisis", False)})
    svc.session.commit()

    serialized = _serialize_message(m)
    serialized["metadata"] = resp
    return serialized
