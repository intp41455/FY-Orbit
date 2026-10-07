"""可观测与成本聚合 HTTP 契约测试（A-可观测-02/03 · P2，A-成本仪表盘-02 · P3）。

对应 ``tests/unit/test_observability.py``，只验**HTTP 契约**这一层：

* 未认证 401；错误信封统一 ``{"error": {code, message}}``
* 读路由挂 ``get_actor``、改动挂 ``csrf_protected``（本包全是只读，无改动路由）
* **owner 收敛**：A 用户看不到 B 用户的任务 / 团队 → 404，不是 403
* ``owner_id`` / ``role`` 不接受从 query 注入身份（查询参数里压根没这两个位置）
* SSE 契约：``text/event-stream`` + ``no-store`` + ``id:``/``event:``/``data:``，
  非法 ``Last-Event-ID`` 退化成 0 而不 500
* 参数边界（``days`` / ``limit`` / 分页）落到 422
* 🔴 **openapi 里确实有这 6 个路径，且路由是靠自动发现挂上的**——
  本文件**不**手动 ``include_router``，就是为了让这条守护真实有效

最后一条是本包与 P17 的关键差异：路由不再需要碰 ``routes/__init__.py``。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner

import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401
from find_yourself.db.models import BudgetReservation, Task, TaskEvent
from find_yourself.db.team_models import AgentInstance, TeamDefinition, TeamEvent
from find_yourself.db.types import utcnow
from find_yourself.services.audit import AuditService

#: dev-token 恒定映射到 "owner"（见 tests/api/test_kanban_api.py 的同名说明）
OWNER_ID = "owner"
OTHER_ID = "someone-else"


@pytest.fixture()
def auth(client: TestClient) -> dict[str, str]:
    return login_owner(client)


@pytest.fixture()
def seed(session_maker):
    """Factory writing real rows into the shared in-memory DB.

    直接写库而不走业务 API：这些端点是**读**聚合，走工作流启动任务会引入
    lease / 执行状态机一整套无关依赖，失败时更难定位。
    """
    counter = {"n": 0}

    def _seed(*, owner_id: str = OWNER_ID, steps: int = 2, max_steps: int = 8,
              events: int = 0) -> dict[str, str]:
        counter["n"] += 1
        task_id = f"t-{counter['n']}"
        team_id = f"team-{counter['n']}"
        s = session_maker()
        try:
            s.add(Task(id=task_id, owner_id=owner_id, goal="g", status="running",
                       stage="execution", steps=steps, max_steps=max_steps,
                       deadline=utcnow(), idempotency_key=f"idem-{task_id}"))
            s.add(BudgetReservation(
                id=f"br-{task_id}", task_id=task_id, period="task", scope="task",
                amount="0.25", currency="USD", state="reserved",
                idempotency_key=f"idem-br-{task_id}",
            ))
            s.add(TeamDefinition(id=team_id, owner_id=owner_id, name="crew",
                                 mode="product_native", members=[]))
            s.add(AgentInstance(id=f"ai-{counter['n']}", team_id=team_id, role="dev",
                                state="running", session_id=f"sess-{counter['n']}",
                                parent_task_id=task_id, root_task_id=task_id,
                                steps=steps, max_steps=max_steps))
            for i in range(events):
                s.add(TeamEvent(id=f"evt-{counter['n']}-{i}", team_id=team_id, seq=i + 1,
                                event_type="member.step",
                                agent_instance_id=f"ai-{counter['n']}"))
                s.add(TaskEvent(id=f"te-{task_id}-{i}", task_id=task_id, owner_id=owner_id,
                                kind="status", to_status="running"))
            s.commit()
        finally:
            s.close()
        return {"task_id": task_id, "team_id": team_id,
                "member_id": f"ai-{counter['n']}"}

    return _seed


@pytest.fixture()
def frame(session_maker):
    """Append audit frames and return the seq of each.

    ``with_message_id=False`` 时**不带** ``details.message_id``——测「关联不上」
    的用例必须显式要这种帧，不能靠 fixture 的默认值蒙对。
    """
    counter = {"n": 0}

    def _frame(
        action: str = "task.failed",
        target: str | None = "task:t-1",
        *,
        with_message_id: bool = False,
        message_id: str = "msg-1",
    ) -> int:
        counter["n"] += 1
        s = session_maker()
        try:
            details = {"message_id": message_id} if with_message_id else None
            row = AuditService(s).append(
                _owner(), action, target=target, details=details
            )
            s.commit()
            return int(row.seq)
        finally:
            s.close()

    return _frame


def _owner():
    from find_yourself.services.actor import Actor

    return Actor.owner(OWNER_ID)


# --------------------------------------------------------------------------- #
# 未认证
# --------------------------------------------------------------------------- #
class TestUnauthenticated:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/observability/logs",
            "/api/observability/performance",
            "/api/observability/performance/agents",
        ],
    )
    def test_reads_require_auth(self, client, path):
        assert client.get(path).status_code == 401

    def test_stream_requires_auth(self, client):
        assert client.get("/api/observability/logs/stream").status_code == 401

    def test_trace_requires_auth(self, client, frame):
        assert client.get("/api/observability/logs/1/trace").status_code == 401

    def test_cost_progress_requires_auth(self, client):
        assert client.get("/api/observability/cost/progress?task_id=t-1").status_code == 401


# --------------------------------------------------------------------------- #
# 日志面板
# --------------------------------------------------------------------------- #
class TestLogsEndpoint:
    def test_returns_envelope_with_seq_time_axis_and_watermark(self, client, auth, frame):
        frame()
        body = client.get("/api/observability/logs").json()
        assert body["time_axis"] == "seq"
        assert body["head_seq"] >= 1
        assert body["facets"]["total"] >= 1
        assert all(i["at"] is None for i in body["items"])

    def test_level_filter_round_trips(self, client, auth, frame):
        frame(action="task.failed")
        assert client.get("/api/observability/logs?level=error").json()["total"] >= 1
        assert client.get("/api/observability/logs?level=debug").json()["total"] == 0

    def test_unknown_level_is_422_with_code(self, client, auth):
        r = client.get("/api/observability/logs?level=catastrophe")
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "level_unknown"

    @pytest.mark.parametrize("qs", ["limit=0", "limit=501", "offset=-1"])
    def test_pagination_bounds_are_422(self, client, auth, qs):
        assert client.get(f"/api/observability/logs?{qs}").status_code == 422

    def test_error_shape_is_the_frozen_contract(self, client, auth):
        r = client.get("/api/observability/logs?level=catastrophe")
        assert set(r.json()["error"]) >= {"code", "message"}


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #
class TestStreamEndpoint:
    """SSE 契约。

    ⚠️ 这些用例**必须**用 ``max_frames`` 把流收住。不给上界时生成器会一直转，
    而 ``TestClient`` 跑在阻塞式 portal 上，客户端一断开生成器未必立刻收到
    取消信号——结果就是测试挂死（第一版就是这么挂的 15 分钟）。
    ``max_frames`` 是给真实客户端也用得上的参数，不是为测试开后门。
    """

    def _read(self, client, qs: str) -> tuple[dict, str]:
        with client.stream("GET", f"/api/observability/logs/stream?{qs}") as r:
            assert r.status_code == 200, r.status_code
            headers = dict(r.headers)
            body = "".join(r.iter_text())
        return headers, body

    def test_declares_event_stream_and_no_store(self, client, auth, frame):
        frame()
        headers, _body = self._read(
            client, "since_seq=0&poll_interval=0.5&max_frames=1"
        )
        assert headers["content-type"].startswith("text/event-stream")
        assert "no-store" in headers["cache-control"]

    def test_emits_sse_framed_payloads(self, client, auth, frame):
        frame()
        _headers, body = self._read(
            client, "since_seq=0&poll_interval=0.5&max_frames=1"
        )
        assert "id: " in body
        assert "event: log" in body
        assert "data: " in body
        # 帧里的载荷不许带编造的墙钟时间
        assert '"at": null' in body or '"at":null' in body

    def test_invalid_last_event_id_degrades_instead_of_500(self, client, auth, frame):
        """🔴 坏头部不该把整条流打断——退化成从头开始即可。"""
        frame()
        with client.stream(
            "GET",
            "/api/observability/logs/stream?since_seq=0&poll_interval=0.5&max_frames=1",
            headers={"Last-Event-ID": "not-a-number"},
        ) as r:
            assert r.status_code == 200
            assert "".join(r.iter_text())

    def test_level_filter_applies_to_the_stream(self, client, auth, frame):
        frame(action="task.failed")
        _headers, body = self._read(
            client, "since_seq=0&poll_interval=0.5&max_frames=1&level=error"
        )
        assert "event: log" in body
        _headers, empty = self._read(
            client, "since_seq=0&poll_interval=0.5&max_frames=1&level=debug"
        )
        assert "event: log" not in empty

    def test_since_seq_in_the_future_yields_no_frames_but_still_terminates(
        self, client, auth, frame,
    ):
        """🔴 「等不到新事件」必须是一个**有终点的等待**。

        只给 ``max_frames`` 不够：帧数永远不涨，生成器会一直转。
        ``max_idle_polls`` 让它在空闲若干周期后主动收尾并发 ``event: end``。
        """
        frame()
        _headers, body = self._read(
            client,
            "since_seq=99999&poll_interval=0.5&max_frames=1&max_idle_polls=1",
        )
        assert "event: log" not in body
        assert "event: end" in body
        assert '"reason": "idle"' in body or '"reason":"idle"' in body

    def test_frame_budget_reports_why_it_stopped(self, client, auth, frame):
        frame()
        _headers, body = self._read(
            client, "since_seq=0&poll_interval=0.5&max_frames=1"
        )
        assert "event: end" in body

    @pytest.mark.parametrize(
        "qs",
        ["max_frames=0", "max_frames=501", "poll_interval=0.1", "max_idle_polls=0"],
    )
    def test_stream_bounds_are_422(self, client, auth, qs):
        r = client.get(f"/api/observability/logs/stream?since_seq=0&{qs}")
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# 错误跳 trace
# --------------------------------------------------------------------------- #
class TestTraceEndpoint:
    def test_correlates_by_message_id(self, client, auth, frame, seed):
        seed()
        seq = frame(with_message_id=True)
        body = client.get(f"/api/observability/logs/{seq}/trace").json()
        assert body["basis"] == "message"
        assert body["correlation_key"] == "msg-1"

    def test_unlinkable_frame_is_not_an_error(self, client, auth, frame):
        """🔴 关联不上是**结论**，不是失败——200 + basis=none。"""
        seq = frame(action="budget.alert", target="???")
        r = client.get(f"/api/observability/logs/{seq}/trace")
        assert r.status_code == 200
        body = r.json()
        assert body["basis"] == "none"
        assert body["items"] == []
        assert body["reason"] == "no_correlation_key"

    def test_unknown_seq_is_404_with_code(self, client, auth):
        r = client.get("/api/observability/logs/999999/trace")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "log_frame_not_found"


# --------------------------------------------------------------------------- #
# 性能
# --------------------------------------------------------------------------- #
class TestPerformanceEndpoints:
    def test_overall_shape(self, client, auth, seed):
        seed()
        body = client.get("/api/observability/performance").json()
        assert body["window_days"] == 7
        assert set(body["overall"]) >= {"stage_duration_ms", "dispatch_duration_ms"}
        assert body["honesty"]["no_persisted_latency_table"]

    @pytest.mark.parametrize("qs", ["days=0", "days=91"])
    def test_window_bounds_are_422(self, client, auth, qs):
        """边界由路由的 ``Query(ge=1, le=90)`` 先挡下，落到通用的
        ``validation_failed``——这与 kanban 等路由的既有做法一致，
        服务层的 ``days_invalid`` 是给**绕过 HTTP 直调服务**时用的第二道。"""
        r = client.get(f"/api/observability/performance?{qs}")
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_agent_stats_lists_members(self, client, auth, seed):
        ids = seed(steps=3)
        body = client.get("/api/observability/performance/agents").json()
        assert body["total"] >= 1
        assert any(i["agent_instance_id"] == ids["member_id"] for i in body["items"])

    def test_agent_stats_team_filter(self, client, auth, seed):
        ids = seed()
        body = client.get(
            f"/api/observability/performance/agents?team_id={ids['team_id']}"
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["team_id"] == ids["team_id"]

    def test_agent_stats_limit_bounds_are_422(self, client, auth):
        for bad in (0, 501):
            r = client.get(f"/api/observability/performance/agents?limit={bad}")
            assert r.status_code == 422


# --------------------------------------------------------------------------- #
# 成本进度条
# --------------------------------------------------------------------------- #
class TestCostProgressEndpoint:
    def test_requires_a_target(self, client, auth):
        r = client.get("/api/observability/cost/progress")
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "target_required"

    def test_task_progress_reports_the_three_segments(self, client, auth, seed):
        ids = seed(steps=2, max_steps=8)
        body = client.get(
            f"/api/observability/cost/progress?task_id={ids['task_id']}"
        ).json()
        item = body["items"][0]
        assert item["step_current"] == 2
        assert item["step_total"] == 8
        assert item["step_progress_pct"] == 25.0
        # 剩余是区间 + 假设，不是单一预测数
        assert "remaining_steps_upper_bound" in item["remaining"]
        assert "assumption" in item["remaining"]

    def test_cost_usage_is_reported_against_the_limit(self, client, auth, seed):
        ids = seed()
        body = client.get(
            f"/api/observability/cost/progress?task_id={ids['task_id']}"
        ).json()
        assert body["items"][0]["cost"]["spent_usd"] == 0.25
        assert body["currency"] == "USD"

    def test_team_progress_lists_members(self, client, auth, seed):
        ids = seed()
        body = client.get(
            f"/api/observability/cost/progress?team_id={ids['team_id']}"
        ).json()
        assert body["total"] == 1

    def test_unknown_task_is_404(self, client, auth):
        r = client.get("/api/observability/cost/progress?task_id=nope")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "task_not_found"

    def test_unknown_team_is_404(self, client, auth):
        r = client.get("/api/observability/cost/progress?team_id=nope")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "team_not_found"


# --------------------------------------------------------------------------- #
# owner 收敛
# --------------------------------------------------------------------------- #
class TestOwnerScoping:
    def test_another_owners_task_is_404_not_403(self, client, auth, seed):
        ids = seed(owner_id=OTHER_ID)
        r = client.get(f"/api/observability/cost/progress?task_id={ids['task_id']}")
        assert r.status_code == 404

    def test_another_owners_team_is_404(self, client, auth, seed):
        ids = seed(owner_id=OTHER_ID)
        r = client.get(f"/api/observability/cost/progress?team_id={ids['team_id']}")
        assert r.status_code == 404

    def test_agent_stats_excludes_other_owners_members(self, client, auth, seed):
        seed(owner_id=OTHER_ID)
        assert client.get("/api/observability/performance/agents").json()["total"] == 0


# --------------------------------------------------------------------------- #
# 自动发现
# --------------------------------------------------------------------------- #
class TestAutoDiscovery:
    EXPECTED = (
        "/api/observability/logs",
        "/api/observability/logs/stream",
        "/api/observability/logs/{seq}/trace",
        "/api/observability/performance",
        "/api/observability/performance/agents",
        "/api/observability/cost/progress",
    )

    @pytest.mark.parametrize("path", EXPECTED)
    def test_path_exists_in_the_schema(self, app, path):
        """🔴 本文件**没有** ``include_router``——路径能出现就只可能是自动发现。"""
        assert path in app.openapi()["paths"]

    def test_no_observability_path_ends_with_plan(self, app):
        """沿用仓内既有护栏（``tests/unit/test_guest_account.py`` 的同款约定）。"""
        assert not [p for p in app.openapi()["paths"] if p.endswith("/plan")]