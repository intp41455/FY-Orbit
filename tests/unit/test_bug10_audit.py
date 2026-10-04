"""BUG-10: audit hash chain detects tampering, gaps and anchor mismatch.

R39 note: anchors used to be written to and read back from the primary
``audit_anchors`` table, so a single write grant could rewrite both the chain and
its anchor and verification still passed. Anchors now live in an independent
retention store (``find_yourself.services.anchor_store``).

These tests inject a ``FileAnchorStore`` rooted in ``tmp_path`` rather than
relying on the ``~/.find-yourself/anchors/`` default. That is required for
correctness, not convenience: a shared default directory would leak anchor state
between tests (a stale higher-seq anchor from an earlier test would make
``test_chain_verifies_when_intact`` fail spuriously) and would write into the
developer's home directory during test runs. The assertions themselves are
unchanged; ``test_anchor_is_written_outside_primary_db`` is added to pin the R39
behaviour.
"""

import pytest

from find_yourself.db.models import AuditAnchor, AuditEvent
from find_yourself.db.types import utcnow
from find_yourself.services.anchor_store import FileAnchorStore, NullAnchorStore
from find_yourself.services.audit import AuditService


@pytest.fixture()
def anchor_store(tmp_path):
    return FileAnchorStore(tmp_path / "anchors")


@pytest.fixture()
def a(session, anchor_store):
    return AuditService(session, anchor_store=anchor_store)


def test_chain_verifies_when_intact(a, session, owner):
    a.append(owner, "one", "t1")
    a.append(owner, "two", "t2")
    a.anchor()
    res = a.verify()
    assert res.ok, res.problems
    assert res.anchored is True


def test_chain_detects_tampering(a, session, owner):
    a.append(owner, "one", "t1")
    a.append(owner, "two", "t2")
    # Tamper with a stored event detail after the fact.
    ev = session.query(AuditEvent).filter_by(seq=2).one()
    ev.details = {"mutated": True}
    session.flush()
    res = a.verify()
    assert not res.ok
    assert any("content-hash" in p for p in res.problems)


def test_chain_detects_missing_event(a, session, owner):
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


def test_anchor_is_written_outside_primary_db(a, session, owner, anchor_store):
    """R39: anchor() must not write to the primary ``audit_anchors`` table."""
    a.append(owner, "one", "t1")
    record = a.anchor()
    session.flush()
    assert session.query(AuditAnchor).count() == 0
    # ...and the anchor really is in the external store.
    assert anchor_store.latest() is not None
    assert record.head_hash == a._head()[1]
    assert record.storage == anchor_store.location()


def test_anchor_evidence_records_real_provenance(a, owner):
    """The evidence dict must state facts, not an unfulfilled promise."""
    a.append(owner, "one", "t1")
    record = a.anchor()
    ev = record.evidence
    assert ev["note"] == "anchor written to external store; primary-DB write path no longer used"
    assert ev["storage"] == record.storage
    assert ev["store_class"] == "FileAnchorStore"
    assert ev["written_at"]
    assert len(ev["anchor_content_hash"]) == 64
    assert ev["contains_private_text"] is False


def test_verify_without_anchor_is_not_a_mismatch(session, owner):
    """No anchor available means 'no evidence', not 'tampering' (R39)."""
    a = AuditService(session, anchor_store=NullAnchorStore())
    a.append(owner, "one", "t1")
    a.anchor()  # discarded by NullAnchorStore
    res = a.verify()
    assert res.ok, res.problems
    assert res.problems == []
    assert res.anchored is False


def test_verify_detects_anchor_rewritten_in_primary_db(a, session, owner):
    """The R39 attack: rewriting the chain AND planting a fake in-DB anchor.

    Before the fix, ``anchor()`` wrote to ``audit_anchors`` and ``verify()`` read
    it back, so an attacker with primary-DB write access could make both sides
    agree. Now the in-DB table is ignored entirely, so a planted row cannot
    rescue a tampered chain — the real anchor in the external store still
    disagrees with it.
    """
    a.append(owner, "one", "t1")
    a.append(owner, "two", "t2")
    a.anchor()

    # Attacker with full primary-DB write access rewrites the chain tail...
    ev = session.query(AuditEvent).filter_by(seq=2).one()
    ev.details = {"mutated": True}
    session.flush()
    # ...and plants a matching anchor in the primary DB to cover their tracks.
    session.add(AuditAnchor(
        id="forged", seq=2, storage="local-backup",
        head_hash=ev.hash, evidence={}, created_at=utcnow(),
    ))
    session.flush()

    res = a.verify()
    assert not res.ok
    assert any("content-hash" in p for p in res.problems)
