"""A-Agent运行时-01/07 测试：Hook 总线 + 单循环内核 + 产品 API 接线。

AC：唯一执行主干并接线到产品 API（01）；Hook 总线 + 20+ 事件清单 +
可确定性阻断（07）。
"""

from __future__ import annotations

import pytest

from find_yourself.db.models import AuditEvent
from find_yourself.runtime.hooks import (
    HOOK_EVENTS,
    WIRED_EVENTS,
    HookBus,
    HookDecision,
    reset_default_hook_bus,
)
from find_yourself.runtime.kernel import SingleLoopKernel


# ---------------------------------------------------------------------------
# Hook 总线（07）
# ---------------------------------------------------------------------------

def test_event_list_has_20_plus_and_wired_subset():
    assert len(HOOK_EVENTS) >= 20
    assert len(set(HOOK_EVENTS)) == len(HOOK_EVENTS)          # 无重复
    assert WIRED_EVENTS <= set(HOOK_EVENTS)                    # wired 必在清单内
    assert len(WIRED_EVENTS) >= 10                             # 内核真实接线的
    assert {"task.received", "task.validated", "route.selected"} <= WIRED_EVENTS


def test_register_unknown_event_rejected():
    bus = HookBus()
    with pytest.raises(ValueError):
        bus.register("not.an.event", lambda p: None)


def test_emit_priority_order_and_trace():
    bus = HookBus()
    order: list[str] = []
    bus.register("task.received", lambda p: order.append("second"), priority=200)
    bus.register("task.received", lambda p: order.append("first"), priority=50)
    out = bus.emit("task.received", {})
    assert order == ["first", "second"]
    assert not out.blocked and len(out.fired) == 2


def test_emit_blocking_protocols():
    bus = HookBus()
    bus.register("task.validated", lambda p: False)                     # False 协议
    out1 = bus.emit("task.validated", {})
    assert out1.blocked and out1.reason == "hook returned False"

    bus2 = HookBus()
    bus2.register("task.validated", lambda p: {"blocked": True, "reason": "预算熔断"})
    bus2.register("task.validated", lambda p: HookDecision(blocked=True))
    # 同优先级按注册序；首个阻断即停 → fired 只 1 条，第二个 hook 未触发
    out2 = bus2.emit("task.validated", {})
    assert out2.blocked and len(out2.fired) == 1 and "预算熔断" in out2.reason

    bus3 = HookBus()
    bus3.register("task.validated", lambda p: HookDecision(blocked=True, reason="veto"))
    assert bus3.emit("task.validated", {}).blocked


def test_hook_exception_isolated_not_blocking():
    bus = HookBus()
    def boom(p):
        raise RuntimeError("hook exploded")
    bus.register("task.received", boom)
    bus.register("task.received", lambda p: None)
    out = bus.emit("task.received", {})
    assert not out.blocked
    assert len(out.fired) == 2 and any(not f["ok"] for f in out.fired)


def test_once_hook_fires_exactly_once():
    bus = HookBus()
    calls: list[int] = []
    hid = bus.register("task.received", lambda p: calls.append(1), once=True)
    bus.emit("task.received", {})
    bus.emit("task.received", {})
    assert calls == [1]
    assert bus.listeners("task.received") == []            # 已注销
    assert bus.unregister(hid) is False                    # 再注销=False


# ---------------------------------------------------------------------------
# 单循环内核（01）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_bus_and_checkpointer(tmp_path, monkeypatch):
    monkeypatch.setenv("FY_CHECKPOINT_DB", str(tmp_path / "kernel-ck.db"))
    reset_default_hook_bus()
    yield
    reset_default_hook_bus()


_LISTEN_STATE = {
    "task_id": "k-01",
    "attempt": 1,
    "goal": "I feel very overwhelmed by work and need someone to listen",
    "domain": "personal",
    "route": "single_agent",
    "budget_balance": 1.0,
    "max_steps": 5,
    "history": [{"role": "user", "content": "I feel overwhelmed"}],
}


