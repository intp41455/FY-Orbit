"""Cross-domain grants and the shared authorization predicate (§8).

Grants are explicit per-record, time-boxed, never wildcard (§3.2). The
authorization predicate is the single gate used by memory/conversation reads:
same-domain, explicitly-shared, or covered by an active unexpired grant. A
derived memory being readable never grants access to its raw sources — source
links are checked independently (BUG-01).
"""

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Grant, Memory
from ..db.types import utcnow
from .actor import Actor
from .errors import NotFound, ValidationFailed
from .hasher import digest
from .audit import AuditService

MAX_GRANT_SECONDS = 30 * 24 * 3600


class GrantService:
    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    def create(
        self,
        actor: Actor,
        *,
        source_domain: str,
        consumer_domain: str,
        record_ids: list[str],
        expires_at: datetime,
    ) -> Grant:
        actor.require_owner()
        now = utcnow()
        if expires_at.tzinfo is None:
            raise ValidationFailed("grant_tz", "expires_at must be timezone-aware UTC")
        if expires_at <= now or (expires_at - now).total_seconds() > MAX_GRANT_SECONDS:
            raise ValidationFailed("grant_expiry", "Grant expiry must be in the future and <= 30 days")
        if not record_ids or not isinstance(record_ids, list):
            raise ValidationFailed("grant_scope", "Explicit record IDs required; wildcard is prohibited")
        if source_domain == consumer_domain:
            raise ValidationFailed("grant_self", "Grant is only needed across domains")
        # Every referenced record must actually live in the source domain.
        for rid in record_ids:
            row = self.s.get(Memory, rid)
            if row is None or row.domain != source_domain or row.deleted_at is not None:
                raise ValidationFailed("grant_scope", f"Record {rid} not an active {source_domain} record")
        g = Grant(
            id=uuid4().hex, source_domain=source_domain, consumer_domain=consumer_domain,
            record_ids=record_ids, expires_at=expires_at, state="active",
            scope_hash=digest({"source": source_domain, "consumer": consumer_domain,
                               "records": sorted(record_ids), "exp": expires_at}),
        )
        self.s.add(g)
        self.s.flush()
        self.audit.append(actor, "grant.created", g.id,
                          {"source": source_domain, "consumer": consumer_domain, "n": len(record_ids)})
        return g

    def revoke(self, actor: Actor, grant_id: str) -> Grant:
        actor.require_owner()
        g = self.s.get(Grant, grant_id)
        if g is None:
            raise NotFound("grant_not_found", "Grant not found")
        g.state = "revoked"
        g.revoked_at = utcnow()
        self.audit.append(actor, "grant.revoked", g.id)
        self.s.flush()
        return g

    def is_authorized(self, *, record_domain: str, record_id: str, consumer_domain: str) -> bool:
        now = utcnow()
        if record_domain == consumer_domain:
            return True
        if record_domain == "shared":
            # shared content is readable by other domains only if explicitly endorsed/shared.
            return True
        rows = self.s.execute(
            select(Grant).where(
                Grant.state == "active",
                Grant.source_domain == record_domain,
                Grant.consumer_domain == consumer_domain,
                Grant.expires_at > now,
            )
        ).scalars()
        for g in rows:
            if record_id in (g.record_ids or []):
                return True
        return False
