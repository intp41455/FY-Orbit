"""Agent registration, health, leases, draining and revocation (§13.1).

Lifecycle: candidate -> registered -> healthy -> enabled -> draining -> revoked.
In-flight tasks bind an agent version via a lease. When draining starts, no new
tasks are assigned; existing leases finish or are force-released after the drain
deadline, then credentials are revoked. This is the BUG-12 lifecycle primitive
(real A2A handshake and health probes are wired by the Runtime shard later).
"""

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Agent, AgentLease, ServiceIdentity
from ..db.types import utcnow
from .actor import Actor
from .errors import Conflict, NotFound, ValidationFailed
from .audit import AuditService

DRAINABLE = {"enabled", "healthy"}


class AgentService:
    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    def register(self, actor: Actor, *, name: str, semantic_version: str, capabilities: list[str],
                 domains: list[str], endpoint_key: str, max_concurrency: int = 1) -> Agent:
        actor.require_owner()
        row = Agent(
            id=uuid4().hex, name=name, semantic_version=semantic_version, capabilities=capabilities,
            domains=domains, endpoint_key=endpoint_key, state="registered", healthy=False,
            max_concurrency=max_concurrency,
        )
        self.s.add(row)
        self.s.flush()
        self.audit.append(actor, "agent.registered", row.id, {"name": name})
        return row

    def set_health(self, actor: Actor, agent_id: str, healthy: bool) -> Agent:
        agent = self.s.get(Agent, agent_id)
        if agent is None:
            raise NotFound("agent_not_found", "Agent not found")
        agent.healthy = healthy
        if healthy and agent.state == "registered":
            agent.state = "healthy"
        self.s.flush()
        return agent

    def enable(self, actor: Actor, agent_id: str) -> Agent:
        actor.require_owner()
        agent = self.s.get(Agent, agent_id)
        if agent is None:
            raise NotFound("agent_not_found", "Agent not found")
        if not agent.healthy:
            raise Conflict("agent_unhealthy", "Enable only after a successful health probe")
        agent.state = "enabled"
        self.s.flush()
        self.audit.append(actor, "agent.enabled", agent.id)
        return agent

    def acquire_lease(self, actor: Actor, agent_id: str, task_id: str, ttl_seconds: int = 3600) -> AgentLease:
        agent = self.s.get(Agent, agent_id)
        if agent is None:
            raise NotFound("agent_not_found", "Agent not found")
        if agent.state != "enabled":
            raise Conflict("agent_not_enabled", "Cannot assign tasks to a non-enabled agent")
        active = self.s.execute(
            select(AgentLease).where(AgentLease.agent_id == agent_id, AgentLease.state == "active")
        ).scalars()
        if len(list(active)) >= agent.max_concurrency:
            raise Conflict("agent_at_capacity", "Agent is at max concurrency")
        now = utcnow()
        lease = AgentLease(
            id=uuid4().hex, agent_id=agent_id, task_id=task_id,
            starts_at=now, expires_at=now + timedelta(seconds=ttl_seconds), state="active",
        )
        self.s.add(lease)
        self.s.flush()
        self.audit.append(actor, "agent.lease_acquired", agent_id, {"task": task_id})
        return lease

    def drain(self, actor: Actor, agent_id: str) -> Agent:
        actor.require_owner()
        agent = self.s.get(Agent, agent_id)
        if agent is None:
            raise NotFound("agent_not_found", "Agent not found")
        if agent.state not in DRAINABLE:
            raise Conflict("agent_not_drainable", "Only healthy/enabled agents can drain")
        agent.state = "draining"
        agent.healthy = False
        self.s.flush()
        self.audit.append(actor, "agent.draining", agent.id)
        return agent

    def revoke(self, actor: Actor, agent_id: str) -> Agent:
        actor.require_owner()
        agent = self.s.get(Agent, agent_id)
        if agent is None:
            raise NotFound("agent_not_found", "Agent not found")
        # Expire all active leases; credentials are now invalid.
        for lease in self.s.execute(
            select(AgentLease).where(AgentLease.agent_id == agent_id, AgentLease.state == "active")
        ).scalars():
            lease.state = "revoked"
        # Revoke all associated service identities for this agent name
        svc_identities = self.s.execute(
            select(ServiceIdentity).where(ServiceIdentity.name == agent.name, ServiceIdentity.state == "active")
        ).scalars()
        for svc_ident in svc_identities:
            svc_ident.state = "revoked"
            svc_ident.revoked_at = utcnow()
        agent.state = "revoked"
        agent.healthy = False
        self.s.flush()
        self.audit.append(actor, "agent.revoked", agent.id)
        return agent
