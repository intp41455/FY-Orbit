"""任务看板 HTTP 层测试（A-任务看板-01～13 的 API 面）。

服务层行为已在 ``tests/unit/test_kanban_board.py`` 覆盖；本文件只测**HTTP 契约
这一层**特有的东西，重复测业务逻辑没有意义：

* 未认证拒绝（401）；
* 写操作必须有 CSRF（这是 ``csrf_protected`` 的存在理由）；
* **owner 隔离**：A 用户绝对看不到 / 动不了 B 的任务，且按 404 而非 403 回
  （不泄露存在性）；
* 错误信封形状 ``{"error": {code, message}}``（FROZEN_CONTRACT §1，可被前端
  ``kanbanErrorCode()`` 判定）；
* 三态进度语义（不传 / null / 给值）在 HTTP 上真的分得开；
* ``owner_id`` 不能从 body 注入（BUG-03）；
* openapi 里确实挂着这些路径，且**没有**以 ``/plan`` 结尾的路径。

用真实 FastAPI app + 真实 SQLite（``tests/api/conftest.py`` 的 ``client`` /
``app`` / ``session_maker`` fixture），不 mock 掉被测路由本身。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner

from find_yourself.db.models import Task

#: ⚠️ 这是 ``POST /auth/local/dev-token`` 返回的 **owner_id**，不是 OIDC subject。
#: 实测（probe 打印）：``login: 200 {"status":"ok","owner_id":"owner", ...}``。
#: 用错成 ``owner-sub-123``（那是 helpers.py 里的 OWNER_SUB）会让插进去的任务
#: 对当前 actor 完全不可见——board 为空、detail 404、terminate 404，而且这些
#: 失败看起来像「功能坏了」，其实只是数据属于别人。
OWNER_ID = "owner"


@pytest.fixture()
def auth(client: TestClient) -> dict[str, str]:
    return login_owner(client)


@pytest.fixture()
def mk(session_maker):
    """Factory inserting a Task **straight into the shared in-memory DB**.

    🔴 为什么不走 ``POST /api/tasks``：那个端点会**启动真实工作流**，工作流随后
    会在断言之间改写这一行（status / progress），把 HTTP 契约测试变成竞态测试。
    实测过两个症状：写完 ``progress_percent=70`` 再读回来是 ``0``；terminate
    第二次不是幂等，因为状态已被工作流改回。本文件要验的是 HTTP 契约，不是工作流
    引擎，所以任务直接落库。``tests/api/conftest.py`` 的 ``engine`` 与 app 共用
    同一个 StaticPool 内存库，所以这里插的行 HTTP 请求读得到。
    """
    counter = {"n": 0}

    def _make(**kw) -> Task:
        counter["n"] += 1
        task = Task(
            id=kw.pop("task_id", f"kb-task-{counter['n']}"),
            owner_id=kw.pop("owner_id", OWNER_ID),
            goal=kw.pop("goal", "看板任务"),
            deadline=kw.pop("deadline", datetime(2027, 1, 1, tzinfo=timezone.utc)),
            idempotency_key=kw.pop("idempotency_key", f"kb-idem-{counter['n']}"),
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


# --------------------------------------------------------------------------- #
# 认证与 CSRF
# --------------------------------------------------------------------------- #
def test_board_requires_authentication(client: TestClient):
    assert client.get("/api/kanban/board").status_code == 401


def test_burndown_requires_authentication(client: TestClient):
    assert client.get("/api/kanban/board/burndown").status_code == 401


def test_detail_requires_authentication(client: TestClient):
    assert client.get("/api/kanban/tasks/anything").status_code == 401


def test_mutations_require_a_csrf_token(client: TestClient, auth: dict[str, str], mk):
    """写路由挂的是 ``csrf_protected``：只带会话 cookie、不带 CSRF token 必须 403。

    ``login_owner()`` 只返回 ``X-CSRF-Token``（会话本身在 cookie 里），所以
    「去掉 CSRF」就是**不传这个头**。
    """
    task = mk()
    no_csrf: dict[str, str] = {}
    assert client.put(
        f"/api/kanban/tasks/{task.id}/progress",
        json={"progress_percent": 50}, headers=no_csrf,
    ).status_code == 403
    assert client.post(
        f"/api/kanban/tasks/{task.id}/pause", json={"reason": "x"}, headers=no_csrf,
    ).status_code == 403
    # 带上 token 就通
    assert client.put(
        f"/api/kanban/tasks/{task.id}/progress",
        json={"progress_percent": 50}, headers=auth,
    ).status_code == 200


# --------------------------------------------------------------------------- #
# 空板：诚实的空形状
# --------------------------------------------------------------------------- #
def test_empty_board_is_an_honest_shape(client: TestClient, auth: dict[str, str]):
    body = client.get("/api/kanban/board", headers=auth).json()
    assert [c["id"] for c in body["columns"]] == ["todo", "doing", "blocked", "done"]
    assert all(c["cards"] == [] and c["count"] == 0 for c in body["columns"])
    assert body["summary"]["total_cards"] == 0
    assert body["summary"]["weighted_progress"] == 0
    assert body["summary"]["red_band_count"] == 0
    assert "cancelled_count" in body["summary"]


def test_board_rejects_a_nonsense_escalation_window(client: TestClient, auth):
    resp = client.get("/api/kanban/board", headers=auth, params={"escalation_hours": -1})
    assert resp.status_code == 422


def test_burndown_rejects_a_nonsense_window(client: TestClient, auth):
    resp = client.get("/api/kanban/board/burndown", headers=auth, params={"days": 0})
    assert resp.status_code == 422


def test_burndown_without_data_reports_partial(client: TestClient, auth, mk):
    """没有完成事件时必须 ``partial=true``，不能返回一条看起来正常的曲线。"""
    mk()
    body = client.get("/api/kanban/board/burndown", headers=auth, params={"days": 5}).json()
    assert body["partial"] is True
    assert body["sampled_days"] == 0
    assert all(p["sampled"] is False for p in body["actual"])
    assert body["initial_weight"] == 1


# --------------------------------------------------------------------------- #
# 看板内容
# --------------------------------------------------------------------------- #
def test_board_places_a_task_in_the_todo_column(client: TestClient, auth, mk):
    task = mk(goal="在 todo 列")
    body = client.get("/api/kanban/board", headers=auth).json()
    todo = next(c for c in body["columns"] if c["id"] == "todo")
    assert [c["id"] for c in todo["cards"]] == [task.id]
    card = todo["cards"][0]
    assert card["progress_source"] == "self"
    assert card["red_band"] is False
    assert card["scheduled"] is False


def test_blocked_status_lands_in_the_blocked_column(client: TestClient, auth, mk):
    mk(status="waiting_input")
    body = client.get("/api/kanban/board", headers=auth).json()
    blocked = next(c for c in body["columns"] if c["id"] == "blocked")
    assert blocked["count"] == 1
    assert body["summary"]["red_band_count"] == 0   # 阻塞但无红带信号，不冒充红带


def test_cancelled_tasks_are_counted_but_not_placed(client: TestClient, auth, mk):
    mk(status="cancelled")
    mk(status="cancelled")
    body = client.get("/api/kanban/board", headers=auth).json()
    assert body["summary"]["total_cards"] == 0
    assert body["summary"]["cancelled_count"] == 2


def test_detail_returns_subtasks_history_and_dependency_sides(client: TestClient, auth, mk):
    parent = mk(goal="父任务")
    client.put(
        f"/api/kanban/tasks/{parent.id}/progress", json={"critical": True}, headers=auth,
    )
    detail = client.get(f"/api/kanban/tasks/{parent.id}", headers=auth).json()
    assert detail["task"]["id"] == parent.id
    assert detail["task"]["critical"] is True
    assert detail["subtasks"] == []
    assert detail["checklist"] == []
    assert detail["dependencies"] == {"blocked_by": [], "blocks": []}
    assert any(e["kind"] == "progress" for e in detail["history"])


def test_detail_of_an_unknown_task_is_404(client: TestClient, auth):
    resp = client.get("/api/kanban/tasks/nope-does-not-exist", headers=auth)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "task_not_found"


# --------------------------------------------------------------------------- #
# 进度三态语义必须在 HTTP 上分得开
# --------------------------------------------------------------------------- #
def test_progress_absent_null_and_value_are_three_different_things(
    client: TestClient, auth, mk,
):
    task = mk()
    url = f"/api/kanban/tasks/{task.id}/progress"

    # 1) 给值
    body = client.put(url, json={"progress_percent": 70, "weight": 3}, headers=auth).json()
    assert body["own_progress"] == 70
    assert body["weight"] == 3

    # 2) 空 body：不改
    body = client.put(url, json={}, headers=auth).json()
    assert body["own_progress"] == 70
    assert body["weight"] == 3

    # 3) 显式 null：清空
    body = client.put(url, json={"progress_percent": None}, headers=auth).json()
    assert body["own_progress"] == 0        # NULL 回落到状态机 = 未开始
    assert body["weight"] == 3             # 没传的字段不动


def test_progress_rejects_out_of_range(client: TestClient, auth, mk):
    task = mk()
    for bad in (-1, 101):
        resp = client.put(
            f"/api/kanban/tasks/{task.id}/progress",
            json={"progress_percent": bad}, headers=auth,
        )
        assert resp.status_code == 422


def test_progress_body_rejects_unknown_fields(client: TestClient, auth, mk):
    """``owner_id`` 绝不能从 body 进来（BUG-03：请求体不能提升权限）。"""
    task = mk()
    resp = client.put(
        f"/api/kanban/tasks/{task.id}/progress",
        json={"progress_percent": 10, "owner_id": "someone-else"}, headers=auth,
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# 排期（路径是 /schedule 不是 /plan）
# --------------------------------------------------------------------------- #
def test_schedule_round_trips_and_clears(client: TestClient, auth, mk):
    task = mk()
    url = f"/api/kanban/tasks/{task.id}/schedule"
    body = client.put(
        url,
        json={"planned_start": "2026-09-01T09:00:00Z", "planned_end": "2026-09-05T17:00:00Z"},
        headers=auth,
    ).json()
    assert body["scheduled"] is True
    assert body["planned_start"].startswith("2026-09-01T09:00")
    cleared = client.put(
        url, json={"planned_start": None, "planned_end": None}, headers=auth,
    ).json()
    assert cleared["scheduled"] is False


# --------------------------------------------------------------------------- #
# 依赖
# --------------------------------------------------------------------------- #
def test_dependency_lifecycle_and_cycle_rejection(client: TestClient, auth, mk):
    a = mk(goal="A")
    b = mk(goal="B")
    c = mk(goal="C")

    assert client.post(
        f"/api/kanban/tasks/{b.id}/dependencies",
        json={"depends_on_task_id": a.id}, headers=auth,
    ).status_code == 201
    assert client.post(
        f"/api/kanban/tasks/{c.id}/dependencies",
        json={"depends_on_task_id": b.id}, headers=auth,
    ).status_code == 201

    # a -> c 会成环（已有 c->b->a）
    resp = client.post(
        f"/api/kanban/tasks/{a.id}/dependencies",
        json={"depends_on_task_id": c.id}, headers=auth,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "dependency_cycle"

    # 删掉一条边后同样的操作就成立
    assert client.delete(
        f"/api/kanban/tasks/{b.id}/dependencies/{a.id}", headers=auth,
    ).status_code == 200
    assert client.post(
        f"/api/kanban/tasks/{a.id}/dependencies",
        json={"depends_on_task_id": c.id}, headers=auth,
    ).status_code == 201


def test_self_dependency_is_rejected(client: TestClient, auth, mk):
    task = mk()
    resp = client.post(
        f"/api/kanban/tasks/{task.id}/dependencies",
        json={"depends_on_task_id": task.id}, headers=auth,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "self_dependency"


def test_start_is_gated_by_dependencies_over_http(client: TestClient, auth, mk):
    upstream = mk(goal="上游")
    downstream = mk(goal="下游")
    client.post(
        f"/api/kanban/tasks/{downstream.id}/dependencies",
        json={"depends_on_task_id": upstream.id}, headers=auth,
    )
    resp = client.post(f"/api/kanban/tasks/{downstream.id}/start", json={}, headers=auth)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "dependency_blocking"
    # 上游自己没被挡住，可以启动
    assert client.post(
        f"/api/kanban/tasks/{upstream.id}/start", json={}, headers=auth,
    ).status_code == 200


# --------------------------------------------------------------------------- #
# 生命周期
# --------------------------------------------------------------------------- #
def test_terminate_is_idempotent_over_http(client: TestClient, auth, mk):
    task = mk()
    url = f"/api/kanban/tasks/{task.id}/terminate"
    first = client.post(url, json={}, headers=auth)
    assert first.status_code == 200
    assert first.json()["changed"] is True
    assert first.json()["status"] == "cancelled"
    second = client.post(url, json={}, headers=auth)
    assert second.status_code == 200
    assert second.json()["changed"] is False
    assert second.json()["idempotent"] is True


def test_illegal_transition_is_409_with_an_explanation(client: TestClient, auth, mk):
    task = mk()
    resp = client.post(f"/api/kanban/tasks/{task.id}/pause", json={}, headers=auth)
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "illegal_transition"
    assert "queued" in body["message"]


def test_unknown_operation_is_rejected(client: TestClient, auth, mk):
    task = mk()
    resp = client.post(f"/api/kanban/tasks/{task.id}/explode", json={}, headers=auth)
    assert resp.status_code in (404, 422)


def test_pause_then_resume_round_trip(client: TestClient, auth, mk):
    task = mk()
    assert client.post(f"/api/kanban/tasks/{task.id}/start", json={}, headers=auth).status_code == 200
    paused = client.post(
        f"/api/kanban/tasks/{task.id}/pause", json={"reason": "等评审"}, headers=auth,
    ).json()
    assert paused["status"] == "waiting_input"
    assert paused["blocked_reason"] == "等评审"
    assert paused["blocked_since"] is not None

    # 暂停后卡片出现在 blocked 列
    body = client.get("/api/kanban/board", headers=auth).json()
    blocked = next(c for c in body["columns"] if c["id"] == "blocked")
    assert [c["id"] for c in blocked["cards"]] == [task.id]
    assert blocked["cards"][0]["blocked_reason"] == "等评审"

    resumed = client.post(f"/api/kanban/tasks/{task.id}/resume", json={}, headers=auth).json()
    assert resumed["status"] == "running"
    assert resumed["blocked_reason"] is None


# --------------------------------------------------------------------------- #
# owner 隔离
# --------------------------------------------------------------------------- #
def test_another_owners_task_is_invisible_and_untouchable(client: TestClient, auth, mk):
    """🔴 隔离用「数据属于别人」来构造，而不是登第二个账号。

    ``login_owner()`` 走 dev-token，**永远映射到同一个 owner_id**（实测
    ``owner_id="owner"``）。连登两次并不会产生第二个身份，隔离也就没被测到。
    正确做法是直接插一条属于他人 owner 的行，看当前 actor 是不是对它一无所知。
    """
    mine = mk(goal="我的任务")
    theirs = mk(owner_id="other-owner", goal="别人的私密任务")

    board = client.get("/api/kanban/board", headers=auth).json()
    ids = [c["id"] for col in board["columns"] for c in col["cards"]]
    assert ids == [mine.id]
    assert theirs.id not in ids

    # 详情按 404 回，不泄露存在性（不是 403——403 等于告诉调用者「它存在」）
    resp = client.get(f"/api/kanban/tasks/{theirs.id}", headers=auth)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "task_not_found"

    # 改动同样不可见
    assert client.put(
        f"/api/kanban/tasks/{theirs.id}/progress", json={"progress_percent": 100},
        headers=auth,
    ).status_code == 404
    assert client.post(
        f"/api/kanban/tasks/{theirs.id}/terminate", json={}, headers=auth,
    ).status_code == 404
    assert client.delete(
        f"/api/kanban/tasks/{theirs.id}/dependencies/{mine.id}", headers=auth,
    ).status_code == 404

    # 我的那条没被顺手改掉
    detail = client.get(f"/api/kanban/tasks/{mine.id}", headers=auth).json()
    assert detail["task"]["goal"] == "我的任务"


def test_dependency_cannot_target_another_owners_task(client: TestClient, auth, mk):
    mine = mk()
    theirs = mk(owner_id="other-owner")

    resp = client.post(
        f"/api/kanban/tasks/{mine.id}/dependencies",
        json={"depends_on_task_id": theirs.id},
        headers=auth,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "task_not_found"


# --------------------------------------------------------------------------- #
# openapi 形状
# --------------------------------------------------------------------------- #
def test_kanban_paths_are_mounted(app):
    paths = app.openapi()["paths"]
    for expected in (
        "/api/kanban/board",
        "/api/kanban/board/burndown",
        "/api/kanban/tasks/{task_id}",
        "/api/kanban/tasks/{task_id}/progress",
        "/api/kanban/tasks/{task_id}/schedule",
        "/api/kanban/tasks/{task_id}/dependencies",
        "/api/kanban/tasks/{task_id}/pause",
        "/api/kanban/tasks/{task_id}/resume",
        "/api/kanban/tasks/{task_id}/terminate",
        "/api/kanban/tasks/{task_id}/start",
    ):
        assert expected in paths, f"missing {expected}"


def test_no_kanban_path_ends_with_plan(app):
    """🔴 护栏：`test_guest_account` 断言「没有端点以 /plan 结尾」（防止出现能改
    账号套餐的端点）。看板的排期端点必须继续叫 ``/schedule``，别改回 ``/plan``。"""
    offenders = [p for p in app.openapi()["paths"] if p.endswith("/plan")]
    assert offenders == [], f"路径以 /plan 结尾会撞上账号套餐护栏: {offenders}"
