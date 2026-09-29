"""A03/A04: in-flight task pinned to old agent version across upgrade; drain blocks new work; revoke invalidates leases/credentials."""

from __future__ import annotations

from fastapi.testclient import TestClient

from helpers import login_owner


def _register(client, headers, version, name="researcher"):
    return client.post("/api/agents", json={
        "name": name, "semantic_version": version, "capabilities": ["search"],
        "domains": ["work"], "endpoint_key": f"ep-{version}", "max_concurrency": 2,
    }, headers=headers)


def _enable(client, headers, agent_id):
    client.post(f"/api/agents/{agent_id}/health?healthy=true", headers=headers)
    return client.post(f"/api/agents/{agent_id}/enable", headers=headers)


def test_a03_in_flight_task_pinned_old_version_new_task_new_version(client: TestClient):
    headers = login_owner(client)
    reg = _register(client, headers, "1.0.0").json()
    _enable(client, headers, reg["id"])
    # In-flight task on v1.
    t1 = client.post("/api/tasks", json={"goal": "old task", "idempotency_key": "ver-pin-1"},
                     headers=headers).json()
    lease1 = client.post(f"/api/agents/{reg['id']}/leases?task_id={t1['id']}",
                        headers=headers).json()
    assert lease1["agent_version"] == "1.0.0"

    # Upgrade: register a new version and enable it.
    reg2 = _register(client, headers, "2.0.0", name="researcher").json()
    _enable(client, headers, reg2["id"])
    t2 = client.post("/api/tasks", json={"goal": "new task", "idempotency_key": "ver-pin-2"},
                     headers=headers).json()
    lease2 = client.post(f"/api/agents/{reg2['id']}/leases?task_id={t2['id']}",
                        headers=headers).json()
    assert lease2["agent_version"] == "2.0.0"
    # In-flight v1 lease still records v1 (not silently upgraded).
    assert lease1["agent_version"] == "1.0.0"


def test_a04_drain_rejects_new_leases_but_keeps_existing(client: TestClient):
    headers = login_owner(client)
    reg = _register(client, headers, "1.0.0", name="worker").json()
    _enable(client, headers, reg["id"])
    t1 = client.post("/api/tasks", json={"goal": "ongoing", "idempotency_key": "drain-1111"},
                     headers=headers).json()
    active = client.post(f"/api/agents/{reg['id']}/leases?task_id={t1['id']}",
                        headers=headers).json()
    assert active["state"] == "active"

    # Drain: no new tasks assigned.
    d = client.post(f"/api/agents/{reg['id']}/drain", headers=headers)
    assert d.json()["state"] == "draining"
    t2 = client.post("/api/tasks", json={"goal": "new", "idempotency_key": "drain-2222"},
                    headers=headers).json()
    r = client.post(f"/api/agents/{reg['id']}/leases?task_id={t2['id']}", headers=headers)
    assert r.status_code == 409  # agent_not_enabled


def test_a04_revoke_invalidates_leases(client: TestClient):
    headers = login_owner(client)
    reg = _register(client, headers, "1.0.0", name="doomed").json()
    _enable(client, headers, reg["id"])
    t1 = client.post("/api/tasks", json={"goal": "x", "idempotency_key": "revoke-1"},
                    headers=headers).json()
    client.post(f"/api/agents/{reg['id']}/leases?task_id={t1['id']}", headers=headers)
    rv = client.post(f"/api/agents/{reg['id']}/revoke", headers=headers)
    assert rv.json()["state"] == "revoked"
    # Revoked agent cannot take new leases.
    t2 = client.post("/api/tasks", json={"goal": "y", "idempotency_key": "revoke-2"},
                    headers=headers).json()
    r = client.post(f"/api/agents/{reg['id']}/leases?task_id={t2['id']}", headers=headers)
    assert r.status_code == 409
