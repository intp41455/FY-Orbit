"""闭环接线回归：HITL↔Bus 播报 + 总控发布通道 + Orchestrator 签名契约。

这些用例**刻意不 mock 总线**：闭环的价值全在「消息真的落到总线上、真的按
system/system 身份落下、读侧真的鉴权」，mock 掉就等于什么都没验。

覆盖三处闭环接线：
  - ``AgentBusService.publish_as_role``：总控主动向 dm 房间发消息（Step 2）
  - ``HitlInterruptService.interrupt/decide`` 的总线播报（Step 1）
  - ``Orchestrator.bind`` 与真实 ``HubService.invoke`` 的签名契约（Step 3）
"""

from __future__ import annotations

import pytest

from find_yourself.db.hitl_models import HitlInterrupt
from find_yourself.runtime.agent_bus import AgentBus
from find_yourself.services.actor import Actor
from find_yourself.services.bus_service import AgentBusService
from find_yourself.services.errors import PermissionDenied, ValidationFailed
from find_yourself.services.hitl import HitlInterruptService
from find_yourself.services.hub.connections import HubService
from find_yourself.services.hub.orchestrator import Orchestrator, _bind_invoke

OWNER = "o-loop"
ROLE = "coordinator"
ROOM = f"dm:owner:{OWNER}:agent:{ROLE}"

# session / engine / bus 全部复用 tests/conftest.py 的全局 fixture：
# conftest 用 StaticPool + check_same_thread=False，内存库才能跨线程共享
# （TestClient 在别的线程跑请求，默认 SingletonThreadPool 会给那个线程一个空库，
# 表现为 "no such table: hub_connections"）。这里**不要**自己再建 session。


@pytest.fixture()
def bus() -> AgentBus:
    return AgentBus()


@pytest.fixture()
def owner() -> Actor:
    return Actor(subject_type="owner", owner_id=OWNER)


