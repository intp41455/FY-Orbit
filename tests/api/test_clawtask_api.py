"""P16 任务可移植 HTTP 契约测试（A-任务可移植-03/04/05）。

服务层行为已在 ``tests/unit/test_clawtask.py`` 覆盖；本文件只测 HTTP 层：
认证 / CSRF / 错误信封 / body 不能提权 / 真实任务行导出 / 市场与冬眠的 HTTP 往返 /
openapi 路径被约定式自动发现。

一处**跨包集成**断言值得单独说：``test_export_from_real_task_uses_the_p13_contract``
从真实任务行导出 ``.clawtask``，其 ``system_template`` 段必须与 **P13** 冻结的
模板契约（schema v1.0.0 + 八类必备项）一致——这验证的正是「P16 照 P13 契约实现」。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner

from find_yourself.db.models import Task

OWNER_ID = "owner"


@pytest.fixture()
def auth(client: TestClient) -> dict[str, str]:
    return login_owner(client)


@pytest.fixture()
def isolated_dirs(tmp_path, monkeypatch):
    """市场与冬眠包都指到 tmp，测试不脏仓库 ``.runtime/``。"""
    monkeypatch.setenv("FY_CLAWTASK_MARKET_DIR", str(tmp_path / "market"))
    monkeypatch.setenv("FY_CLAWTASK_HIBERNATION_DIR", str(tmp_path / "hib"))
    return tmp_path


@pytest.fixture()
def mk(session_maker):
    """直接落一条真实 Task（不走 POST /api/tasks，免得工作流改写断言对象）。"""
    counter = {"n": 0}

    def _make(**kw) -> Task:
        counter["n"] += 1
        task = Task(
            id=kw.pop("task_id", f"ct-task-{counter['n']}"),
            owner_id=kw.pop("owner_id", OWNER_ID),
            goal=kw.pop("goal", "把产品资料变成三条小红书文案"),
            deadline=kw.pop("deadline", datetime(2027, 1, 1, tzinfo=timezone.utc)),
            idempotency_key=kw.pop("idempotency_key", f"ct-idem-{counter['n']}"),
            **kw,
        )
        session = session_maker()
        try:
            session.add(task)
            session.commit()
        finally:
            session.close()
        return task

    return _make


def _task_template_doc(client: TestClient, auth: dict[str, str]) -> dict:
    """从 P13 的真实模板详情组装一份合格的任务模板文档（跨包契约的真实使用）。"""
    detail = client.get("/api/templates/writing-pipeline", headers=auth).json()
    essentials = {item["key"]: {"value": item["value"], "explain": item["explain"]}
                  for item in detail["essentials_view"]}
    return {
        "clawtask_version": "1.0.0",
        "kind": "task_template",
        "id": "xiaohongshu-copy",
        "name": "小红书爆款文案生成",
        "goal": "输入产品，产出三条小红书文案",
        "human_brief": ("# 任务交接：小红书爆款文案生成\n\n"
                        "输入产品名与卖点，产出三条文案。任何模型读这一段即可接手。\n"),
        "created_at": "2026-10-07T00:00:00+00:00",
        "target_models": ["gpt", "claude", "qwen", "deepseek"],
        "system_template": {
            "schema_version": detail["schema_version"],
            "template_id": "writing-pipeline",
            "quality_tier": "novice",
            "essentials": essentials,
        },
        "context": {"format": "Markdown + YAML front-matter"},
        "steps": [{"id": "topic", "title": "选题", "owner": "topic", "status": "pending"}],
        "budget": {"max_model_calls": 12},
        "progress": {"status": "queued", "stage": "requirements", "percent": 0},
    }


# --------------------------------------------------------------------------- #
# 认证与 CSRF
# --------------------------------------------------------------------------- #
def test_schema_requires_authentication(client: TestClient):
    assert client.get("/api/clawtask/schema").status_code == 401


def test_market_and_hibernation_mutations_require_csrf(client: TestClient, isolated_dirs):
    auth = login_owner(client)
    no_csrf: dict[str, str] = {}
    assert client.post("/api/clawtask/market/publish",
                       json={"doc": {"kind": "task_template"}}, headers=no_csrf).status_code == 403
    assert client.post("/api/clawtask/hibernations",
                       json={"doc": None, "reason": "x"}, headers=no_csrf).status_code == 403
    # 带上 token 就不再是 CSRF 403（这里是别的错误，说明 CSRF 门已过）
    assert client.post("/api/clawtask/hibernations",
                       json={"reason": "x"}, headers=auth).status_code in (404, 422)


# --------------------------------------------------------------------------- #
# 契约与文档工具
# --------------------------------------------------------------------------- #
def test_schema_endpoint_pins_the_p13_contract(client: TestClient):
    auth = login_owner(client)
    body = client.get("/api/clawtask/schema", headers=auth).json()
    assert body["clawtask_version"] == "1.0.0"
    assert body["extension"] == ".clawtask"
    assert body["system_template_schema_version"] == "1.0.0"


def test_docs_validate_serialize_parse_roundtrip(client: TestClient, isolated_dirs):
    auth = login_owner(client)
    doc = _task_template_doc(client, auth)

    validated = client.post("/api/clawtask/docs/validate", json={"doc": doc},
                            headers=auth).json()
    assert validated["ok"] is True and validated["problems"] == []

    exported = client.post("/api/clawtask/docs/serialize",
                           json={"doc": doc}, headers=auth).json()
    assert exported["filename"].endswith(".clawtask")
    assert exported["portable"] is True

    back = client.post("/api/clawtask/docs/parse",
                       json={"text": exported["text"]}, headers=auth).json()
    assert back["doc"]["name"] == doc["name"]


def test_docs_parse_rejects_a_tampered_text(client: TestClient, isolated_dirs):
    auth = login_owner(client)
    doc = _task_template_doc(client, auth)
    text = client.post("/api/clawtask/docs/serialize", json={"doc": doc},
                       headers=auth).json()["text"]
    resp = client.post("/api/clawtask/docs/parse",
                       json={"text": text.replace("三条文案", "三百条文案")}, headers=auth)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "clawtask_invalid"


def test_docs_body_rejects_unknown_fields(client: TestClient, isolated_dirs):
    """``owner_id`` 绝不能从 body 进来（BUG-03）。"""
    auth = login_owner(client)
    resp = client.post("/api/clawtask/docs/validate",
                       json={"doc": {}, "owner_id": "someone-else"}, headers=auth)
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# 从真实任务行导出（跨包契约）
# --------------------------------------------------------------------------- #
def test_export_from_real_task_uses_the_p13_contract(client: TestClient, auth, mk):
    task = mk(status="running", stage="execution", progress_percent=40, steps=2)
    resp = client.post(f"/api/clawtask/tasks/{task.id}/export",
                       json={"template_id": "writing-pipeline"}, headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["doc"]["progress"]["percent"] == 40.0
    assert body["doc"]["progress"]["steps_done"] == 2
    # 🔴 跨包契约：导出的 system_template 必须符合 P13 冻结的 schema
    st = body["doc"]["system_template"]
    assert st["schema_version"] == "1.0.0"
    assert st["template_id"] == "writing-pipeline"
    assert len(st["essentials"]) == 8
    assert body["export"]["filename"].endswith(".clawtask")


def test_export_of_another_owners_task_is_404(client: TestClient, auth, mk):
    theirs = mk(owner_id="other-owner")
    assert client.post(f"/api/clawtask/tasks/{theirs.id}/export", json={},
                       headers=auth).status_code == 404


# --------------------------------------------------------------------------- #
# 任务市场
# --------------------------------------------------------------------------- #
def test_market_publish_list_import_unpublish(client: TestClient, auth, isolated_dirs):
    doc = _task_template_doc(client, auth)
    published = client.post("/api/clawtask/market/publish",
                            json={"doc": doc, "version": "1.0.0"}, headers=auth).json()
    item_id = published["item_id"]
    assert published["pricing"]["payments_supported"] is False

    listing = client.get("/api/clawtask/market", headers=auth,
                         params={"query": "小红书"}).json()
    assert listing["total"] == 1
    assert listing["payments_supported"] is False

    imported = client.post(f"/api/clawtask/market/{item_id}/import", headers=auth).json()
    assert imported["doc"]["name"] == doc["name"]
    assert imported["payments_supported"] is False

    assert client.get(f"/api/clawtask/market/{item_id}", headers=auth).status_code == 200
    assert client.delete(f"/api/clawtask/market/{item_id}", headers=auth).status_code == 200
    assert client.get(f"/api/clawtask/market/{item_id}", headers=auth).status_code == 404


def test_market_rejects_a_task_instance_over_http(client: TestClient, auth, isolated_dirs):
    doc = _task_template_doc(client, auth)
    doc["kind"] = "task"
    resp = client.post("/api/clawtask/market/publish", json={"doc": doc}, headers=auth)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "market_only_task_templates"


def test_market_paging_bounds_are_enforced_over_http(client: TestClient, auth):
    assert client.get("/api/clawtask/market", headers=auth,
                      params={"limit": 0}).status_code == 422
    assert client.get("/api/clawtask/market", headers=auth,
                      params={"limit": 500}).status_code == 422


# --------------------------------------------------------------------------- #
# 冬眠机制
# --------------------------------------------------------------------------- #
def test_hibernate_from_task_then_list_wake_discard(client: TestClient, auth, mk,
                                                    isolated_dirs):
    task = mk()
    created = client.post("/api/clawtask/hibernations", headers=auth, json={
        "task_id": task.id, "reason": "预算临界，先封存", "budget_percent": 96,
    }).json()
    assert created["artifact_count"] == 0
    hib_id = created["hibernation_id"]

    listing = client.get("/api/clawtask/hibernations", headers=auth).json()
    assert listing["total"] == 1
    assert listing["items"][0]["reason"] == "预算临界，先封存"

    woken = client.post(f"/api/clawtask/hibernations/{hib_id}/wake", headers=auth).json()
    assert woken["awakened"] is True
    assert woken["doc"]["id"] == task.id

    assert client.delete(f"/api/clawtask/hibernations/{hib_id}",
                         headers=auth).status_code == 200
    assert client.get("/api/clawtask/hibernations", headers=auth).json()["total"] == 0


def test_hibernate_requires_a_source(client: TestClient, auth, isolated_dirs):
    resp = client.post("/api/clawtask/hibernations", json={"reason": "x"}, headers=auth)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "hibernate_source_required"


def test_hibernation_policy_endpoint(client: TestClient, auth):
    warn = client.get("/api/clawtask/hibernation-policy", headers=auth,
                      params={"budget_percent": 85}).json()
    assert warn["warn_only"] is True and warn["should_hibernate"] is False
    force = client.get("/api/clawtask/hibernation-policy", headers=auth,
                       params={"budget_percent": 99}).json()
    assert force["should_hibernate"] is True and "budget_force" in force["triggers"]


# --------------------------------------------------------------------------- #
# openapi 形状
# --------------------------------------------------------------------------- #
def test_clawtask_paths_are_auto_discovered(app):
    paths = app.openapi()["paths"]
    for expected in (
        "/api/clawtask/schema",
        "/api/clawtask/docs/validate",
        "/api/clawtask/docs/serialize",
        "/api/clawtask/docs/parse",
        "/api/clawtask/tasks/{task_id}/export",
        "/api/clawtask/market",
        "/api/clawtask/market/publish",
        "/api/clawtask/market/{item_id}",
        "/api/clawtask/market/{item_id}/import",
        "/api/clawtask/hibernation-policy",
        "/api/clawtask/hibernations",
        "/api/clawtask/hibernations/{hibernation_id}",
        "/api/clawtask/hibernations/{hibernation_id}/wake",
    ):
        assert expected in paths, f"missing {expected}"


def test_no_clawtask_path_ends_with_plan(app):
    offenders = [p for p in app.openapi()["paths"] if p.endswith("/plan")]
    assert offenders == []
