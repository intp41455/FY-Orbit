"""API integration tests for selective synchronization and data classification governance."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner


def test_sync_status_default(client: TestClient) -> None:
    headers = login_owner(client)
    res = client.get("/api/sync/status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "local_only"
    assert data["paused"] is False
    assert "profiles_and_corrections" in data["enabled_categories"]
    assert "credentials_and_keys" in data["local_only_categories"]
    assert data["pending_conflicts_count"] == 0


def test_sync_config_update(client: TestClient) -> None:
    headers = login_owner(client)

    # 1. Reject enabling local_only category
    res_bad = client.post(
        "/api/sync/config",
        json={"enabled_categories": ["credentials_and_keys"]},
        headers=headers,
    )
    assert res_bad.status_code == 422

    # 2. Opt in to sync with valid categories
    res_ok = client.post(
        "/api/sync/config",
        json={
            "mode": "sync_opt_in",
            "enabled_categories": ["profiles_and_corrections", "canvas_topology_tasks"],
            "device_id": "test-device-win",
        },
        headers=headers,
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["mode"] == "sync_opt_in"
    assert res_ok.json()["device_id"] == "test-device-win"


def test_sync_push_boundary_and_conflicts(client: TestClient) -> None:
    headers = login_owner(client)

    # 1. Pushing while local_only is rejected
    res_rejected = client.post(
        "/api/sync/push",
        json={
            "items": [
                {
                    "entity_type": "profiles_and_corrections",
                    "entity_id": "p-1",
                    "version": 1,
                    "payload": {"name": "Test"},
                }
            ]
        },
        headers=headers,
    )
    assert res_rejected.status_code == 422

    # 2. Switch to sync_opt_in
    client.post("/api/sync/config", json={"mode": "sync_opt_in"}, headers=headers)

    # 3. Pushing credentials_and_keys is blocked
    res_creds = client.post(
        "/api/sync/push",
        json={
            "items": [
                {
                    "entity_type": "credentials_and_keys",
                    "entity_id": "k-1",
                    "version": 1,
                    "payload": {"token": "secret"},
                }
            ]
        },
        headers=headers,
    )
    assert res_creds.status_code == 422

    # 4. Valid push of v1
    res_v1 = client.post(
        "/api/sync/push",
        json={
            "items": [
                {
                    "entity_type": "profiles_and_corrections",
                    "entity_id": "p-1",
                    "version": 1,
                    "payload": {"name": "Alice Initial"},
                }
            ]
        },
        headers=headers,
    )
    assert res_v1.status_code == 200
    assert res_v1.json()["accepted_count"] == 1

    # 5. Advance server to v2
    client.post(
        "/api/sync/push",
        json={
            "items": [
                {
                    "entity_type": "profiles_and_corrections",
                    "entity_id": "p-1",
                    "version": 2,
                    "payload": {"name": "Alice Server v2"},
                }
            ]
        },
        headers=headers,
    )

    # 6. Push conflicting v1 with different payload
    res_conflict = client.post(
        "/api/sync/push",
        json={
            "items": [
                {
                    "entity_type": "profiles_and_corrections",
                    "entity_id": "p-1",
                    "version": 1,
                    "payload": {"name": "Alice Local Divergent"},
                }
            ]
        },
        headers=headers,
    )
    assert res_conflict.status_code == 200
    assert res_conflict.json()["conflicts_count"] == 1

    # 7. Check conflicts list
    res_conf_list = client.get("/api/sync/conflicts", headers=headers)
    assert res_conf_list.status_code == 200
    confs = res_conf_list.json()
    assert len(confs) == 1
    conf_id = confs[0]["conflict_id"]

    # 8. Resolve conflict via accept_remote
    res_resolve = client.post(
        f"/api/sync/conflicts/{conf_id}/resolve",
        json={"resolution": "accept_remote"},
        headers=headers,
    )
    assert res_resolve.status_code == 200
    assert res_resolve.json()["status"] == "resolved_remote"

    # 9. Verify conflict is no longer pending
    res_conf_pending = client.get("/api/sync/conflicts", headers=headers)
    assert len(res_conf_pending.json()) == 0

    # 10. Pull changes
    res_pull = client.get("/api/sync/pull", headers=headers)
    assert res_pull.status_code == 200
    assert len(res_pull.json()["changes"]) >= 2
