"""API-layer synthetic verification for 19 团队能力.

Independent of the 354-test baseline: these exercise the new
``/api/teams`` surface end to end through the real FastAPI app — auth, CSRF,
identity, owner isolation, the draft → validate → start → control → events
flow, and the credential redaction guarantees.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner


@pytest.fixture()
def auth(client: TestClient) -> dict[str, str]:
    return login_owner(client)


def create_team(client, auth, **overrides):
    body = {
        "name": "工程团队",
        "template_id": "engineering",
        "budget_ref": {"root_budget_usd": 0.50, "member_reserve_cap_usd": 0.05},
        "reason": "acceptance",
    }
    body.update(overrides)
    return client.post("/api/teams", json=body, headers=auth)


def test_catalog_lists_models_and_hosts_without_secrets(client, auth):
    resp = client.get("/api/teams/catalog", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["models"], "the catalog must not be empty"
    assert any(h["agent_host"] == "find_yourself" for h in body["hosts"])
    # Every provider states credential state as a boolean plus a reference.
    for p in body["providers"]:
        assert isinstance(p["credential_configured"], bool)
        assert p["credential_ref"].startswith(("env:", "inprocess:"))
    blob = resp.text
    assert "sk-" not in blob
    assert "model_api_key" not in blob


def test_team_lifecycle_over_http(client, auth):
    created = create_team(client, auth)
    assert created.status_code == 201, created.text
    snap = created.json()
    team_id = snap["team"]["id"]
    assert snap["team"]["state"] == "draft"
    assert len(snap["team"]["members"]) == 3

    # Validation is reachable before starting and reports honestly.
    val = client.get(f"/api/teams/{team_id}/validation", headers=auth).json()
    assert val["can_start"] is True
    assert val["blockers"] == []
    assert val["real_model_configured"] is False

    started = client.post(
        f"/api/teams/{team_id}/start", json={"expected_version": 1}, headers=auth
    )
    assert started.status_code == 200, started.text
    snap = started.json()
    assert snap["team"]["state"] == "running"
    sessions = [m["session_id"] for m in snap["members"]]
    assert len(set(sessions)) == 3, "each member gets its own session"
    for m in snap["members"]:
        assert m["effective_model"] == "未执行"
        assert m["effective_confidence"] == "not_executed"

    # Events replay monotonically from a cursor.
    ev = client.get(f"/api/teams/{team_id}/events", headers=auth).json()
    seqs = [e["seq"] for e in ev["items"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    tail = client.get(f"/api/teams/{team_id}/events?cursor={seqs[0]}", headers=auth).json()
    assert all(e["seq"] > seqs[0] for e in tail["items"])


def test_switch_to_an_unavailable_model_is_refused_over_http(client, auth):
    """With no provider adapter the catalog offers only the synthetic model.

    A switch to any other model must be refused with a clear reason rather than
    silently keeping the old one or pretending the switch happened.
    """
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    client.post(f"/api/teams/{team_id}/start", json={"expected_version": 1}, headers=auth)

    models = client.get("/api/teams/models", headers=auth).json()
    assert [m["model_id"] for m in models["models"]] == ["mock-deterministic"]
    assert models["real_model_configured"] is False

    resp = client.post(
        f"/api/teams/{team_id}/control",
        json={
            "operation": "switch_model", "role": "implementer",
            "scope": {"model_id": "gpt-4o-mini"},
            "reason": "try a real model",
        },
        headers=auth,
    )
    assert resp.status_code == 422
    assert "capability catalog" in resp.json()["error"]["message"]

    # The member is untouched — no half-applied switch.
    after = client.get(f"/api/teams/{team_id}", headers=auth).json()
    impl = [m for m in after["members"] if m["role"] == "implementer"][0]
    assert impl["requested_model"] == "mock-deterministic"
    assert impl["run_batch"] == 1


def test_stale_run_batch_result_is_refused_over_http(client, auth):
    """A result for a superseded batch must be rejected, not applied."""
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    started = client.post(
        f"/api/teams/{team_id}/start", json={"expected_version": 1}, headers=auth
    ).json()
    impl = [m for m in started["members"] if m["role"] == "implementer"][0]

    ok = client.post(
        f"/api/teams/{team_id}/members/result",
        json={"role": "implementer", "run_batch": impl["run_batch"], "output": "done"},
        headers=auth,
    )
    assert ok.status_code == 200

    # Rework does not open a new model batch, so an old-batch result stays stale.
    client.post(
        f"/api/teams/{team_id}/control",
        json={"operation": "rework", "role": "implementer", "reason": "needs tests"},
        headers=auth,
    )
    after = client.get(f"/api/teams/{team_id}", headers=auth).json()
    impl2 = [m for m in after["members"] if m["role"] == "implementer"][0]
    assert impl2["run_batch"] == impl["run_batch"]
    assert impl2["state"] == "waiting_rework"


def test_control_is_idempotent_over_http(client, auth):
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    client.post(f"/api/teams/{team_id}/start", json={"expected_version": 1}, headers=auth)

    body = {
        "operation": "pause", "role": "reviewer",
        "idempotency_key": "http-idem-1", "reason": "hold",
    }
    first = client.post(f"/api/teams/{team_id}/control", json=body, headers=auth).json()
    second = client.post(f"/api/teams/{team_id}/control", json=body, headers=auth).json()
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert first["id"] == second["id"]


def test_unsupported_capability_is_disabled_not_faked(client, auth):
    snap = create_team(
        client, auth, name="原生团队", template_id=None,
        mode="product_native",
        members=[
            {"role": "coordinator", "agent_host": "external_a2a"},
            {"role": "worker", "agent_host": "external_a2a"},
        ],
        default_binding={"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
    ).json()
    team_id = snap["team"]["id"]

    resp = client.put(
        f"/api/teams/{team_id}/members/worker/binding",
        json={"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
        headers=auth,
    )
    assert resp.status_code == 422
    assert "per-member model override" in resp.json()["error"]["message"]


def test_credential_reference_only_never_a_secret(client, auth):
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    client.post(f"/api/teams/{team_id}/start", json={"expected_version": 1}, headers=auth)
    body = client.get(f"/api/teams/{team_id}", headers=auth).text
    assert "credential_ref" in body
    assert "sk-" not in body
    assert "model_api_key" not in body


def test_goal_change_bumps_plan_version_over_http(client, auth):
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    client.post(f"/api/teams/{team_id}/start", json={"expected_version": 1}, headers=auth)

    resp = client.post(
        f"/api/teams/{team_id}/goal",
        json={"goal": "实现脱敏并补齐单测", "reason": "scope changed"},
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["plan_version"] == 2
    after = client.get(f"/api/teams/{team_id}", headers=auth).json()
    assert all(m["plan_version"] == 2 for m in after["members"])


def test_budget_reserve_over_http(client, auth):
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    started = client.post(
        f"/api/teams/{team_id}/start", json={"expected_version": 1}, headers=auth
    ).json()
    root = started["team"]["root_task_id"]

    ok = client.post(
        f"/api/teams/{team_id}/budget/reserve",
        json={"role": "implementer", "amount_usd": 0.03}, headers=auth,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["root_task_id"] == root

    over = client.post(
        f"/api/teams/{team_id}/budget/reserve",
        json={"role": "implementer", "amount_usd": 0.40}, headers=auth,
    )
    assert over.status_code == 422
    assert "per-member cap" in over.json()["error"]["message"]


# ----------------------------------------------------------------------
# Security boundaries
# ----------------------------------------------------------------------
def test_unauthenticated_cannot_read_teams(client):
    assert client.get("/api/teams").status_code == 401
    assert client.get("/api/teams/catalog").status_code == 401


def test_mutation_requires_csrf(client, auth):
    # No CSRF header at all.
    assert create_team(client, {}).status_code == 403


def test_forged_owner_in_body_does_not_promote_identity(client, auth):
    resp = client.post(
        "/api/teams",
        json={"name": "x", "template_id": "engineering", "owner_id": "someone-else"},
        headers=auth,
    )
    assert resp.status_code == 201
    # The team is owned by the authenticated session, not by the body field.
    listing = client.get("/api/teams", headers=auth).json()
    assert any(t["id"] == resp.json()["team"]["id"] for t in listing["items"])


def test_unknown_team_is_not_found(client, auth):
    assert client.get("/api/teams/team-does-not-exist", headers=auth).status_code == 404


def test_model_not_in_catalog_is_rejected_over_http(client, auth):
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    resp = client.put(
        f"/api/teams/{team_id}/members/implementer/binding",
        json={"provider_id": "local-synthetic", "model_id": "gpt-9-imaginary"},
        headers=auth,
    )
    assert resp.status_code == 422
    assert "capability catalog" in resp.json()["error"]["message"]


def test_binding_inheritance_chain_is_reported(client, auth):
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]

    resolved = client.get(
        f"/api/teams/{team_id}/members/implementer/binding", headers=auth
    ).json()
    assert resolved["inherited_from"] == "team"

    client.put(
        f"/api/teams/{team_id}/roles/implementer/binding",
        json={"model_id": "mock-deterministic"}, headers=auth,
    )
    assert client.get(
        f"/api/teams/{team_id}/members/implementer/binding", headers=auth
    ).json()["inherited_from"] == "role"


# ---------------------------------------------------------------------------
# T2：成员重命名端点（UI 组实测缺口——PATCH 团队不支持成员 rename）
# ---------------------------------------------------------------------------

def test_member_rename_updates_title_only_and_bumps_version(client, auth):
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    members = snap["team"]["members"]
    role = members[0]["role"]
    version = snap["team"]["version"]
    before = {m["role"]: dict(m) for m in members}

    r = client.patch(
        f"/api/teams/{team_id}/members/{role}/rename",
        json={"title": "新名字·首席工程师", "expected_version": version,
              "reason": "rename-acceptance"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    after = {m["role"]: dict(m) for m in body["team"]["members"]}
    assert after[role]["title"] == "新名字·首席工程师"
    assert body["team"]["version"] == version + 1
    # 其他成员与被改名成员的其余字段（绑定/依赖/模型配置）原样保留
    for m_role, old in before.items():
        expected = dict(old)
        if m_role == role:
            expected["title"] = "新名字·首席工程师"
        assert after[m_role] == expected


def test_member_rename_unknown_role_404(client, auth):
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    r = client.patch(
        f"/api/teams/{team_id}/members/no-such-role/rename",
        json={"title": "x", "expected_version": snap["team"]["version"]},
        headers=auth,
    )
    assert r.status_code == 404, r.text


def test_member_rename_version_conflict_409(client, auth):
    snap = create_team(client, auth).json()
    team_id = snap["team"]["id"]
    r = client.patch(
        f"/api/teams/{team_id}/members/implementer/rename",
        json={"title": "x", "expected_version": 999},
        headers=auth,
    )
    assert r.status_code == 409, r.text