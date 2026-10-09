"""S04, S05, S06, S08, S11: self-reported identity, agent self-approval, digest tampering, idempotent decision, audit chain."""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner
from sqlalchemy.orm import sessionmaker

from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.auth import AuthService


def _make_service_token(session_maker: sessionmaker) -> str:
    s = session_maker()
    audit = AuditService(s)
    auth = AuthService(s, audit, environment="test", local_token="x")
    owner = Actor.owner("owner-1")
    reg = auth.register_service(owner, kind="agent", name="researcher-1", domains=["work"])
    token = reg.plaintext_token
    s.commit()
    s.close()
    return token


def test_s04_owner_id_comes_from_session_not_body(client: TestClient, session_maker):
    headers = login_owner(client)
    # Request body tries to claim a different owner / domain; extra fields are
    # forbidden and ownership is bound to the authenticated session.
    r = client.post("/api/conversations",
                    json={"title": "mine", "domain": "personal", "owner_id": "attacker"},
                    headers=headers)
    # extra=forbid -> 422; regardless, no body field promotes identity.
    assert r.status_code in (401, 403, 422)


def test_s04_session_owner_is_server_bound(client: TestClient, session_maker):
    headers = login_owner(client)
    r = client.post("/api/conversations", json={"title": "t", "domain": "personal"}, headers=headers)
    assert r.status_code == 200
    # The row owner_id is the authenticated owner, not a body claim.
    s = session_maker()
    from find_yourself.db.models import Conversation
    c = s.get(Conversation, r.json()["id"])
    assert c.owner_id == "owner"
    s.close()


def test_s05_agent_service_cannot_decide_proposal(client: TestClient, session_maker):
    headers = login_owner(client)
    # Owner creates a proposal.
    r = client.post("/api/proposals", json={
        "operation": "memory.upsert", "payload": {"x": 1}, "reason": "because",
        "rollback": "undo", "expires_in_minutes": 30,
    }, headers=headers)
    assert r.status_code == 200, r.text
    proposal = r.json()

    token = _make_service_token(session_maker)
    # Agent (service bearer) tries to approve its own-visible proposal -> 403.
    rr = client.post(f"/api/proposals/{proposal['id']}/decision",
                    json={"digest": proposal["digest"], "decision": "approve"},
                    headers={"Authorization": f"Bearer {token}"})
    assert rr.status_code == 403
    assert rr.json()["error"]["code"] == "owner_only"


def test_s06_modified_payload_old_digest_rejected(client: TestClient, session_maker):
    headers = login_owner(client)
    r = client.post("/api/proposals", json={
        "operation": "memory.upsert", "payload": {"x": 1}, "reason": "because",
        "rollback": "undo", "expires_in_minutes": 30,
    }, headers=headers)
    proposal = r.json()
    # Re-submit decision with a tampered digest.
    rr = client.post(f"/api/proposals/{proposal['id']}/decision",
                    json={"digest": "0" * 64, "decision": "approve"}, headers=headers)
    assert rr.status_code == 409
    assert rr.json()["error"]["code"] == "digest_mismatch"


def test_s08_duplicate_decision_no_double_execution(client: TestClient, session_maker):
    headers = login_owner(client)
    r = client.post("/api/proposals", json={
        "operation": "memory.upsert", "payload": {"x": 1}, "reason": "because",
        "rollback": "undo", "expires_in_minutes": 30,
    }, headers=headers)
    proposal = r.json()
    good = {"digest": proposal["digest"], "decision": "approve"}
    first = client.post(f"/api/proposals/{proposal['id']}/decision", json=good, headers=headers)
    assert first.status_code == 200
    # Second decision on the now-decided proposal is rejected.
    second = client.post(f"/api/proposals/{proposal['id']}/decision", json=good, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_decided"


def test_s08_reject_then_approve_no_execution(client: TestClient, session_maker):
    headers = login_owner(client)
    r = client.post("/api/proposals", json={
        "operation": "memory.upsert", "payload": {"x": 1}, "reason": "because",
        "rollback": "undo", "expires_in_minutes": 30,
    }, headers=headers)
    proposal = r.json()
    reject = client.post(f"/api/proposals/{proposal['id']}/decision",
                        json={"digest": proposal["digest"], "decision": "reject"}, headers=headers)
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"
    approve = client.post(f"/api/proposals/{proposal['id']}/decision",
                        json={"digest": proposal["digest"], "decision": "approve"}, headers=headers)
    assert approve.status_code == 409


def test_s11_audit_chain_verify(client: TestClient, session_maker):
    headers = login_owner(client)
    # Produce a few audited events.
    client.post("/api/conversations", json={"title": "a", "domain": "personal"}, headers=headers)
    client.post("/api/conversations", json={"title": "b", "domain": "personal"}, headers=headers)
    r = client.get("/api/internal/audit/verify", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["checked"] >= 3
    assert body["problems"] == []


def test_s11_audit_detection_of_tampering(client: TestClient, session_maker):
    headers = login_owner(client)
    client.post("/api/conversations", json={"title": "a", "domain": "personal"}, headers=headers)
    # Tamper with a stored audit event content hash directly in the DB.
    s = session_maker()
    from find_yourself.db.models import AuditEvent
    ev = s.query(AuditEvent).order_by(AuditEvent.seq.desc()).first()
    ev.details = {"tampered": True}
    s.commit()
    s.close()
    r = client.get("/api/internal/audit/verify", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert any("hash" in p for p in body["problems"])
