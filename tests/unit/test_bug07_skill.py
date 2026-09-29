"""BUG-07: skill promotion requires a passing evaluation bound to the immutable package hash."""

import pytest

from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, PermissionDenied
from find_yourself.services.skill import SkillService


def test_promote_requires_passing_evaluation(session, owner):
    audit = AuditService(session)
    svc = SkillService(session, audit)
    sk = svc.stage(owner, name="note-taker", semantic_version="1.0.0",
                   package={"skill_md": "---\nname: note-taker\n---\n# hi"},
                   source="internal", license_="MIT", domain="personal")
    # Failing evaluation must NOT promote.
    ev = svc.evaluate(owner, sk.id, static_passed=True, functional_passed=False)
    with pytest.raises(Conflict):
        svc.promote(owner, sk.id, ev.id)
    # Passing evaluation bound to the same package hash promotes.
    ev2 = svc.evaluate(owner, sk.id, static_passed=True, functional_passed=True)
    promoted = svc.promote(owner, sk.id, ev2.id)
    assert promoted.state == "active"


def test_agent_cannot_promote_itself(session, owner):
    from find_yourself.services.actor import Actor
    audit = AuditService(session)
    svc = SkillService(session, audit)
    sk = svc.stage(owner, name="x", semantic_version="1.0.0", package={"skill_md": "x"},
                   source="internal", license_="MIT", domain="personal")
    agent = Actor.service("agent-1", "agent")
    with pytest.raises(PermissionDenied):
        svc.promote(agent, sk.id, "nope")
