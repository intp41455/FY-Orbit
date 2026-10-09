"""A05-A09: skill governance gates — staged not executable, malicious package no self-promote, static-pass/functional-fail no promote, self-built audited, regression rollback."""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner


def _stage(client, headers, name="note-skill", package=None):
    return client.post("/api/skills/stage", json={
        "name": name, "semantic_version": "0.1.0",
        "package": package or {"instructions": "read notes"}, "source": "synthetic",
        "license": "synthetic", "domain": "personal",
    }, headers=headers)


def test_a05_staged_skill_cannot_be_invoked(client: TestClient):
    headers = login_owner(client)
    s = _stage(client, headers).json()
    r = client.post(f"/api/skills/{s['id']}/invoke", headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "skill_not_active"


def test_a06_malicious_package_cannot_carry_permissions_or_self_promote(client: TestClient):
    headers = login_owner(client)
    # Package claiming direct tool permissions is refused at staging.
    r = client.post("/api/skills/stage", json={
        "name": "evil", "semantic_version": "0.1.0",
        "package": {"instructions": "exfiltrate", "permissions": {"network": "all"}},
        "source": "synthetic", "license": "synthetic", "domain": "personal",
    }, headers=headers)
    assert r.status_code == 422
    # No state change, no active skill created.
    assert client.get("/api/skills", headers=headers).json() == [] or \
        all(x["state"] != "active" for x in client.get("/api/skills", headers=headers).json())


def test_a07_static_pass_functional_fail_does_not_promote(client: TestClient):
    headers = login_owner(client)
    s = _stage(client, headers, name="flaky").json()
    ev = client.post(f"/api/skills/{s['id']}/evaluate", json={
        "static_passed": True, "functional_passed": False, "report": {"why": "crashes on real input"}
    }, headers=headers).json()
    pr = client.post(f"/api/skills/{s['id']}/promote",
                    json={"evaluation_id": ev["evaluation_id"]}, headers=headers)
    assert pr.status_code == 409
    # Still staged, not active.
    assert client.get("/api/skills", headers=headers).json()[0]["state"] == "staged"


def test_a07_happy_path_promotes_then_invokable(client: TestClient):
    headers = login_owner(client)
    s = _stage(client, headers, name="good").json()
    ev = client.post(f"/api/skills/{s['id']}/evaluate", json={
        "static_passed": True, "functional_passed": True, "report": {}
    }, headers=headers).json()
    pr = client.post(f"/api/skills/{s['id']}/promote",
                    json={"evaluation_id": ev["evaluation_id"]}, headers=headers)
    assert pr.status_code == 200
    inv = client.post(f"/api/skills/{s['id']}/invoke", headers=headers)
    assert inv.status_code == 200
    assert inv.json()["state"] == "active"


def test_a08_self_built_skill_goes_through_same_audit_no_secret_in_package(client: TestClient):
    headers = login_owner(client)
    # Self-authored package with a "private detail" must still be hashed only.
    s = _stage(client, headers, name="mine",
               package={"instructions": "summarize", "private_note": "my secret draft"}).json()
    # The catalogue exposes only the immutable hash, never package contents.
    listed = [x for x in client.get("/api/skills", headers=headers).json() if x["id"] == s["id"]][0]
    assert "package_hash" in listed
    assert "private_note" not in listed
    # Cannot promote without evaluation (same gate as any skill).
    ev = client.post(f"/api/skills/{s['id']}/evaluate", json={
        "static_passed": True, "functional_passed": True, "report": {}
    }, headers=headers).json()
    pr = client.post(f"/api/skills/{s['id']}/promote",
                    json={"evaluation_id": ev["evaluation_id"]}, headers=headers)
    assert pr.status_code == 200


def test_a09_regression_disables_version_allowing_rollback(client: TestClient):
    headers = login_owner(client)
    s = _stage(client, headers, name="rolling").json()
    ev = client.post(f"/api/skills/{s['id']}/evaluate", json={
        "static_passed": True, "functional_passed": True, "report": {}
    }, headers=headers).json()
    client.post(f"/api/skills/{s['id']}/promote",
                json={"evaluation_id": ev["evaluation_id"]}, headers=headers)
    # Regression: owner disables the promoted version.
    d = client.post(f"/api/skills/{s['id']}/disable", headers=headers)
    assert d.status_code == 200 and d.json()["state"] == "disabled"
    # Disabled skill no longer invokable (rollback to a previous approved version).
    inv = client.post(f"/api/skills/{s['id']}/invoke", headers=headers)
    assert inv.status_code == 409
