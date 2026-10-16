"""W6 超级中台 API 层测试：端点契约 / 越权 403 / 凭证掩码 / 探活与调用如实返回。

只跑本文件（交接总纲：禁止跑全量）。
"""

from __future__ import annotations

import json

import pytest
from helpers import login_owner

from find_yourself.db.workbench_models import HubConnection

# 本项目错误约定（services/errors.py）：ValidationFailed → 422，NotFound → 404，
# PermissionDenied → 403。断言按真实约定写，不臆造 400。
VALIDATION_STATUS = 422


def _manifest(name: str = "manifest-hook", *, required_token: bool = True) -> str:
    """按 manifest.py 的真实 schema 构造（schema/endpoint/auth.fields）。"""
    import json as _json

    return _json.dumps(
        {
            "schema": "fy-hub-adapter/v1",
            "id": name,
            "name": name,
            "kind": "http_webhook",
            "icon": "🔍",
            "description": "manifest 导入测试",
            "endpoint": {"url": "https://api.example.com/m", "method": "POST"},
            "auth": {
                "fields": [
                    {"key": "api_token", "label": "API Token", "secret": True, "required": required_token}
                ]
            },
            "capabilities": [{"name": "hook", "tags": ["http"]}],
        },
        ensure_ascii=False,
    )


def _conn_payload(**over):
    payload = {
        "name": "测试 Webhook",
        "kind": "http_webhook",
        "icon": "🪝",
        "description": "单元测试用",
        "config": {"url": "https://api.example.com/hook"},
        "credentials": {"token": "plain-token-value-123"},
        "secret_fields": ["token"],
        "credential_fields": [
            {"key": "token", "label": "Token", "secret": True, "required": True}
        ],
        "capabilities": [{"name": "webhook", "tags": ["http"], "description": "回调"}],
    }
    payload.update(over)
    return payload


@pytest.fixture()
def owner(client):
    csrf = login_owner(client)
    return client, csrf


# --------------------------------------------------------------------------- #
# 预置 / manifest
# --------------------------------------------------------------------------- #

def test_presets_are_public_catalog_without_secrets(owner):
    c, _ = owner
    r = c.get("/api/hub/presets")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 7, "任务书要求内置预置 ≥7 条"
    for p in body["presets"]:
        # 预置只允许默认值 + 凭证字段声明，不得含任何真实凭证值
        assert "credentials" not in p
        assert isinstance(p["credential_fields"], list)


def test_manifest_example_is_served(owner):
    c, _ = owner
    r = c.get("/api/hub/manifest/example")
    assert r.status_code == 200
    assert "example" in r.json() and "schema" in r.json()


def test_manifest_import_creates_needs_credentials_when_secret_missing(owner):
    c, csrf = owner
    r = c.post(
        "/api/hub/manifest/import",
        json={"text": _manifest(), "filename": "m.json"},
        headers=csrf,
    )
    assert r.status_code == 200, r.text
    conn = r.json()["connection"]
    # 诚实：缺必填凭证就必须停在 needs_credentials，不能假装可用
    assert conn["state"] == "needs_credentials"
    assert "plain" not in r.text


def test_manifest_import_with_credential_becomes_active(owner):
    c, csrf = owner
    r = c.post(
        "/api/hub/manifest/import",
        json={"text": _manifest("with-secret"), "credentials": {"api_token": "tok-abcdef123456"}},
        headers=csrf,
    )
    assert r.status_code == 200, r.text
    conn = r.json()["connection"]
    assert conn["state"] != "needs_credentials"
    assert "tok-abcdef123456" not in r.text


def test_manifest_missing_required_field_is_reported(owner):
    c, csrf = owner
    r = c.post(
        "/api/hub/manifest/import",
        json={"text": json.dumps({"name": "no-schema", "kind": "http_webhook"})},
        headers=csrf,
    )
    assert r.status_code == VALIDATION_STATUS, r.text
    assert r.json()["error"]["code"] == "hub_manifest_missing_field"


# --------------------------------------------------------------------------- #
# CRUD + 凭证
# --------------------------------------------------------------------------- #

