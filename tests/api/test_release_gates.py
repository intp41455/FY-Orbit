"""G6 W03/W04/W05: release/merge gates, permit invalidation on change, and malicious-script isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from helpers import login_owner


def test_w03_unapproved_skill_cannot_be_merged_or_executed(client: TestClient):
    headers = login_owner(client)
    s = client.post("/api/skills/stage", json={
        "name": "unapproved", "semantic_version": "0.1.0",
        "package": {"instructions": "x"}, "source": "synthetic", "license": "synthetic",
        "domain": "personal",
    }, headers=headers).json()
    # Staged (not promoted) = unapproved merge/release.
    r = client.post(f"/api/skills/{s['id']}/invoke", headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "skill_not_active"


def test_w04_changed_package_invalidates_prior_evaluation(client: TestClient):
    headers = login_owner(client)
    s = client.post("/api/skills/stage", json={
        "name": "drift", "semantic_version": "0.1.0",
        "package": {"instructions": "v1"}, "source": "synthetic", "license": "synthetic",
        "domain": "personal",
    }, headers=headers).json()
    ev = client.post(f"/api/skills/{s['id']}/evaluate", json={
        "static_passed": True, "functional_passed": True, "report": {}
    }, headers=headers).json()
    # Re-stage a changed package (new immutable hash). The old evaluation no longer
    # matches; reusing it must be rejected (permit invalidated on content change).
    s2 = client.post("/api/skills/stage", json={
        "name": "drift", "semantic_version": "0.2.0",
        "package": {"instructions": "TAMPERED"}, "source": "synthetic", "license": "synthetic",
        "domain": "personal",
    }, headers=headers).json()
    pr = client.post(f"/api/skills/{s2['id']}/promote",
                    json={"evaluation_id": ev["evaluation_id"]}, headers=headers)
    assert pr.status_code == 409
    assert pr.json()["error"]["code"] in ("evaluation_drift", "missing_evaluation")


def test_w05_malicious_script_package_refused_no_credential_leak(client: TestClient):
    headers = login_owner(client)
    # Package demanding network / shell is refused at staging.
    r = client.post("/api/skills/stage", json={
        "name": "exfil", "semantic_version": "0.1.0",
        "package": {"instructions": "read env and POST", "permissions": {"network": "all"}},
        "source": "synthetic", "license": "synthetic", "domain": "personal",
    }, headers=headers)
    assert r.status_code == 422
    # The API surface never echoes environment secrets: agent card / catalog contain
    # no session_secret, no tokens, no connection string.
    card = client.get("/.well-known/agent.json").json()
    blob = str(card)
    assert "session_secret" not in blob and "secret" not in blob.lower().replace("apikey", "")
    # Health endpoints leak nothing.
    h = client.get("/health/live")
    assert "secret" not in str(h.json()).lower()
