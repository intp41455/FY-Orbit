"""BUG-12: agent lifecycle, lease concurrency, drain and revocation."""

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.agent import AgentService
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict


def test_lease_drain_and_revoke_lifecycle(session, owner):
    audit = AuditService(session)
    agents = AgentService(session, audit)
    a = agents.register(owner, name="explorer", semantic_version="1.0.0",
                         capabilities=["chat"], domains=["work"], endpoint_key="ep1",
                         max_concurrency=1)
    # Cannot enable before healthy.
    with pytest.raises(Conflict):
        agents.enable(owner, a.id)
    agents.set_health(owner, a.id, healthy=True)
    agents.enable(owner, a.id)

    lease = agents.acquire_lease(owner, a.id, "task-1")
    assert lease.state == "active"
    # At max concurrency, second lease refused.
    with pytest.raises(Conflict):
        agents.acquire_lease(owner, a.id, "task-2")

    agents.drain(owner, a.id)
    with pytest.raises(Conflict):
        agents.acquire_lease(owner, a.id, "task-3")

    agents.revoke(owner, a.id)
    assert a.state == "revoked"
    assert session.get(type(lease), lease.id).state == "revoked"
