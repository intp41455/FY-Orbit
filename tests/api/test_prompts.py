"""P1-06 prompt template library: CRUD four-interface + governance + determinism.

Real-request tests against the FastAPI app (TestClient on isolated SQLite).
Importing ``prompt_models`` registers its tables on the shared ``Base``
metadata used by the ``engine`` fixture's ``create_all``.
"""

from __future__ import annotations

import find_yourself.db.prompt_models  # noqa: F401  (register tables on Base.metadata)
from fastapi.testclient import TestClient

from helpers import login_owner

TEMPLATE = {
    "name": "graph.single_agent.reflection",
    "content": "You are reflecting on goal: {{goal}}.\nDepth: {{depth|1}}.",
    "variables_schema": {
        "goal": {"type": "str", "required": True},
        "depth": {"type": "int", "required": False, "default": 1},
    },
    "scope": "platform",
    "description": "单Agent共情回应模板",
}


def _create(client: TestClient, headers: dict, **overrides) -> dict:
    body = {**TEMPLATE, **overrides}
    r = client.post("/api/prompts", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _decide(client: TestClient, headers: dict, proposal: dict, decision="approve") -> dict:
    r = client.post(f"/api/proposals/{proposal['proposal_id']}/decision",
                    json={"digest": proposal["digest"], "decision": decision},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _apply(client: TestClient, headers: dict, name: str, proposal: dict) -> dict:
    r = client.post(f"/api/prompts/{name}/apply",
                    json={"proposal_id": proposal["proposal_id"], "digest": proposal["digest"]},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- CRUD four interfaces ------------------------------------------------------

def test_crud_create_list_detail_update(client: TestClient):
    headers = login_owner(client)

    # 1) POST -> draft (is_active false; activation requires a proposal)
    created = _create(client, headers)
    assert created["name"] == TEMPLATE["name"]
    assert created["is_active"] is False
    assert created["latest_version"] == 1

    # duplicate name -> 409
    r = client.post("/api/prompts", json=TEMPLATE, headers=headers)
    assert r.status_code == 409

    # 2) GET list (with filters)
    r = client.get("/api/prompts", headers=headers)
    assert r.status_code == 200
    names = [row["name"] for row in r.json()]
    assert TEMPLATE["name"] in names
    r = client.get("/api/prompts", params={"q": "reflection"}, headers=headers)
    assert any(row["name"] == TEMPLATE["name"] for row in r.json())
    r = client.get("/api/prompts", params={"scope": "game_tree"}, headers=headers)
    assert all(row["scope"] == "game_tree" for row in r.json())
    r = client.get("/api/prompts", params={"status": "draft"}, headers=headers)
    assert all(row["is_active"] is False for row in r.json())

    # 3) GET detail incl. version history
    r = client.get(f"/api/prompts/{TEMPLATE['name']}", headers=headers)
    assert r.status_code == 200
    detail = r.json()
    assert [v["version"] for v in detail["versions"]] == [1]
    assert detail["versions"][0]["created_by"] == "owner"

    # 4) PUT metadata-only update succeeds (optimistic lock respected)
    r = client.put(f"/api/prompts/{TEMPLATE['name']}",
                   json={"description": "更新后的描述", "version": detail["version"]},
                   headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "更新后的描述"

    # stale optimistic lock -> 409
    r = client.put(f"/api/prompts/{TEMPLATE['name']}",
                   json={"description": "x", "version": 1}, headers=headers)
    assert r.status_code == 409


def test_put_with_content_is_rejected_requires_proposal(client: TestClient):
    headers = login_owner(client)
    _create(client, headers)
    # Content/schema changes via PUT must be REJECTED (acceptance criterion 4).
    r = client.put(f"/api/prompts/{TEMPLATE['name']}",
                   json={"description": "x", "content": "new {{content}}"},
                   headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"


def test_create_rejects_unknown_variables_and_bad_scope(client: TestClient):
    headers = login_owner(client)
    r = client.post("/api/prompts", json={
        "name": "bad.undeclared", "content": "Hi {{who}}", "variables_schema": {},
    }, headers=headers)
    assert r.status_code == 422
    assert "unknown_variables" in r.json()["error"]["code"]
    r = client.post("/api/prompts", json={
        "name": "bad.scope", "content": "x", "variables_schema": {}, "scope": "nope",
    }, headers=headers)
    assert r.status_code == 422


# --- governance: stage / activate / disable ------------------------------------

def _make_active(client: TestClient, headers: dict) -> dict:
    _create(client, headers)
    proposal = _proposal_activate_v1(client, headers)
    _decide(client, headers, proposal)
    return _apply(client, headers, TEMPLATE["name"], proposal)


def _proposal_activate_v1(client: TestClient, headers: dict) -> dict:
    r = client.put(f"/api/prompts/{TEMPLATE['name']}/activate",
                   json={"version": 1, "reason": "上线 v1"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_activate_requires_proposal_then_renders(client: TestClient):
    headers = login_owner(client)
    _create(client, headers)

    # Draft cannot render before activation.
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                   params={"variables": '{"goal": "g"}'}, headers=headers)
    assert r.status_code == 409

    proposal = _proposal_activate_v1(client, headers)
    assert proposal["operation"] == "prompt.activate"

    # Applying BEFORE approval must fail.
    r = client.post(f"/api/prompts/{TEMPLATE['name']}/apply",
                    json={"proposal_id": proposal["proposal_id"], "digest": proposal["digest"]},
                    headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "proposal_not_executed"

    _decide(client, headers, proposal)
    applied = _apply(client, headers, TEMPLATE["name"], proposal)
    assert applied["is_active"] is True

    # Digest tampering on apply -> 409
    r2 = client.put(f"/api/prompts/{TEMPLATE['name']}/activate",
                    json={"version": 1, "reason": "again"}, headers=headers)
    p2 = r2.json()
    _decide(client, headers, p2)
    r = client.post(f"/api/prompts/{TEMPLATE['name']}/apply",
                    json={"proposal_id": p2["proposal_id"], "digest": "0" * 64},
                    headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "digest_mismatch"

    # Now render works (default -> latest_version, default applied).
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                   params={"variables": '{"goal": "写诗"}'}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "You are reflecting on goal: 写诗.\nDepth: 1."
    assert body["version"] == 1
    assert len(body["content_hash"]) == 64 and len(body["variables_hash"]) == 64


def test_stage_new_version_flow_and_pointer_switch(client: TestClient):
    headers = login_owner(client)
    _make_active(client, headers)

    # stage v2 (content + schema change) -> proposal -> decision -> apply
    r = client.post(f"/api/prompts/{TEMPLATE['name']}/stage", json={
        "content": "Goal {{goal}} at depth {{depth|2}} (v2).",
        "variables_schema": {
            "goal": {"type": "str", "required": True},
            "depth": {"type": "int", "required": False, "default": 2},
        },
        "reason": "改写反思措辞",
    }, headers=headers)
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert proposal["operation"] == "prompt.stage"
    _decide(client, headers, proposal)
    applied = _apply(client, headers, TEMPLATE["name"], proposal)
    assert applied["version"] == 2

    # Staging did NOT move the effective pointer: render still v1.
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                   params={"variables": '{"goal": "g"}'}, headers=headers)
    assert r.json()["version"] == 1

    # activate v2 -> pointer switch (rollback = pointer switch, never rewrite)
    r = client.put(f"/api/prompts/{TEMPLATE['name']}/activate",
                   json={"version": 2, "reason": "切 v2"}, headers=headers)
    p2 = r.json()
    _decide(client, headers, p2)
    _apply(client, headers, TEMPLATE["name"], p2)

    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                   params={"variables": '{"goal": "g"}'}, headers=headers)
    body = r.json()
    assert body["version"] == 2
    assert body["text"] == "Goal g at depth 2 (v2)."

    # Roll back the pointer to v1: v1 content unchanged.
    r = client.put(f"/api/prompts/{TEMPLATE['name']}/activate",
                   json={"version": 1, "reason": "回滚 v1"}, headers=headers)
    p3 = r.json()
    _decide(client, headers, p3)
    _apply(client, headers, TEMPLATE["name"], p3)
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                   params={"variables": '{"goal": "g"}'}, headers=headers)
    assert r.json()["version"] == 1
    assert r.json()["text"] == "You are reflecting on goal: g.\nDepth: 1."


def test_disable_flow_blocks_render_then_reactivate(client: TestClient):
    headers = login_owner(client)
    _make_active(client, headers)

    r = client.post(f"/api/prompts/{TEMPLATE['name']}/disable",
                    json={"reason": "下线维护"}, headers=headers)
    assert r.status_code == 200
    proposal = r.json()
    assert proposal["operation"] == "prompt.disable"
    _decide(client, headers, proposal)
    applied = _apply(client, headers, TEMPLATE["name"], proposal)
    assert applied["is_active"] is False

    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                   params={"variables": '{"goal": "g"}'}, headers=headers)
    assert r.status_code == 409

    # Re-activate restores rendering.
    p = _proposal_activate_v1(client, headers)
    _decide(client, headers, p)
    _apply(client, headers, TEMPLATE["name"], p)
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                   params={"variables": '{"goal": "g"}'}, headers=headers)
    assert r.status_code == 200


# --- validation & determinism ---------------------------------------------------

def test_render_validation_failures_are_4xx_and_free(client: TestClient, session_maker):
    from find_yourself.db.prompt_models import PromptRenderLog
    headers = login_owner(client)
    _make_active(client, headers)

    # required missing -> 422
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render", headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_variable"
    # type mismatch -> 422
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                   params={"variables": '{"goal": "g", "depth": "many"}'}, headers=headers)
    assert r.status_code == 422
    # unknown template -> 404
    r = client.get("/api/prompts/nope.missing/render",
                   params={"variables": "{}"}, headers=headers)
    assert r.status_code == 404

    # Zero-cost guarantee: no render log rows were written by failures.
    s = session_maker()
    assert s.query(PromptRenderLog).count() == 0
    s.close()


def test_render_determinism_same_input_same_output_and_hash(client: TestClient, session_maker):
    from find_yourself.db.prompt_models import PromptRenderLog
    headers = login_owner(client)
    _make_active(client, headers)

    params = {"variables": '{"goal": "确定性", "depth": 3}', "task_id": "task-det-1"}
    r1 = client.get(f"/api/prompts/{TEMPLATE['name']}/render", params=params, headers=headers)
    r2 = client.get(f"/api/prompts/{TEMPLATE['name']}/render", params=params, headers=headers)
    b1, b2 = r1.json(), r2.json()
    assert b1["text"] == b2["text"]
    assert b1["content_hash"] == b2["content_hash"]
    assert b1["variables_hash"] == b2["variables_hash"]

    # Extra supplied variables are ignored; same normalized hash.
    r3 = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                    params={"variables": '{"goal": "确定性", "depth": 3, "extra": "x"}'},
                    headers=headers)
    assert r3.json()["variables_hash"] == b1["variables_hash"]

    # Preview produced no logs; the service path (log=True) does, with hashes only.
    s = session_maker()
    assert s.query(PromptRenderLog).count() == 0
    s.close()

    from find_yourself.services.audit import AuditService
    from find_yourself.services.prompt import PromptService
    s = session_maker()
    ps = PromptService(s, AuditService(s))
    rendered = ps.render(TEMPLATE["name"], {"goal": "确定性", "depth": 3},
                         task_id="task-det-2", scope="workbench")
    s.commit()
    assert rendered.text == b1["text"]
    assert rendered.variables_hash == b1["variables_hash"]
    s.commit()
    logs = s.query(PromptRenderLog).all()
    assert len(logs) == 1
    assert logs[0].template_name == TEMPLATE["name"]
    assert logs[0].variables_hash == b1["variables_hash"]
    assert logs[0].scope == "workbench"
    assert logs[0].task_id == "task-det-2"
    s.close()


def test_render_requires_login_and_logs_endpoint(client: TestClient, app):
    from fastapi.testclient import TestClient as _TC
    _headers = login_owner(client)
    _make_active(client, _headers)
    # A cookie-free client (the login_owner cookie jar above is per-client)
    # must be rejected: render is 免审 but NOT 免登录.
    anon = _TC(app)
    r = anon.get(f"/api/prompts/{TEMPLATE['name']}/render",
                 params={"variables": '{"goal": "g"}'})
    assert r.status_code == 401
    # logs endpoint returns hash-only rows
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/logs", headers=_headers)
    assert r.status_code == 200
    for row in r.json():
        assert "variables" not in row  # never plaintext
        assert set(row) == {"id", "template_name", "version", "variables_hash",
                            "scope", "task_id", "created_at"}


def test_escaped_braces_render_literally(client: TestClient):
    headers = login_owner(client)
    r = client.post("/api/prompts", json={
        "name": "esc.lit",
        "content": "Literal: \\{\\{not_a_var}} and {{v}}",
        "variables_schema": {"v": {"type": "str", "required": True}},
    }, headers=headers)
    assert r.status_code == 200, r.text
    from find_yourself.services.prompt import render_text
    out = render_text(
        "Literal: \\{\\{not_a_var}} and {{v}}",
        {"v": {"type": "str", "required": True}},
        {"v": "ok"},
    )
    assert out == "Literal: {{not_a_var}} and ok"