@pytest.fixture()
def hub_client(session, owner):
    """只挂 hub 路由的最小 App，带上**真实的**异常处理器。

    必须调 ``register_exception_handlers`` —— 否则 ``ValidationFailed`` 会以 500
    冒出来，测试就会把「参数校验没接上」误判成「服务崩了」。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from find_yourself.api.deps import get_actor, get_services
    from find_yourself.api.errors import register_exception_handlers
    from find_yourself.api.routes import hub as hub_routes

    class _Svc:
        def __init__(self, s):
            self.session = s

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(hub_routes.router)
    app.dependency_overrides[get_actor] = lambda: owner
    app.dependency_overrides[get_services] = lambda: _Svc(session)
    app.dependency_overrides[hub_routes.csrf_protected] = lambda: owner
    with TestClient(app) as client:
        yield client


# --------------------------------------------------------------------------- #
# Step 2：总控发布通道
# --------------------------------------------------------------------------- #
class TestPublishAsRole:
    def test_controller_can_publish_to_dm_room(self, session, bus, owner):
        svc = AgentBusService(session, bus=bus, auto_reply=False, background=False)
        out = svc.publish_as_role(ROOM, ROLE, kind="text", content="总控已接手。")

        assert out["from_identity"] == f"agent:{ROLE}"
        assert out["content"] == "总控已接手。"

    def test_owner_read_side_still_authorizes(self, session, bus, owner):
        """写侧可绕过，**读侧必须鉴权** —— 用户只能看到自己有权看的房间。"""
        svc = AgentBusService(session, bus=bus, auto_reply=False, background=False)
        svc.publish_as_role(ROOM, ROLE, kind="text", content="x")

        ref = svc.resolve_room(owner, ROOM)
        assert ref.room == ROOM and ref.kind == "dm"

    def test_stranger_cannot_read_others_dm(self, session, bus):
        svc = AgentBusService(session, bus=bus, auto_reply=False, background=False)
        svc.publish_as_role(ROOM, ROLE, kind="text", content="x")

        with pytest.raises(PermissionDenied):
            svc.resolve_room(Actor(subject_type="owner", owner_id="o-stranger"), ROOM)

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"room": "", "role": ROLE, "kind": "text", "content": "x"}, id="empty_room"),
            pytest.param({"room": ROOM, "role": "", "kind": "text", "content": "x"}, id="empty_role"),
            pytest.param({"room": ROOM, "role": ROLE, "kind": "bogus", "content": "x"}, id="bad_kind"),
            pytest.param({"room": ROOM, "role": ROLE, "kind": "text", "content": "  "}, id="blank_content"),
        ],
    )
    def test_rejects_bad_arguments(self, session, bus, kwargs):
        svc = AgentBusService(session, bus=bus, auto_reply=False, background=False)
        with pytest.raises(ValidationFailed):
            svc.publish_as_role(**kwargs)

    def test_oversized_content_rejected(self, session, bus):
        svc = AgentBusService(session, bus=bus, auto_reply=False, background=False)
        with pytest.raises(ValidationFailed):
            svc.publish_as_role(ROOM, ROLE, kind="text", content="x" * 9000)


# --------------------------------------------------------------------------- #
# Step 1：HITL → Bus 播报
# --------------------------------------------------------------------------- #
class TestHitlBusBroadcast:
    def test_interrupt_broadcasts_pending_notice(self, session, bus, owner):
        hitl = HitlInterruptService(session, bus=bus)
        before = len(bus.history(ROOM))

        hitl.interrupt(
            owner, "exec-1", "deploy.gate",
            options=["approve", "reject"], reason="需人工确认",
            timeout_seconds=900, room=ROOM,
        )

        messages = bus.history(ROOM)
        assert len(messages) == before + 1
        notice = messages[-1]
        assert notice.kind == "system"
        assert notice.from_identity == "system"
        assert notice.mention == f"owner:{OWNER}"
        assert "deploy.gate" in notice.content
        assert "approve" in notice.content and "reject" in notice.content

    def test_decide_broadcasts_decision(self, session, bus, owner):
        hitl = HitlInterruptService(session, bus=bus)
        view = hitl.interrupt(
            owner, "exec-2", "cp", options=["approve", "reject"],
            timeout_seconds=900, room=ROOM,
        )

        decided = hitl.decide(owner, view["id"], "approve", room=ROOM)

        assert decided["status"] == "approved"
        final = bus.history(ROOM)[-1]
        assert final.kind == "system"
        assert "approve" in final.content

    def test_no_room_means_no_broadcast(self, session, bus, owner):
        """不传 room 时**完全不播报**，保持与接线前一致的调用面。"""
        hitl = HitlInterruptService(session, bus=bus)
        before = len(bus.history(ROOM))

        view = hitl.interrupt(
            owner, "exec-3", "cp", options=["approve"], timeout_seconds=600,
        )

        assert len(bus.history(ROOM)) == before
        assert view["id"]

    def test_broadcast_failure_does_not_break_approval(self, session, owner):
        """总线是通知通道，不是权威账本 —— 它挂了审批必须照样成功。"""

        class _ExplodingBus:
            def publish(self, *a, **k):
                raise RuntimeError("bus down")

        hitl = HitlInterruptService(session, bus=_ExplodingBus())
        view = hitl.interrupt(
            owner, "exec-4", "cp", options=["approve"],
            timeout_seconds=600, room=ROOM,
        )

        assert view["id"]
        assert session.get(HitlInterrupt, view["id"]) is not None

    def test_timeout_none_means_never_expires(self, session, bus, owner):
        """默认不传 timeout → expires_at=None → 审批永久悬空（已知坑，勿忘）。"""
        hitl = HitlInterruptService(session, bus=bus)

        forever = hitl.interrupt(owner, "exec-5", "cp", options=["approve"])
        assert session.get(HitlInterrupt, forever["id"]).expires_at is None

        bounded = hitl.interrupt(
            owner, "exec-6", "cp", options=["approve"], timeout_seconds=300,
        )
        assert session.get(HitlInterrupt, bounded["id"]).expires_at is not None


# --------------------------------------------------------------------------- #
# Step 3：Orchestrator 签名契约（防回归）
# --------------------------------------------------------------------------- #
class TestOrchestratorInvokeContract:
    def test_real_invoke_takes_actor_first(self):
        """铁证：真实 ``HubService.invoke`` 的第一个位置参数是 actor。

        编排器按 ``invoke(conn_id, ...)`` 调用；若这一条变了，说明真实签名变了，
        契约必须跟着重写 —— 正是为了不让错位再次被「假绿测试」掩盖。
        """
        import inspect

        params = list(inspect.signature(HubService.invoke).parameters)
        assert params[1] == "actor"
        assert params[2] == "conn_id"

    def test_raw_invoke_injection_is_misaligned(self):
        """把 ``hub.invoke`` 直接注入编排器必然错位（这是要修的那个 bug）。"""

        class _Hub:
            def invoke(self, actor, conn_id, *, action, params=None, timeout_seconds=15.0):
                return {"result": {}}

        orch = Orchestrator(invoke=_Hub().invoke, route=lambda *a, **k: [])
        with pytest.raises(TypeError):
            orch._invoke("conn-1", action="cap", params={}, timeout_seconds=5)

    def test_bind_invoke_aligns_arguments(self):
        class _Hub:
            def invoke(self, actor, conn_id, *, action, params=None, timeout_seconds=15.0):
                return {"result": {"actor": actor, "conn_id": conn_id}}

        bound = _bind_invoke(_Hub(), "ACTOR-9")
        inner = bound("conn-1", action="cap", params={}, timeout_seconds=5)["result"]

        assert inner["actor"] == "ACTOR-9"
        assert inner["conn_id"] == "conn-1"

    def test_bound_signature_hides_actor(self):
        import inspect

        bound = _bind_invoke(object(), "A")
        params = list(inspect.signature(bound).parameters)

        assert params[0] == "conn_id"
        assert "actor" not in params

    def test_bind_requires_router_or_session(self):
        class _Hub:
            def invoke(self, actor, conn_id, *, action, params=None, timeout_seconds=15.0):
                return {"result": {}}

        with pytest.raises(ValidationFailed):
            Orchestrator.bind(_Hub(), actor="A")

    def test_bind_produces_working_orchestrator(self):
        calls: list[tuple] = []

        class _Hub:
            def invoke(self, actor, conn_id, *, action, params=None, timeout_seconds=15.0):
                calls.append((actor, conn_id, action))
                return {"result": {"text": "ok"}}

        class _Router:
            def route(self, hint, *, top_k=5, kind=None, include_unhealthy=False):
                return []

        orch = Orchestrator.bind(_Hub(), actor="OWNER-1", router=_Router())
        res = orch._invoke("conn-x", action="cap", params={}, timeout_seconds=5)

        assert res["result"]["text"] == "ok"
        assert calls[-1] == ("OWNER-1", "conn-x", "cap")


# --------------------------------------------------------------------------- #
# Step 3：编排的 HTTP 入口（此前完全缺失 → 死代码）
# --------------------------------------------------------------------------- #
class TestOrchestrateEndpointsWired:
    def test_three_endpoints_registered(self):
        """编排器此前**零 HTTP 入口**，是死代码 —— 这里钉住三个入口不许再消失。"""
        from find_yourself.api.routes import hub as hub_routes

        paths = {r.path for r in hub_routes.router.routes}
        assert "/api/hub/orchestrate" in paths
        assert "/api/hub/orchestrate/pipeline" in paths
        assert "/api/hub/orchestrate/capabilities" in paths

    def test_orchestrate_is_write_protected(self):
        """编排会真的调外部 Agent（花钱），必须走 CSRF 保护。

        断言的是 ``Depends.dependency`` **就是** ``deps.csrf_protected`` 本尊。
        早先这里写的是 ``isinstance(dep, Depends) or dep is not Parameter.empty``
        —— 那个断言几乎必然为真（凡有默认值都满足第二支），等于形同虚设：
        把 csrf_protected 换成裸 get_actor 它照样绿。
        """
        import inspect

        from fastapi.params import Depends

        from find_yourself.api.deps import csrf_protected
        from find_yourself.api.routes import hub as hub_routes

        by_path = {r.path: r for r in hub_routes.router.routes}
        for path in ("/api/hub/orchestrate", "/api/hub/orchestrate/pipeline"):
            dep = inspect.signature(by_path[path].endpoint).parameters["actor"].default
            assert isinstance(dep, Depends), f"{path} 的 actor 依赖不是 Depends"
            assert dep.dependency is csrf_protected, (
                f"{path} 的 actor 依赖是 {dep.dependency!r}，不是 csrf_protected；"
                f"裸 get_actor 意味着无 CSRF 防护"
            )

    def test_capabilities_endpoint_is_read_only(self):
        """前置体检是只读的，用 get_actor 即可 —— 它不该要 CSRF token。"""
        import inspect

        from fastapi.params import Depends

        from find_yourself.api.deps import csrf_protected, get_actor
        from find_yourself.api.routes import hub as hub_routes

        by_path = {r.path: r for r in hub_routes.router.routes}
        dep = inspect.signature(
            by_path["/api/hub/orchestrate/capabilities"].endpoint
        ).parameters["actor"].default
        assert isinstance(dep, Depends)
        assert dep.dependency is get_actor, (
            f"只读端点应直接用 get_actor，实际 {dep.dependency!r}"
        )
        assert dep.dependency is not csrf_protected

    def test_pipeline_stages_required(self):
        from pydantic import ValidationError

        from find_yourself.api.routes.hub import PipelineRequest

        with pytest.raises(ValidationError):
            PipelineRequest(stages=[])

    def test_orchestrate_mode_restricted(self):
        """mode 只允许 sequential/parallel —— 其它值应在参数校验层就拒掉。"""
        from pydantic import ValidationError

        from find_yourself.api.routes.hub import OrchestrateRequest

        assert OrchestrateRequest(task="t", mode="sequential").mode == "sequential"
        assert OrchestrateRequest(task="t", mode="parallel").mode == "parallel"
        with pytest.raises(ValidationError):
            OrchestrateRequest(task="t", mode="bogus")

    def test_capabilities_endpoint_shape(self, hub_client):
        """前置体检：一个 Agent 都没有时必须诚实说 can_orchestrate=false。"""
        got = hub_client.get("/api/hub/orchestrate/capabilities").json()

        assert got["can_orchestrate"] is False
        assert got["agent_count"] == 0
        assert got["capability_count"] == 0
        assert got["max_workers"] == 3

    def test_capabilities_endpoint_counts_real_agents(self, hub_client, session):
        """反向分支：真有一个 Agent 时必须报 true —— 否则前置体检是死的。

        只测「空库报 false」是不够的：一个无论有没有 Agent 都返回 false 的
        实现同样能通过上一条用例，那这个体检对用户毫无价值。
        """
        from find_yourself.db.workbench_models import HubConnection

        session.add(HubConnection(
            id="conn-1",
            owner_id=OWNER,
            name="我的云端 Agent",
            kind="openai_chat",
            capabilities=[
                {"name": "chat", "tags": ["write"], "aliases": ["写作"]},
                {"name": "review", "tags": ["audit"]},
            ],
            state="active",
        ))
        session.commit()

        got = hub_client.get("/api/hub/orchestrate/capabilities").json()

        assert got["can_orchestrate"] is True
        assert got["agent_count"] == 1, "两条能力属同一连接，连接数应去重为 1"
        assert got["capability_count"] == 2
        assert got["kinds"] == ["openai_chat"]

    def test_capabilities_endpoint_excludes_other_owners(self, hub_client, session):
        """别人的连接不算我的可编排资源 —— 越权是 403，此处是「不可见」。"""
        from find_yourself.db.workbench_models import HubConnection

        session.add(HubConnection(
            id="conn-x",
            owner_id="o-someone-else",
            name="他人的 Agent",
            kind="openai_chat",
            capabilities=[{"name": "chat", "tags": []}],
            state="active",
        ))
        session.commit()

        got = hub_client.get("/api/hub/orchestrate/capabilities").json()

        assert got["agent_count"] == 0
        assert got["can_orchestrate"] is False

    def test_orchestrate_rejects_empty_steps(self, hub_client):
        """mode=parallel 却没给 aspects → 服务层拒。

        状态码按项目既有约定是 422（``ValidationFailed`` 的映射），这里不硬改
        语义，只钉住「不返回 200 + 带明确业务码」。
        """
        resp = hub_client.post(
            "/api/hub/orchestrate",
            json={"task": "写份周报", "mode": "parallel", "aspects": []},
        )

        assert resp.status_code == 422, resp.text
        body = resp.json()
        # 错误码必须是明确的业务码，不能是空壳 500
        assert body["error"]["code"] == "hub_orchestrate_empty", body
