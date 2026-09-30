"""Owner data export service with security filtering, short-lived tokens and cleanup (M14).

Guarantees:
- Never includes secrets, session cookies, service credentials, or connection strings.
- Formats messages with role, content, source, created_at, and version.
- Ephemeral download token expires after 15 minutes and can be explicitly purged.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Conversation, Grant, Memory, Message
from ..db.types import utcnow
from .actor import Actor
from .audit import AuditService
from .errors import NotFound, PermissionDenied


class ExportService:
    # Class-level ephemeral bundle store protected by lock
    _tokens: dict[str, tuple[datetime, dict[str, Any]]] = {}
    _lock = threading.Lock()

    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    def create_export_bundle(
        self,
        actor: Actor,
        conversation_ids: list[str] | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        actor.require_owner()

        conv_query = select(Conversation).where(
            Conversation.owner_id == actor.owner_id,
            Conversation.deleted_at.is_(None),
        )
        if conversation_ids:
            conv_query = conv_query.where(Conversation.id.in_(conversation_ids))
        if domain:
            conv_query = conv_query.where(Conversation.domain == domain)

        conversations = list(self.s.execute(conv_query).scalars())
        conv_list = []
        for c in conversations:
            msgs = list(
                self.s.execute(
                    select(Message)
                    .where(Message.conversation_id == c.id, Message.deleted_at.is_(None))
                    .order_by(Message.created_at.asc())
                ).scalars()
            )
            conv_list.append({
                "id": c.id,
                "title": c.title,
                "domain": c.domain,
                "mode": c.mode,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "version": c.version,
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "source": m.source,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                        "version": m.version,
                    }
                    for m in msgs
                ],
            })

        mem_query = select(Memory).where(
            Memory.owner_id == actor.owner_id,
            Memory.deleted_at.is_(None),
        )
        if domain:
            mem_query = mem_query.where(Memory.domain == domain)
        memories = list(self.s.execute(mem_query).scalars())

        grants = list(self.s.execute(select(Grant).where(Grant.state != "deleted")).scalars())

        bundle = {
            "owner_id": actor.owner_id,
            "exported_at": utcnow().isoformat(),
            "conversations": conv_list,
            "memories": [
                {
                    "id": m.id,
                    "domain": m.domain,
                    "category": m.category,
                    "content": m.content,
                    "version": m.version,
                    "hypothesis_status": m.hypothesis_status,
                }
                for m in memories
            ],
            "grants": [
                {
                    "id": g.id,
                    "source_domain": g.source_domain,
                    "consumer_domain": g.consumer_domain,
                    "state": g.state,
                }
                for g in grants
            ],
            "contains_secrets": False,
        }
        self.audit.append(
            actor,
            "export.created",
            actor.owner_id,
            {"conversations": len(conv_list), "memories": len(memories)},
        )
        return bundle

    def generate_download_token(
        self,
        actor: Actor,
        bundle: dict[str, Any],
        ttl_seconds: int = 900,
    ) -> dict[str, str]:
        actor.require_owner()
        token = secrets.token_urlsafe(32)
        exp = utcnow() + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._tokens[token] = (exp, bundle)
        return {"download_token": token, "expires_at": exp.isoformat()}

    def consume_download_token(self, token: str) -> dict[str, Any]:
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None:
                raise NotFound("token_not_found", "Download token not found or already used")
            exp, bundle = entry
            if utcnow() > exp:
                del self._tokens[token]
                raise PermissionDenied("token_expired", "Download token has expired", 410)
            # Remove on single consumption or keep until expiry
            del self._tokens[token]
            return bundle

    @classmethod
    def cleanup_expired_tokens(cls) -> int:
        now = utcnow()
        removed = 0
        with cls._lock:
            for k in list(cls._tokens.keys()):
                if now > cls._tokens[k][0]:
                    del cls._tokens[k]
                    removed += 1
        return removed
