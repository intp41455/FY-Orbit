"""P15 任务档案库与企业模式 HTTP 契约测试。

服务层已在 ``tests/unit/test_dossier.py`` 覆盖；这里只测 HTTP 层：认证 / CSRF /
错误信封 / body 不提权 / 真实任务行的档案与简报 / 知识沉淀落盘与隔离 /
企业模式的目录、转换、映射与接入文档 / openapi 路径自动发现。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner

from find_yourself.db.models import Task, TaskEvent

OWNER_ID = "owner"


@pytest.fixture()
def auth(client: TestClient) -> dict[str, str]:
    return login_owner(client)


@pytest.fixture()
def isolated_dossier(tmp_path, monkeypatch):
    """知识模板落 tmp，测试不脏仓库 ``.runtime/``。"""
    monkeypatch.setenv("FY_DOSSIER_DIR", str(tmp_path / "dossier"))
    return tmp_path


@pytest.fixture()
def mk(session_maker):
    counter = {"n": 0}

    def _make(**kw) -> Task:
        counter["n"] += 1
        task = Task(
            id=kw.pop("task_id", f"ds-task-{counter['n']}"),
            owner_id=kw.pop("owner_id", OWNER_ID),
            goal=kw.pop("goal", "把产品资料变成三条小红书文案"),
            deadline=kw.pop("deadline", datetime(2027, 1, 1, tzinfo=timezone.utc)),
            idempotency_key=kw.pop("idempotency_key", f"ds-idem-{counter['n']}"),
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


@pytest.fixture()
def mk_event(session_maker):
    counter = {"n": 0}

    def _make(task_id: str, kind: str = "status", **kw) -> None:
        counter["n"] += 1
        session = session_maker()
        try:
            session.add(TaskEvent(id=kw.pop("id", f"ev-{task_id}-{kind}-{counter['n']}"),
                                  task_id=task_id, owner_id=OWNER_ID, kind=kind, **kw))
            session.commit()
        finally:
            session.close()

    return _make


# --------------------------------------------------------------------------- #
# 认证与 CSRF
# --------------------------------------------------------------------------- #
def test_archive_requires_authentication(client: TestClient):
    assert client.get("/api/dossier/tasks/anything/archive").status_code == 401


def test_enterprise_catalog_requires_authentication(client: TestClient):
    assert client.get("/api/dossier/enterprise").status_code == 401


def test_distill_and_mappings_require_csrf(client: TestClient, isolated_dossier):
    auth = login_owner(client)
    no_csrf: dict[str, str] = {}
    assert client.post("/api/dossier/tasks/x/distill", json={}, headers=no_csrf).status_code == 403
    assert client.post("/api/dossier/enterprise/generic/mappings",
                       json={"mappings": [{"from": "a", "to": "b"}]},
                       headers=no_csrf).status_code == 403
    assert client.post("/api/dossier/enterprise/generic/mappings",
                       json={"mappings": [{"from": "corp", "to": "agent.id"}]},
                       headers=auth).status_code == 200


# --------------------------------------------------------------------------- #
# 档案 / 简报 / 复盘
# --------------------------------------------------------------------------- #
def test_archive_endpoint_exposes_objective_and_sources(client: TestClient, auth, mk):
    task = mk(status="running", stage="execution", progress_percent=35)
    body = client.get(f"/api/dossier/tasks/{task.id}/archive", headers=auth).json()
    assert body["objective"]["goal"] == task.goal
    assert body["objective"]["progress_percent"] == 35
    assert "decisions" in body["sources"]


def test_archive_of_another_owners_task_is_404(client: TestClient, auth, mk):
    theirs = mk(owner_id="other-owner")
    resp = client.get(f"/api/dossier/tasks/{theirs.id}/archive", headers=auth)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "task_not_found"


def test_briefing_is_bounded_and_reports_truncation(client: TestClient, auth, mk, mk_event):
    task = mk(blocked_reason="等评审")
    for i in range(15):
        mk_event(task.id, kind="progress", detail={"i": i})
    body = client.get(f"/api/dossier/tasks/{task.id}/briefing", headers=auth,
                      params={"limit": 400}).json()
    assert body["chars"] <= 400
    assert "卡点：等评审" in body["briefing"]
    assert body["archive_endpoint"].endswith("/archive")


def test_briefing_rejects_a_useless_limit_over_http(client: TestClient, auth, mk):
    task = mk()
    assert client.get(f"/api/dossier/tasks/{task.id}/briefing", headers=auth,
                      params={"limit": 5}).status_code == 422


def test_retrospective_reports_metrics_and_lessons(client: TestClient, auth, mk, mk_event):
    task = mk(blocked_reason="上游没就绪")
    mk_event(task.id, kind="status", from_status="queued", to_status="waiting_input")
    body = client.get(f"/api/dossier/tasks/{task.id}/retrospective", headers=auth).json()
    assert body["metrics"]["change_events"] == 1
    assert any(l["kind"] == "blocker" for l in body["lessons"])
    assert "任务复盘" in body["report"]


# --------------------------------------------------------------------------- #
# 知识沉淀
# --------------------------------------------------------------------------- #
def test_distill_then_list_and_detail(client: TestClient, auth, mk, isolated_dossier):
    task = mk()
    out = client.post(f"/api/dossier/tasks/{task.id}/distill",
                      json={"name": "文案经验模板", "tags": ["writing"]},
                      headers=auth).json()
    assert out["knowledge_id"]
    assert "经验模板" in out["title"]

    listing = client.get("/api/dossier/knowledge", headers=auth).json()
    assert listing["total"] == 1
    assert listing["items"][0]["source_task_id"] == task.id

    detail = client.get(f"/api/dossier/knowledge/{out['knowledge_id']}",
                        headers=auth).json()
    assert "复用时怎么改" in detail["markdown"]


def test_unknown_knowledge_is_404(client: TestClient, auth, isolated_dossier):
    resp = client.get("/api/dossier/knowledge/nope", headers=auth)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "knowledge_not_found"


# --------------------------------------------------------------------------- #
# 企业模式（A-三重模式-03）
# --------------------------------------------------------------------------- #
def test_enterprise_catalog_declares_reuse_instead_of_new_tenancy(client: TestClient, auth):
    body = client.get("/api/dossier/enterprise", headers=auth).json()
    assert {"generic", "spring-ai-chatclient", "langgraph", "openai-assistants"} <= {
        a["target"] for a in body["adapters"]
    }
    assert "grant" in body["governance"]["permission_source"]
    assert body["governance"]["single_loop_kernel"] is True


def test_enterprise_adapt_reports_unmapped_fields(client: TestClient, auth):
    body = client.post("/api/dossier/enterprise/generic/adapt", headers=auth, json={
        "external": {"name": "x", "system_prompt": "s", "tools": ["a"],
                     "corpRule": {"level": 2}},
    }).json()
    assert body["unmapped"] == ["corpRule.level"]
    assert body["spec"]["extensions"]["unmapped"]["corpRule"]["level"] == 2
    assert body["executed"] is False


def test_enterprise_adapt_body_rejects_owner_id(client: TestClient, auth):
    resp = client.post("/api/dossier/enterprise/generic/adapt", headers=auth, json={
        "external": {}, "owner_id": "someone-else",
    })
    assert resp.status_code == 422


def test_enterprise_onboarding_doc_has_the_mapping_table(client: TestClient, auth):
    body = client.get("/api/dossier/enterprise/spring-ai-chatclient/onboarding",
                      headers=auth).json()
    assert "企业接入文档" in body["markdown"]
    assert "不提供第二套多租户" in body["markdown"]


def test_enterprise_unknown_target_is_404(client: TestClient, auth):
    resp = client.post("/api/dossier/enterprise/nope/adapt", headers=auth,
                       json={"external": {"name": "x"}})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "enterprise_target_unknown"


# --------------------------------------------------------------------------- #
# openapi 形状
# --------------------------------------------------------------------------- #
def test_dossier_paths_are_auto_discovered(app):
    paths = app.openapi()["paths"]
    for expected in (
        "/api/dossier/tasks/{task_id}/archive",
        "/api/dossier/tasks/{task_id}/briefing",
        "/api/dossier/tasks/{task_id}/retrospective",
        "/api/dossier/tasks/{task_id}/distill",
        "/api/dossier/knowledge",
        "/api/dossier/knowledge/{knowledge_id}",
        "/api/dossier/enterprise",
        "/api/dossier/enterprise/{target}/adapt",
        "/api/dossier/enterprise/{target}/mappings",
        "/api/dossier/enterprise/{target}/onboarding",
    ):
        assert expected in paths, f"missing {expected}"


def test_no_dossier_path_ends_with_plan(app):
    offenders = [p for p in app.openapi()["paths"] if p.endswith("/plan")]
    assert offenders == []