def test_create_connection_never_returns_plaintext_secret(owner):
    c, csrf = owner
    r = c.post("/api/hub/connections", json=_conn_payload(), headers=csrf)
    assert r.status_code == 200, r.text
    conn = r.json()["connection"]
    assert "plain-token-value-123" not in r.text
    masked = conn["config"]["token"]
    assert "****" in masked or set(masked) == {"*"}


def test_get_connection_returns_mask_not_plaintext(owner):
    c, csrf = owner
    created = c.post("/api/hub/connections", json=_conn_payload(), headers=csrf).json()["connection"]
    r = c.get(f"/api/hub/connections/{created['id']}")
    assert r.status_code == 200
    assert "plain-token-value-123" not in r.text


def test_patch_connection_updates_name(owner):
    c, csrf = owner
    created = c.post("/api/hub/connections", json=_conn_payload(), headers=csrf).json()["connection"]
    r = c.patch(f"/api/hub/connections/{created['id']}", json={"name": "改名后"}, headers=csrf)
    assert r.status_code == 200
    assert r.json()["connection"]["name"] == "改名后"


def test_delete_connection_removes_it(owner):
    c, csrf = owner
    created = c.post("/api/hub/connections", json=_conn_payload(), headers=csrf).json()["connection"]
    r = c.delete(f"/api/hub/connections/{created['id']}", headers=csrf)
    assert r.status_code == 200
    assert c.get(f"/api/hub/connections/{created['id']}").status_code == 404


