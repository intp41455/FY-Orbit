"""C0 / R-13：HTTP 层的诚实降级信号。

服务层逻辑在 ``tests/unit/test_task_reaper.py`` 验证；这里证明信号真的
出现在 HTTP 响应里——因为「服务层算对了但路由没把字段透出去」正是
R-13 最初那类「看起来 200 就没事」的失败模式。
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from helpers import login_owner
from sqlalchemy import select
from sqlalchemy.orm import Session

from find_yourself.db.models import Task
from find_yourself.db.types import utcnow
from find_yourself.services.task_reaper import reap_stuck_queued_tasks

# --------------------------------------------------------------------------
# /health/ready：能区分「挂了」与「从未配置」
# --------------------------------------------------------------------------


def test_health_ready_reports_degraded_when_temporal_disabled(client: TestClient):
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    # TemporalRuntime.disabled() 是默认部署形态
    assert body["temporal"] == "disabled"
    assert "degraded" in body, "降级时 readiness 必须自报降级，不能只说 ok"
    assert "temporal" in body["degraded"]


def test_health_ready_has_no_degraded_when_temporal_enabled(client: TestClient, app):
    """把 runtime 换成 enabled，degraded 必须消失——证明该字段不是硬编码。"""

    original = getattr(app.state, "temporal", None)

    class _Enabled:
        def is_enabled(self) -> bool:
            return True

        async def close(self) -> None:
            return None

    app.state.temporal = _Enabled()
    try:
        body = client.get("/health/ready").json()
        assert body["temporal"] == "ready"
        assert "degraded" not in body
    finally:
        app.state.temporal = original


def test_health_live_stays_minimal(client: TestClient):
    """``/health/live`` 是公开端点，不得泄露拓扑或降级细节。"""
    body = client.get("/health/live").json()
    assert body == {"status": "ok"}


def test_health_ready_never_leaks_connection_strings(client: TestClient):
    raw = client.get("/health/ready").text.lower()
    for leak in ("postgres://", "password", "secret", "token", "tcp://"):
        assert leak not in raw, f"readiness 响应泄露了 {leak}"


# --------------------------------------------------------------------------
# POST /api/tasks：降级时必须自报，而非假装正常
# --------------------------------------------------------------------------


def test_create_task_marks_degraded_when_temporal_unavailable(client: TestClient):
    headers = login_owner(client)
    r = client.post(
        "/api/tasks",
        json={"goal": "degraded probe", "idempotency_key": "r13-degraded-1"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "degraded" in body, "降级建任务必须带 degraded 标记"
    assert "temporal" in body["degraded"]
    assert body["workflow"]["started"] is False


def test_create_task_degraded_is_removable(client: TestClient, app):
    """同样请求，runtime enabled 时 degraded 必须消失。"""

    class _Enabled:
        def is_enabled(self) -> bool:
            return True

        async def start_task_workflow(self, payload):
            return "run-enabled"

        async def close(self) -> None:
            return None

    original = getattr(app.state, "temporal", None)
    app.state.temporal = _Enabled()
    try:
        headers = login_owner(client)
        r = client.post(
            "/api/tasks",
            json={"goal": "healthy probe", "idempotency_key": "r13-healthy-1"},
            headers=headers,
        )
        body = r.json()
        assert "degraded" not in body
        assert body["workflow"]["started"] is True
        assert body["workflow"]["status"] == "started"
    finally:
        app.state.temporal = original


def test_create_task_still_idempotent_under_degraded(client: TestClient):
    """R-13 修复不能破坏幂等契约（冻结契约 §5.2）。"""
    headers = login_owner(client)
    payload = {"goal": "idem probe", "idempotency_key": "r13-idem-1"}
    r1 = client.post("/api/tasks", json=payload, headers=headers)
    r2 = client.post("/api/tasks", json=payload, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


def test_create_task_requires_auth(client: TestClient):
    r = client.post(
        "/api/tasks", json={"goal": "anon", "idempotency_key": "r13-anon-1"}
    )
    assert r.status_code == 401


# --------------------------------------------------------------------------
# reap 与 HTTP 的衔接：reap 后任务不再显示「进行中」
# --------------------------------------------------------------------------


def test_reaped_task_no_longer_reports_running(client: TestClient, session_maker) -> None:
    """端到端：建任务 → 伪造卡死 → reap → 状态变 failed 而非永远 queued。"""
    headers = login_owner(client)
    created = client.post(
        "/api/tasks",
        json={"goal": "stuck probe", "idempotency_key": "r13-stuck-1"},
        headers=headers,
    ).json()

    session: Session = session_maker()
    try:
        task = session.scalars(select(Task).where(Task.id == created["id"])).one()
        assert task.status == "queued"
        # 把 created_at 拨回 30 分钟前，模拟「queued 后再无人接手」
        task.created_at = utcnow() - timedelta(minutes=30)
        session.commit()

        report = reap_stuck_queued_tasks(session, stuck_minutes=5)
        assert report.reaped_count == 1
    finally:
        session.close()

    after = client.get(f"/api/tasks/{created['id']}").json()
    assert after["status"] == "failed", "被 reap 的任务不能还挂在 queued 上让人以为在跑"


def test_reap_does_not_touch_recent_task(client: TestClient, session_maker) -> None:
    headers = login_owner(client)
    created = client.post(
        "/api/tasks",
        json={"goal": "recent probe", "idempotency_key": "r13-recent-1"},
        headers=headers,
    ).json()

    session: Session = session_maker()
    try:
        report = reap_stuck_queued_tasks(session, stuck_minutes=5)
        assert report.reaped_count == 0
    finally:
        session.close()

    after = client.get(f"/api/tasks/{created['id']}").json()
    assert after["status"] == "queued"