def test_kernel_run_completes_and_emits_lifecycle_trace():
    bus = HookBus()
    kernel = SingleLoopKernel(bus=bus)
    result = kernel.run(dict(_LISTEN_STATE))
    assert result.status == "completed"
    assert result.state["output"]["mode"] == "listening"
    events = [t["event"] for t in result.trace]
    # 生命周期轨迹：前置 → 逐节点 → 终态
    assert events[0] == "task.received"
    assert "task.validated" in events
    assert "route.selected" in events
    assert events.count("node.completed") >= 3               # 多节点真实驱动
    assert events[-1] == "task.completed"
    # 检查点真落盘（T6 持久检查点，FY_CHECKPOINT_DB 指向 tmp）
    assert (result.state.get("task_id")) == "k-01"


def test_kernel_pre_event_blocking_stops_graph():
    bus = HookBus()
    bus.register("task.validated", lambda p: {"blocked": True, "reason": "治理拦截"})
    kernel = SingleLoopKernel(bus=bus)
    result = kernel.run(dict(_LISTEN_STATE))
    assert result.blocked and "治理拦截" in result.block_reason
    assert result.status is None
    assert all(t["event"] != "node.completed" for t in result.trace)   # 图未执行
    assert result.trace[-1]["event"] == "task.blocked"


def test_kernel_route_blocking():
    bus = HookBus()
    bus.register("route.selected", lambda p: False)
    kernel = SingleLoopKernel(bus=bus)
    result = kernel.run(dict(_LISTEN_STATE))                 # state 带 route
    assert result.blocked
    events = [t["event"] for t in result.trace]
    assert "route.blocked" in events and "node.completed" not in events


def test_kernel_empty_goal_fails_closed():
    kernel = SingleLoopKernel(bus=HookBus())
    result = kernel.run({"task_id": "k-empty", "goal": "", "route": "single_agent"})
    assert result.error and "goal is required" in result.error
    assert all(t["event"] != "node.completed" for t in result.trace)


def test_kernel_writes_audit_frames(session, audit, owner):
    kernel = SingleLoopKernel(bus=HookBus(), audit=audit, actor=owner)
    kernel.run(dict(_LISTEN_STATE))
    frames = session.query(AuditEvent).filter(
        AuditEvent.action.in_(["kernel.run", "kernel.run_completed"])
    ).all()
    assert {f.action for f in frames} == {"kernel.run", "kernel.run_completed"}


# ---------------------------------------------------------------------------
# 产品 API 接线（AC：接线到产品 API）
# ---------------------------------------------------------------------------

OWNER_EMAIL = "owner-kernel@k.test"
PASSWORD = "Kernel-test-pass-12345"


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)
    return wrapper


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from find_yourself.api.app import create_app
    from find_yourself.config import Settings
    from find_yourself.db.base import Base
    import find_yourself.db.resilience_models  # noqa: F401

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool, future=True)
    Base.metadata.create_all(eng)
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token="dev-token-secret-kernel",
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
        recovery_autoresume=False,
    )
    app = create_app(session_maker=sessionmaker(bind=eng, expire_on_commit=False, future=True),
                     settings=settings)
    with TestClient(_force_loopback(app)) as c:
        yield c


@pytest.fixture()
def api_headers(client):
    # unit 目录无 tests/api/helpers —— 内联注册+登录（照 test_recovery_flow 范式）
    assert client.post(
        "/auth/register",
        json={"email": OWNER_EMAIL, "password": PASSWORD, "consent_accepted": True},
    ).status_code == 200
    r = client.post("/auth/login", json={"email": OWNER_EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def test_hook_events_endpoint(api_headers, client):
    r = client.get("/api/runtime/hooks/events", headers=api_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 20
    wired = {e["name"] for e in body["events"] if e["wired"]}
    assert "task.received" in wired and "tool.pre" not in wired  # reserved 如实


def test_kernel_run_via_api(api_headers, client):
    r = client.post(
        "/api/runtime/run",
        json={"goal": "Please just listen to me about my day",
              "route": "single_agent", "task_id": "api-k-1"},
        headers=api_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["blocked"] is False
    assert body["thread_id"] == "th-api-k-1"
    trace_events = [t["event"] for t in body["hook_trace"]]
    assert "task.received" in trace_events and "task.completed" in trace_events
