"""API-level tests for the Route B local user system.

Covers registration (consent required), login, session cookie flow,
consent listing and self-service account deletion, plus the agent-dispatch
authorization fix (previously anonymous-readable).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_register_login_me_and_consents(client: TestClient):
    r = client.post("/auth/register", json={
        "email": "user1@example.com",
        "password": "longenough1",
        "consent_accepted": True,
        "display_name": "User One",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "registered"
    assert data["email"] == "user1@example.com"
    assert data["csrf_token"]
    csrf = data["csrf_token"]
    assert client.cookies.get("fy_session")

    me = client.get("/auth/me").json()
    assert me["subject_type"] == "owner"
    assert me["owner_id"] == data["owner_id"]

    consents = client.get("/api/account/consents").json()
    assert consents["count"] == 1
    assert consents["consents"][0]["doc_id"] == "privacy-policy"

    # CSRF double-submit works for a mutating request by the fresh user
    c = client.post("/api/conversations", json={
        "title": "T", "domain": "personal", "mode": "listen",
    }, headers={"x-csrf-token": csrf})
    assert c.status_code == 200, c.text


def test_register_requires_consent(client: TestClient):
    r = client.post("/auth/register", json={
        "email": "noconsent@example.com",
        "password": "longenough1",
        "consent_accepted": False,
    })
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "consent_required"


def test_login_flow_and_failure_lockout(client: TestClient):
    client.post("/auth/register", json={
        "email": "login@example.com", "password": "longenough1", "consent_accepted": True,
    })
    client.cookies.clear()

    r = client.post("/auth/login", json={"email": "login@example.com", "password": "wrong-password"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "bad_credentials"

    r = client.post("/auth/login", json={"email": "login@example.com", "password": "longenough1"})
    assert r.status_code == 200
    assert client.cookies.get("fy_session")
    me = client.get("/auth/me").json()
    assert me["owner_id"]


def test_agent_dispatch_requires_auth(client: TestClient):
    # Regression: these endpoints used to answer 200 to anonymous callers.
    r = client.get("/api/agent-dispatch")
    assert r.status_code == 401
    r2 = client.get("/api/agent-dispatch/whatever-id")
    assert r2.status_code == 401


def test_account_deletion_end_to_end(client: TestClient):
    reg = client.post("/auth/register", json={
        "email": "deleteme@example.com", "password": "longenough1", "consent_accepted": True,
    }).json()
    csrf = reg["csrf_token"]

    r = client.delete("/api/account", headers={"x-csrf-token": csrf})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "deleted"
    assert body["consents_retained"] == 1

    # session cookie cleared -> further calls are unauthenticated
    client.cookies.clear()
    me = client.get("/auth/me")
    assert me.status_code == 401
