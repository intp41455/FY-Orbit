"""P13 开箱模板 HTTP 契约测试（A-开箱模板-02/03/05/07/08/09）。

服务层行为已在 ``tests/unit/test_scaffold_templates.py`` 覆盖；本文件只测 **HTTP
层特有的东西**：认证 / CSRF / 错误信封 / body 不能提权 / openapi 真的挂了路径 /
路由被自动发现（``routes/__init__.py`` 的约定式挂载对本包生效）。

用真实 FastAPI app + 真实 SQLite（``tests/api/conftest.py`` 的 fixture），不 mock
被测路由本身。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner

TEMPLATE_ID = "writing-pipeline"


# --------------------------------------------------------------------------- #
# 认证与 CSRF
# --------------------------------------------------------------------------- #
def test_listing_requires_authentication(client: TestClient):
    assert client.get("/api/templates").status_code == 401


def test_schema_requires_authentication(client: TestClient):
    assert client.get("/api/templates/schema").status_code == 401


def test_detail_requires_authentication(client: TestClient):
    assert client.get(f"/api/templates/{TEMPLATE_ID}").status_code == 401


def test_mutations_require_a_csrf_token(client: TestClient):
    auth = login_owner(client)
    no_csrf: dict[str, str] = {}
    assert client.post(
        f"/api/templates/{TEMPLATE_ID}/instantiate", json={}, headers=no_csrf,
    ).status_code == 403
    assert client.post(
        f"/api/templates/{TEMPLATE_ID}/restore-factory", headers=no_csrf,
    ).status_code == 403
    # 带上 token 就通
    assert client.post(
        f"/api/templates/{TEMPLATE_ID}/instantiate", json={}, headers=auth,
    ).status_code == 200


# --------------------------------------------------------------------------- #
# 读面
# --------------------------------------------------------------------------- #
def test_schema_endpoint_publishes_the_frozen_contract(client: TestClient):
    auth = login_owner(client)
    body = client.get("/api/templates/schema", headers=auth).json()
    assert body["schema_version"] == "1.0.0"
    assert len(body["essential_keys"]) == 8
    assert body["controller_id"] == "controller"


def test_listing_returns_factory_templates_with_overview(client: TestClient):
    auth = login_owner(client)
    body = client.get("/api/templates", headers=auth).json()
    ids = {i["template_id"] for i in body["items"]}
    assert {"writing-pipeline", "research-pipeline", "development-pipeline"} <= ids
    card = next(i for i in body["items"] if i["template_id"] == TEMPLATE_ID)
    assert card["overview"]["member_count"] == 4
    assert card["overview"]["estimate"]["estimated"] is True


def test_listing_rejects_an_unknown_scenario(client: TestClient):
    auth = login_owner(client)
    resp = client.get("/api/templates", headers=auth, params={"scenario": "nope"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_scenario"


def test_detail_exposes_eight_essentials_with_explanations(client: TestClient):
    auth = login_owner(client)
    body = client.get(f"/api/templates/{TEMPLATE_ID}", headers=auth).json()
    assert body["problems"] == []
    assert len(body["essentials_view"]) == 8
    assert all(item["explain"] for item in body["essentials_view"])
    assert body["controller_warnings"] == []


def test_unknown_template_is_404_with_envelope(client: TestClient):
    auth = login_owner(client)
    resp = client.get("/api/templates/does-not-exist", headers=auth)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "template_not_found"


# --------------------------------------------------------------------------- #
# 实例化
# --------------------------------------------------------------------------- #
def test_instantiate_is_runnable_with_no_empty_required_fields(client: TestClient):
    auth = login_owner(client)
    body = client.post(f"/api/templates/{TEMPLATE_ID}/instantiate",
                       json={"tier": "novice"}, headers=auth).json()
    assert body["runnable"] is True
    assert body["unresolved"] == []
    assert body["system"]["controller"]["system_prompt"]


def test_instantiate_reports_missing_fields_instead_of_failing_silently(client: TestClient):
    auth = login_owner(client)
    body = client.post(
        f"/api/templates/{TEMPLATE_ID}/instantiate",
        json={"overrides": {"termination": ""}}, headers=auth,
    ).json()
    assert body["runnable"] is False
    assert "termination" in body["unresolved"]
    assert "跑不通" in body["message"]


def test_instantiate_body_rejects_unknown_fields(client: TestClient):
    """``owner_id`` 绝不能从 body 进来（BUG-03：请求体不能提升权限）。"""
    auth = login_owner(client)
    resp = client.post(
        f"/api/templates/{TEMPLATE_ID}/instantiate",
        json={"owner_id": "someone-else"}, headers=auth,
    )
    assert resp.status_code == 422


def test_instantiate_rejects_unknown_override_keys(client: TestClient):
    auth = login_owner(client)
    resp = client.post(
        f"/api/templates/{TEMPLATE_ID}/instantiate",
        json={"overrides": {"role": "admin"}}, headers=auth,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "override_unknown_key"


# --------------------------------------------------------------------------- #
# 总控提示词体检与恢复出厂
# --------------------------------------------------------------------------- #
def test_controller_check_warns_but_does_not_block(client: TestClient):
    auth = login_owner(client)
    ok = client.post(f"/api/templates/{TEMPLATE_ID}/controller-check",
                     json={"draft_prompt": "你是总控，你自己写代码"}, headers=auth).json()
    assert ok["blocking"] is False
    assert any("controller_scope_risk" in w for w in ok["warnings"])
    # 出厂提示词不该被误判
    clean = client.post(
        f"/api/templates/{TEMPLATE_ID}/controller-check",
        json={"draft_prompt": "你是总控，负责任务分配。你不得直接执行具体任务。"},
        headers=auth,
    ).json()
    assert clean["warnings"] == []


def test_restore_factory_round_trips_the_factory_prompt(client: TestClient):
    auth = login_owner(client)
    factory = client.get(f"/api/templates/{TEMPLATE_ID}", headers=auth).json()["controller"]
    out = client.post(f"/api/templates/{TEMPLATE_ID}/restore-factory", headers=auth).json()
    assert out["restored_from"] == "factory"
    assert out["controller_prompt"] == factory["system_prompt"]


# --------------------------------------------------------------------------- #
# 模板 ↔ 代码
# --------------------------------------------------------------------------- #
def test_expand_to_code_is_lossless_and_uses_the_restricted_channel(client: TestClient):
    auth = login_owner(client)
    body = client.post(f"/api/templates/{TEMPLATE_ID}/expand-to-code", headers=auth).json()
    assert body["roundtrip_consistent"] is True
    assert "agent" in body["verbs"]
    assert "@@ TEMPLATE-SPEC" not in body["code"]      # 注释块用的是 ▼/▲ 标记
    assert "TEMPLATE-SPEC-BEGIN" in body["code"]


def test_import_code_saves_a_new_template(client: TestClient):
    auth = login_owner(client)
    exported = client.post(f"/api/templates/{TEMPLATE_ID}/expand-to-code",
                           headers=auth).json()
    out = client.post(
        "/api/templates/import-code",
        json={"code": exported["code"], "base_template_id": TEMPLATE_ID,
              "name": "接口层另存模板"},
        headers=auth,
    ).json()
    assert out["source_template"] == TEMPLATE_ID
    assert out["template"]["name"] == "接口层另存模板"


# --------------------------------------------------------------------------- #
# 一键试跑规格（不假装已执行）
# --------------------------------------------------------------------------- #
def test_example_run_returns_the_spec_without_claiming_execution(client: TestClient):
    auth = login_owner(client)
    body = client.get(f"/api/templates/{TEMPLATE_ID}/example-run", headers=auth).json()
    assert body["executed"] is False
    assert body["example_task"]["goal"]
    assert body["steps"]


# --------------------------------------------------------------------------- #
# 手册质量（W8）
# --------------------------------------------------------------------------- #
def test_manual_quality_reports_gaps_for_a_short_manual(client: TestClient):
    auth = login_owner(client)
    body = client.post("/api/templates/manual-quality",
                       json={"markdown": "# 手册\n\n没有故障目录。"}, headers=auth).json()
    assert body["ok"] is False
    assert body["gaps"]


def test_manual_quality_from_path_without_file_is_404(client: TestClient):
    auth = login_owner(client)
    resp = client.get("/api/templates/manual-quality", headers=auth)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "manual_not_found"


# --------------------------------------------------------------------------- #
# openapi 形状：路由被约定式自动发现
# --------------------------------------------------------------------------- #
def test_template_paths_are_auto_discovered(app):
    paths = app.openapi()["paths"]
    for expected in (
        "/api/templates",
        "/api/templates/schema",
        "/api/templates/layers",
        "/api/templates/quality-tiers",
        "/api/templates/manual-quality",
        "/api/templates/import-code",
        "/api/templates/{template_id}",
        "/api/templates/{template_id}/instantiate",
        "/api/templates/{template_id}/controller-check",
        "/api/templates/{template_id}/restore-factory",
        "/api/templates/{template_id}/expand-to-code",
        "/api/templates/{template_id}/example-run",
    ):
        assert expected in paths, f"missing {expected}"


def test_no_template_path_ends_with_plan(app):
    """护栏：``test_guest_account`` 断言「没有端点以 /plan 结尾」；模板路径必须避开。"""
    offenders = [p for p in app.openapi()["paths"] if p.endswith("/plan")]
    assert offenders == []
