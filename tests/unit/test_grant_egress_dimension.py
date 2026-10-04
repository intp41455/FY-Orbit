"""Grant egress-destination axis (ruling 4A / GAP-T1-2).

Lands together with:
  - migrations/versions/0006_grant_egress_destination.py
  - db/models.py            : GRANT_DESTINATIONS + Grant.destination + ck_grant_destination
  - services/grant.py       : create(destination=...) + is_egress_authorized()
                             + is_authorized() destination filter
  - services/canvas.py      : inline grant check destination filter

Design contract under test (spec: opencode_grant_egress_dimension_spec_20261003.md):

  1. Same grants table, same GrantService, same audit chain. No second
     authorization platform.
  2. Egress NEVER short-circuits on same-domain. Ruling 4A makes the cloud
     drive an explicitly-authorized destination, so even the owner's own
     personal records need a real grant to leave the machine.
  3. destination must participate in scope_hash, otherwise an internal grant
     and an egress grant over the same scope hash identically.
  4. THREE isolation points, not one: is_authorized(), canvas.py's inline
     check, and export.py's grant listing. Missing canvas.py is the dangerous
     one: an egress grant replayed there would widen cross-domain read
     authority (that is what test_9b below locks down).

Conventions copied from tests/conftest.py and tests/unit/test_bug11_search.py.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from find_yourself.db.models import AuditEvent, Grant, Memory
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.canvas import CanvasService
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.grant import GrantService
from find_yourself.services.memory import MemoryService


def _grant_svc(session, audit) -> GrantService:
    return GrantService(session, audit)


def _mem_svc(session, audit) -> tuple[MemoryService, GrantService]:
    grants = _grant_svc(session, audit)
    return MemoryService(session, grants, audit), grants


# --------------------------------------------------------------------------
# 1. issuing an egress grant
# --------------------------------------------------------------------------
def test_egress_grant_records_destination_and_audits_it(session, audit, owner):
    grants = _grant_svc(session, audit)
    mem = Memory(id="mem-e1", owner_id="owner-1", domain="personal",
                 category="self_report", content="偏好本地优先",
                 content_hash="h-e1", active=True)
    session.add(mem)
    session.flush()

    g = grants.create(owner, source_domain="personal", consumer_domain="personal",
                      record_ids=[mem.id], expires_at=utcnow() + timedelta(days=7),
                      destination="gdrive")
    assert g.destination == "gdrive"

    events = session.execute(
        select(AuditEvent).where(AuditEvent.action == "grant.created")
    ).scalars().all()
    assert events, "grant.created must leave an audit trail"
    assert any(e.details.get("destination") == "gdrive" for e in events), \
        "audit details must record the egress destination"


# --------------------------------------------------------------------------
# 2-4. existing grant constraints still apply to egress grants
# --------------------------------------------------------------------------
def test_egress_grant_rejects_empty_record_ids(session, audit, owner):
    grants = _grant_svc(session, audit)
    with pytest.raises(ValidationFailed) as ei:
        grants.create(owner, source_domain="personal", consumer_domain="work",
                      record_ids=[], expires_at=utcnow() + timedelta(days=7),
                      destination="gdrive")
    assert ei.value.code == "grant_scope"


def test_egress_grant_rejects_expiry_beyond_30_days(session, audit, owner):
    grants = _grant_svc(session, audit)
    mem = Memory(id="mem-e2", owner_id="owner-1", domain="personal",
                 category="note", content="x", content_hash="h-e2", active=True)
    session.add(mem)
    session.flush()
    with pytest.raises(ValidationFailed) as ei:
        grants.create(owner, source_domain="personal", consumer_domain="work",
                      record_ids=[mem.id], expires_at=utcnow() + timedelta(days=31),
                      destination="gdrive")
    assert ei.value.code == "grant_expiry"


def test_egress_grant_rejects_deleted_or_foreign_record(session, audit, owner):
    grants = _grant_svc(session, audit)
    deleted = Memory(id="mem-e3", owner_id="owner-1", domain="personal",
                     category="note", content="x", content_hash="h-e3",
                     active=False, deleted_at=utcnow())
    foreign = Memory(id="mem-e4", owner_id="owner-1", domain="work",
                     category="note", content="x", content_hash="h-e4", active=True)
    session.add_all([deleted, foreign])
    session.flush()
    for bad in (deleted.id, foreign.id):
        with pytest.raises(ValidationFailed) as ei:
            grants.create(owner, source_domain="personal", consumer_domain="work",
                          record_ids=[bad], expires_at=utcnow() + timedelta(days=7),
                          destination="gdrive")
        assert ei.value.code == "grant_scope"


# --------------------------------------------------------------------------
# 5. THE decisive semantic change: grant_self must not fire for egress.
#    Without this, ruling 4A is unimplementable -- the owner's own personal
#    data going to the owner's own Drive is same-domain by definition.
# --------------------------------------------------------------------------
def test_same_domain_egress_grant_is_allowed(session, audit, owner):
    grants = _grant_svc(session, audit)
    mem = Memory(id="mem-e5", owner_id="owner-1", domain="personal",
                 category="self_report", content="x", content_hash="h-e5", active=True)
    session.add(mem)
    session.flush()
    g = grants.create(owner, source_domain="personal", consumer_domain="personal",
                      record_ids=[mem.id], expires_at=utcnow() + timedelta(days=7),
                      destination="gdrive")
    assert g.destination == "gdrive"


def test_same_domain_internal_grant_still_rejected(session, audit, owner):
    """Regression lock for the pre-existing behaviour (internal path unchanged)."""
    grants = _grant_svc(session, audit)
    mem = Memory(id="mem-e6", owner_id="owner-1", domain="personal",
                 category="note", content="x", content_hash="h-e6", active=True)
    session.add(mem)
    session.flush()
    with pytest.raises(ValidationFailed) as ei:
        grants.create(owner, source_domain="personal", consumer_domain="personal",
                      record_ids=[mem.id], expires_at=utcnow() + timedelta(days=7))
    assert ei.value.code == "grant_self"


# --------------------------------------------------------------------------
# 6-8. is_egress_authorized denies everything it should
# --------------------------------------------------------------------------
def test_egress_denied_without_grant(session, audit, owner):
    grants = _grant_svc(session, audit)
    assert grants.is_egress_authorized(
        record_domain="personal", record_id="mem-none", destination="gdrive") is False


def test_egress_denied_for_record_outside_grant(session, audit, owner):
    grants = _grant_svc(session, audit)
    inside = Memory(id="mem-in", owner_id="owner-1", domain="personal",
                    category="note", content="x", content_hash="h-in", active=True)
    outside = Memory(id="mem-out", owner_id="owner-1", domain="personal",
                     category="note", content="x", content_hash="h-out", active=True)
    session.add_all([inside, outside])
    session.flush()
    grants.create(owner, source_domain="personal", consumer_domain="personal",
                  record_ids=[inside.id], expires_at=utcnow() + timedelta(days=7),
                  destination="gdrive")
    assert grants.is_egress_authorized(
        record_domain="personal", record_id=outside.id, destination="gdrive") is False


def test_egress_denied_when_revoked_or_expired(session, audit, owner):
    grants = _grant_svc(session, audit)
    mem = Memory(id="mem-e7", owner_id="owner-1", domain="personal",
                 category="note", content="x", content_hash="h-e7", active=True)
    session.add(mem)
    session.flush()

    revoked = grants.create(owner, source_domain="personal", consumer_domain="personal",
                            record_ids=[mem.id], expires_at=utcnow() + timedelta(days=7),
                            destination="gdrive")
    grants.revoke(owner, revoked.id)
    assert grants.is_egress_authorized(
        record_domain="personal", record_id=mem.id, destination="gdrive") is False

    # create() rejects a past expiry outright, so age the row to simulate time passing.
    live = grants.create(owner, source_domain="personal", consumer_domain="personal",
                         record_ids=[mem.id], expires_at=utcnow() + timedelta(days=7),
                         destination="gdrive")
    session.execute(
        update(Grant).where(Grant.id == live.id)
        .values(expires_at=utcnow() - timedelta(days=1))
    )
    session.flush()
    session.expire_all()
    assert grants.is_egress_authorized(
        record_domain="personal", record_id=mem.id, destination="gdrive") is False


# --------------------------------------------------------------------------
# 9. an egress grant must NOT widen internal read authority
# --------------------------------------------------------------------------
def test_egress_grant_does_not_authorize_internal_read(session, audit, owner):
    mem_svc, grants = _mem_svc(session, audit)
    secret = mem_svc.upsert(owner, owner_id="owner-1", domain="personal",
                            category="self_report", content="salary secret egress",
                            source_ids=[])
    other = mem_svc.upsert(owner, owner_id="owner-1", domain="personal",
                           category="self_report", content="salary secret unrelated",
                           source_ids=[])

    # Egress grant: same shape as a cross-domain read grant, different destination.
    grants.create(owner, source_domain="personal", consumer_domain="work",
                  record_ids=[secret.id], expires_at=utcnow() + timedelta(days=7),
                  destination="gdrive")
    assert all(h["id"] != secret.id for h in mem_svc.search("work", "salary", owner_id="owner-1")), \
        "egress grant leaked into internal read authorization"

    # An internal grant over the same scope still works.
    grants.create(owner, source_domain="personal", consumer_domain="work",
                  record_ids=[secret.id], expires_at=utcnow() + timedelta(days=7),
                  destination="internal")
    ids = {h["id"] for h in mem_svc.search("work", "salary", owner_id="owner-1")}
    assert secret.id in ids
    assert other.id not in ids


# --------------------------------------------------------------------------
# 10-11. directionality of the two authorization kinds
# --------------------------------------------------------------------------
def test_internal_grant_does_not_authorize_egress(session, audit, owner):
    grants = _grant_svc(session, audit)
    mem = Memory(id="mem-e8", owner_id="owner-1", domain="personal",
                 category="note", content="x", content_hash="h-e8", active=True)
    session.add(mem)
    session.flush()
    grants.create(owner, source_domain="personal", consumer_domain="work",
                  record_ids=[mem.id], expires_at=utcnow() + timedelta(days=7),
                  destination="internal")
    assert grants.is_egress_authorized(
        record_domain="personal", record_id=mem.id, destination="gdrive") is False


def test_unknown_destination_rejected(session, audit, owner):
    grants = _grant_svc(session, audit)
    mem = Memory(id="mem-e9", owner_id="owner-1", domain="personal",
                 category="note", content="x", content_hash="h-e9", active=True)
    session.add(mem)
    session.flush()
    with pytest.raises(ValidationFailed) as ei:
        grants.create(owner, source_domain="personal", consumer_domain="personal",
                      record_ids=[mem.id], expires_at=utcnow() + timedelta(days=7),
                      destination="baidu")
    assert ei.value.code == "grant_destination"


# --------------------------------------------------------------------------
# 12. scope_hash must separate internal from egress
# --------------------------------------------------------------------------
def test_scope_hash_differs_across_destination(session, audit, owner):
    grants = _grant_svc(session, audit)
    mem = Memory(id="mem-e10", owner_id="owner-1", domain="personal",
                 category="note", content="x", content_hash="h-e10", active=True)
    session.add(mem)
    session.flush()
    exp = utcnow() + timedelta(days=7)
    internal = grants.create(owner, source_domain="personal", consumer_domain="work",
                             record_ids=[mem.id], expires_at=exp, destination="internal")
    egress = grants.create(owner, source_domain="personal", consumer_domain="work",
                           record_ids=[mem.id], expires_at=exp, destination="gdrive")
    assert internal.scope_hash != egress.scope_hash, \
        "internal and egress grants hash identically -> semantics indistinguishable"


# --------------------------------------------------------------------------
# 13-14. database level
# --------------------------------------------------------------------------
def test_db_check_rejects_unknown_destination(session):
    session.add(Grant(id="g-bad-dest", source_domain="personal", consumer_domain="work",
                      record_ids=["x"], expires_at=utcnow() + timedelta(days=7),
                      state="active", scope_hash="h", destination="baidu"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_grant_constructed_without_destination_defaults_to_internal(session):
    """Existing fixtures (test_canvas_service, test_bug08_constraints) omit destination."""
    session.add(Grant(id="g-default-dest", source_domain="personal",
                      consumer_domain="work", record_ids=["x"],
                      expires_at=utcnow() + timedelta(days=7), state="active",
                      scope_hash="h"))
    session.flush()
    assert session.get(Grant, "g-default-dest").destination == "internal"


# --------------------------------------------------------------------------
# 9b. THE high-risk regression lock.
#     canvas.py validates grants inline and does NOT call is_authorized().
#     Without a destination filter there, an egress grant ("you may export
#     personal data to your Drive") could be replayed to authorize a work-domain
#     canvas ingesting personal memories -- an actual privilege escalation that
#     this extension would otherwise create.
# --------------------------------------------------------------------------
def test_9b_egress_grant_cannot_be_replayed_for_cross_domain_ingestion(session, audit, owner):
    from find_yourself.db.models import Task

    canvas = CanvasService(session, audit)
    root_task = Task(id="task-egress-9b", owner_id=owner.owner_id,
                     goal="root", domain="work", status="running",
                     deadline=utcnow() + timedelta(hours=2),
                     idempotency_key="idem-task-egress-9b")
    session.add(root_task)
    session.flush()
    inst_work = canvas.create_instance(owner, project_name="egress replay", template_id="work")
    session.add(Memory(id="rec-egress-9b", owner_id=owner.owner_id, domain="personal",
                       category="note", content="绝密个人财务预估",
                       content_hash="h-9b", active=True))
    session.flush()

    egress = Grant(id="grant-egress-9b", source_domain="personal", consumer_domain="work",
                   record_ids=["rec-egress-9b"], expires_at=utcnow() + timedelta(days=5),
                   state="active", scope_hash="h-9b", destination="gdrive")
    session.add(egress)
    session.flush()

    with pytest.raises(ValidationFailed) as ei:
        canvas.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="借上云授权跨域摄取个人记忆",
            input_ref={"domain": "personal", "grant_id": "grant-egress-9b",
                       "record_id": "rec-egress-9b"},
        )
    assert ei.value.code == "grant_destination"

    # Control: an internal grant over the same scope is still accepted by canvas.
    internal = Grant(id="grant-internal-9b", source_domain="personal",
                     consumer_domain="work", record_ids=["rec-egress-9b"],
                     expires_at=utcnow() + timedelta(days=5), state="active",
                     scope_hash="h-9b-internal", destination="internal")
    session.add(internal)
    session.flush()
    canvas.dispatch_subtask(
        actor=owner,
        instance_id=inst_work.id,
        root_task_id=root_task.id,
        worker_id="EngineeringAgent",
        goal="合法跨域摄取",
        input_ref={"domain": "personal", "grant_id": "grant-internal-9b",
                   "record_id": "rec-egress-9b"},
    )


# --------------------------------------------------------------------------
# 15. end-to-end: authorize once, judge per record, audit once
# --------------------------------------------------------------------------
def test_egress_round_trip(session, audit, owner):
    grants = _grant_svc(session, audit)
    mem = Memory(id="mem-e15", owner_id="owner-1", domain="personal",
                 category="self_report", content="冷数据归档候选", content_hash="h-e15",
                 active=True)
    session.add(mem)
    session.flush()

    before = len(session.execute(
        select(AuditEvent).where(AuditEvent.action == "grant.created")).scalars().all())
    g = grants.create(owner, source_domain="personal", consumer_domain="personal",
                      record_ids=[mem.id], expires_at=utcnow() + timedelta(days=7),
                      destination="gdrive")
    assert grants.is_egress_authorized(
        record_domain="personal", record_id=mem.id, destination="gdrive") is True
    after = len(session.execute(
        select(AuditEvent).where(AuditEvent.action == "grant.created")).scalars().all())
    assert after == before + 1, "authorizing once must leave exactly one audit record"
    assert g.destination == "gdrive"