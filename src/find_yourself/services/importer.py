"""Historical conversation importer with deduplication and prompt isolation (M12, M13).

Guarantees:
- Idempotent deduplication on (conversation_id, client_message_id).
- M13 prompt isolation: historical messages are NEVER given role='system'.
  They are strictly historical material (role='user' or 'assistant') with
  source='import:person-kb:<platform>'. Prompt injection in historical text
  remains passive inert text and has zero effect on permissions or system prompts.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.person_kb import PersonKbAdapter
from ..db.models import Conversation, Message
from .actor import Actor
from .audit import AuditService


class HistoryImportService:
    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    def import_from_adapter(
        self,
        actor: Actor,
        adapter: PersonKbAdapter,
        platforms: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        actor.require_owner()

        imported_sessions = 0
        imported_messages = 0
        skipped_duplicates = 0

        for sess_dict in adapter.iter_conversations(platforms=platforms, limit=limit):
            platform = sess_dict["platform"]
            raw_sess_id = sess_dict["id"]
            title = sess_dict["title"] or f"Imported {platform} session"

            # Deterministic/stable mapping of imported conversation
            _ = f"person-kb:{platform}:{raw_sess_id}"
            conv = self.s.execute(
                select(Conversation).where(
                    Conversation.owner_id == actor.owner_id,
                    Conversation.title == title,
                    Conversation.domain == "personal",
                )
            ).scalars().first()

            if conv is None:
                conv = Conversation(
                    id=uuid4().hex,
                    owner_id=actor.owner_id,
                    title=title,
                    domain="personal",
                    mode="explore",
                )
                self.s.add(conv)
                self.s.flush()
                imported_sessions += 1

            for m in sess_dict["messages"]:
                raw_msg_id = m["id"]
                client_msg_id = f"person-kb:{platform}:{raw_sess_id}:{raw_msg_id}"

                # Idempotency check: duplicate prevention
                existing_msg = self.s.execute(
                    select(Message).where(
                        Message.conversation_id == conv.id,
                        Message.client_message_id == client_msg_id,
                    )
                ).scalar_one_or_none()

                if existing_msg is not None:
                    skipped_duplicates += 1
                    continue

                # M13 Prompt isolation: Role strictly user or assistant; NEVER system.
                role = "user" if m["role"] == "user" else "assistant"
                content = m["content"]
                source = f"import:person-kb:{platform}"

                msg_row = Message(
                    id=uuid4().hex,
                    conversation_id=conv.id,
                    role=role,
                    content=content,
                    source=source,
                    client_message_id=client_msg_id,
                )
                self.s.add(msg_row)
                imported_messages += 1

            self.s.flush()

        self.audit.append(
            actor,
            "history.imported",
            "person-kb",
            {
                "imported_sessions": imported_sessions,
                "imported_messages": imported_messages,
                "skipped_duplicates": skipped_duplicates,
            },
        )
        return {
            "imported_sessions": imported_sessions,
            "imported_messages": imported_messages,
            "skipped_duplicates": skipped_duplicates,
        }