def test_unknown_connection_is_404(owner):
    c, _ = owner
    r = c.get("/api/hub/connections/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "hub_connection_not_found"


def test_write_without_csrf_is_rejected(owner):
    c, _ = owner
    r = c.post("/api/hub/connections", json=_conn_payload())
    assert r.status_code in (401, 403), r.text


def test_unknown_secret_field_is_rejected(owner):
    c, csrf = owner
    r = c.post(
        "/api/hub/connections",
        json=_conn_payload(credentials={"not_declared": "x"}),
        headers=csrf,
    )
    assert r.status_code == VALIDATION_STATUS, r.text
    assert r.json()["error"]["code"] == "hub_unknown_secret_field"


# --------------------------------------------------------------------------- #
# 越权
# --------------------------------------------------------------------------- #

def test_other_owner_cannot_read_connection(client, app):
    """把连接的 owner 换成别人后，原 owner 再按 id 访问 → 403，且不泄露任何字段。"""
    csrf_a = login_owner(client)
    created = client.post(
        "/api/hub/connections", json=_conn_payload(), headers=csrf_a
    ).json()["connection"]
    conn_id = created["id"]

    # app.state 只有 session_maker（api/app.py:114），没有 session_scope
    maker = app.state.session_maker
    with maker() as s:
        row = s.get(HubConnection, conn_id)
        original = row.owner_id
        row.owner_id = "someone-else"
        s.commit()

    try:
        r = client.get(f"/api/hub/connections/{conn_id}")
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "hub_connection_forbidden"
        assert original not in r.text
        assert "测试 Webhook" not in r.text
    finally:
        with maker() as s:
            s.get(HubConnection, conn_id).owner_id = original
            s.commit()


# --------------------------------------------------------------------------- #
# 探活 / 调用：失败必须如实返回
# --------------------------------------------------------------------------- #

def test_health_check_failure_reports_real_reason(owner):
    c, csrf = owner
    # example.com 的 443 端口在沙箱内不可达 → 探活必然失败；关键是失败原因真实
    created = c.post(
        "/api/hub/connections",
        json=_conn_payload(config={"url": "https://unreachable.invalid/hook"}),
        headers=csrf,
    ).json()["connection"]
    r = c.post(f"/api/hub/connections/{created['id']}/health-check", headers=csrf)
    assert r.status_code == 200, r.text
    report = r.json()["report"]
    assert report["ok"] is False
    assert report["detail"], "失败必须给出原因，不能空着"


def test_health_check_all_returns_per_item_reports(owner):
    c, csrf = owner
    c.post("/api/hub/connections", json=_conn_payload(), headers=csrf)
    r = c.post("/api/hub/connections/health-check-all", headers=csrf)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert "results" in body and "healthy" in body


def test_invoke_on_disabled_connection_is_refused(owner):
    c, csrf = owner
    created = c.post("/api/hub/connections", json=_conn_payload(), headers=csrf).json()["connection"]
    c.patch(f"/api/hub/connections/{created['id']}", json={"state": "disabled"}, headers=csrf)
    r = c.post(f"/api/hub/connections/{created['id']}/invoke", json={}, headers=csrf)
    assert r.status_code == VALIDATION_STATUS, r.text
    assert r.json()["error"]["code"] == "hub_connection_disabled"


def test_invoke_needs_credentials_is_refused(owner):
    c, csrf = owner
    created = c.post(
        "/api/hub/manifest/import", json={"text": _manifest("no-secret-hook")}, headers=csrf
    ).json()["connection"]
    assert created["state"] == "needs_credentials"
    r = c.post(f"/api/hub/connections/{created['id']}/invoke", json={}, headers=csrf)
    assert r.status_code == VALIDATION_STATUS, r.text
    assert r.json()["error"]["code"] == "hub_connection_needs_credentials"


# --------------------------------------------------------------------------- #
# 能力 / 路由
# --------------------------------------------------------------------------- #

def test_capability_register_and_unregister(owner):
    c, csrf = owner
    created = c.post("/api/hub/connections", json=_conn_payload(), headers=csrf).json()["connection"]
    r = c.post(
        f"/api/hub/connections/{created['id']}/capabilities",
        json={"name": "search", "tags": ["web"], "description": "检索"},
        headers=csrf,
    )
    assert r.status_code == 200
    names = [x["name"] for x in r.json()["capabilities"]]
    assert "search" in names and "webhook" in names

    r2 = c.delete(f"/api/hub/connections/{created['id']}/capabilities/search", headers=csrf)
    assert r2.status_code == 200
    assert "search" not in [x["name"] for x in r2.json()["capabilities"]]


def test_capability_inventory_is_flat(owner):
    c, csrf = owner
    c.post("/api/hub/connections", json=_conn_payload(), headers=csrf)
    r = c.get("/api/hub/capabilities")
    assert r.status_code == 200
    rows = r.json()["capabilities"]
    assert rows and "connection_id" in rows[0] and "capability" in rows[0]


def test_route_ranks_matching_capability_with_reasons(owner):
    c, csrf = owner
    c.post("/api/hub/connections", json=_conn_payload(), headers=csrf)
    r = c.post("/api/hub/route", json={"hint": "调用 webhook 转发请求"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    top = body["candidates"][0]
    assert top["reasons"], "路由必须给出可解释理由"
    assert isinstance(top["score"], (int, float))


def test_route_with_no_match_returns_empty_not_error(owner):
    c, _ = owner
    r = c.post("/api/hub/route", json={"hint": "zzzz-qqqq-xxxx"})
    assert r.status_code == 200
    assert r.json()["candidates"] == []


def test_route_requires_hint(owner):
    c, _ = owner
    r = c.post("/api/hub/route", json={"hint": ""})
    # pydantic min_length=1 → 422
    assert r.status_code == 422


def test_filter_by_kind(owner):
    c, csrf = owner
    c.post("/api/hub/connections", json=_conn_payload(), headers=csrf)
    c.post(
        "/api/hub/connections",
        json=_conn_payload(name="本地模型", kind="openai_chat", config={"base_url": "http://127.0.0.1:1/v1"}),
        headers=csrf,
    )
    only = c.get("/api/hub/connections", params={"kind": "openai_chat"}).json()
    assert only["count"] == 1
    assert only["connections"][0]["kind"] == "openai_chat"


# --------------------------------------------------------------------------- #
# G7 出站配置面：a2a_agent 走真实 HTTP 面（登记 → 探活 → 派发）
# --------------------------------------------------------------------------- #

def _a2a_payload(**over):
    payload = {
        "name": "远程规划 Agent",
        "kind": "a2a_agent",
        "icon": "\U0001f91d",
        "description": "外部 A2A Agent",
        "config": {"base_url": "https://agent.example.com"},
        "credentials": {},
        "secret_fields": [],
        "capabilities": [],
    }
    payload.update(over)
    return payload


def test_a2a_agent_registers_through_api(owner, session_maker):
    c, csrf = owner
    r = c.post("/api/hub/connections", json=_a2a_payload(), headers=csrf)
    assert r.status_code == 200, r.text
    conn = r.json()["connection"]
    assert conn["kind"] == "a2a_agent"
    assert conn["group"] == "tool"
    # 能按 kind 读回（不是只在响应里好看）
    listed = c.get("/api/hub/connections", params={"kind": "a2a_agent"}).json()
    assert listed["count"] == 1
    # 而且真的落库了：直接查 ORM 行，DB CHECK 放行 a2a_agent
    with session_maker() as s:
        row = s.get(HubConnection, conn["id"])
        assert row is not None and row.kind == "a2a_agent"


def test_a2a_agent_without_base_url_is_rejected(owner):
    c, csrf = owner
    r = c.post("/api/hub/connections",
               json=_a2a_payload(config={}), headers=csrf)
    assert r.status_code == VALIDATION_STATUS, r.text
    assert r.json()["error"]["code"] == "hub_a2a_endpoint_required"


def test_a2a_agent_private_endpoint_is_ssrf_blocked_at_registration(owner):
    c, csrf = owner
    r = c.post("/api/hub/connections",
               json=_a2a_payload(config={"base_url": "http://127.0.0.1:8080"}),
               headers=csrf)
    assert r.status_code == VALIDATION_STATUS, r.text
    assert r.json()["error"]["code"] == "hub_ssrf_blocked"


def test_a2a_agent_probe_failure_is_reported_not_faked_green(owner):
    """探活真打网络：``.invalid`` 是 RFC 2606 保留 TLD，永不解析，因此这是
    「真的发了一次请求并真的失败」——既不碰外网服务，也不靠 mock 冒充通过。"""
    c, csrf = owner
    created = c.post("/api/hub/connections",
                     json=_a2a_payload(config={"base_url": "https://a2a-probe.invalid"}),
                     headers=csrf).json()["connection"]
    r = c.post(f"/api/hub/connections/{created['id']}/health-check",
               headers=csrf, params={"timeout_seconds": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report"]["ok"] is False
    assert body["report"]["detail"], "失败必须给出真实原因，不能是空串"
    assert body["connection"]["health"]["ok"] is False
    assert body["connection"]["state"] == "error"


def test_a2a_agent_credentials_never_leak(owner):
    c, csrf = owner
    created = c.post("/api/hub/connections", json=_a2a_payload(
        credentials={"api_token": "a2a-super-secret-value"},
        secret_fields=["api_token"],
    ), headers=csrf).json()["connection"]
    assert "a2a-super-secret-value" not in json.dumps(created, ensure_ascii=False)
    again = c.get(f"/api/hub/connections/{created['id']}").json()["connection"]
    assert "a2a-super-secret-value" not in json.dumps(again, ensure_ascii=False)
    # 掩码形态（public_connection 的既有约定）
    assert "****" in again["config"].get("api_token", "")


def test_a2a_agent_dispatch_without_text_is_refused_honestly(owner):
    """空派发必须被明确拒绝（200 + ok:false），不能悄悄"成功"。"""
    c, csrf = owner
    created = c.post("/api/hub/connections", json=_a2a_payload(), headers=csrf).json()["connection"]
    r = c.post(f"/api/hub/connections/{created['id']}/invoke",
               headers=csrf, json={"action": "send", "params": {}})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["ok"] is False
    assert "hub_a2a_missing_prompt" in result["error"]


def test_a2a_agent_cross_owner_is_forbidden(client, app):
    """把连接 owner 换成别人后，原 owner 探活/派发都 403 —— 与其它 kind 同一条边界。"""
    csrf = login_owner(client)
    created = client.post("/api/hub/connections", json=_a2a_payload(),
                          headers=csrf).json()["connection"]
    conn_id = created["id"]
    maker = app.state.session_maker
    with maker() as s:
        row = s.get(HubConnection, conn_id)
        original = row.owner_id
        row.owner_id = "someone-else"
        s.commit()
    try:
        r = client.post(f"/api/hub/connections/{conn_id}/health-check",
                        headers=csrf, params={"timeout_seconds": 2})
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "hub_connection_forbidden"
        assert "远程规划 Agent" not in r.text  # 不泄露对方任何字段
    finally:
        with maker() as s:
            s.get(HubConnection, conn_id).owner_id = original
