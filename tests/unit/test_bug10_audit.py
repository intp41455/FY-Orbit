"""BUG-10: audit hash chain detects tampering, gaps and anchor mismatch."""

from find_yourself.db.models import AuditEvent
from find_yourself.services.audit import AuditService


def test_chain_verifies_when_intact(session, owner):
    a = AuditService(session)
    a.append(owner, "one", "t1")
    a.append(owner, "two", "t2")
    a.anchor()
    res = a.verify()
    assert res.ok, res.problems


def test_chain_detects_tampering(session, owner):
    a = AuditService(session)
    a.append(owner, "one", "t1")
    a.append(owner, "two", "t2")
    # Tamper with a stored event detail after the fact.
    ev = session.query(AuditEvent).filter_by(seq=2).one()
    ev.details = {"mutated": True}
    session.flush()
    res = a.verify()
    assert not res.ok
    assert any("content-hash" in p for p in res.problems)


def test_chain_detects_missing_event(session, owner):
    a = AuditService(session)
    a.append(owner, "one", "t1")
    a.append(owner, "two", "t2")
    # Anchor records the expected head (seq=2) in an independent store.
    a.anchor()
    # Delete the last event to simulate a gap / missing record.
    ev = session.query(AuditEvent).filter_by(seq=2).one()
    session.delete(ev)
    session.flush()
    res = a.verify()
    assert not res.ok
    assert any("anchor" in p or "gap" in p for p in res.problems)
