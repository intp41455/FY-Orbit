"""Step 4 回归：调度器周期回收 + 总线状态桥。

这些用例同样**刻意不 mock 总线**：桥接的价值全在「消息真的落到总线上、
真的带 system 身份、真的 @ 到 owner」，mock 掉就等于什么都没验。

覆盖三件事：
  1. ``reclaim_once`` 真能收掉超时任务**并**把回收播到总线（此前生产零调用者）
  2. 终态桥接只在有 room 时播报；没有 room 静默跳过
  3. 通知失败绝不阻塞调度 —— 总线挂了任务状态照样流转
"""

from __future__ import annotations

import threading
import time

import pytest

from find_yourself.runtime.agent_bus import AgentBus
from find_yourself.runtime.scheduler_bridge import (
    COORDINATOR_ROLE,
    SchedulerBusBridge,
)
from find_yourself.services.actor import Actor
from find_yourself.services.scheduler.core import (
    CHANNEL_INTERNAL_AGENT,
    TASK_FAILED,
    TASK_RECLAIMED,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    DispatchRequest,
    UnifiedScheduler,
    WorkerSpec,
)

OWNER = "o-bridge"
ROLE = COORDINATOR_ROLE
ROOM = f"dm:owner:{OWNER}:agent:{ROLE}"
CH = CHANNEL_INTERNAL_AGENT


@pytest.fixture()
def bus() -> AgentBus:
    return AgentBus()


@pytest.fixture()
def owner() -> Actor:
    return Actor(subject_type="owner", owner_id=OWNER)


@pytest.fixture()
def sched() -> UnifiedScheduler:
    """全新调度器实例（不用全局单例，避免测试间互相污染）。"""
    return UnifiedScheduler()


def _bridge(sched: UnifiedScheduler, bus) -> SchedulerBusBridge:
    """构造**并注册监听**的桥接器。

    必须走这里而不是直接 ``SchedulerBusBridge(...)``：裸构造只赋值字段，
    监听器并没有挂到调度器上，终态事件根本不会到桥接器（表现为房间空）。
    生产入口是 :meth:`SchedulerBusBridge.attach`，它做两件事——注册监听 +
    起节拍线程；这里用 ``interval=0`` 跳过节拍（避免每个用例都等一拍）。
    """
    bridge = SchedulerBusBridge.attach(sched, bus, interval=0)
    return bridge


def _register(sched: UnifiedScheduler, worker_id: str, executor) -> WorkerSpec:
    return sched.register_worker(
        WorkerSpec(worker_id=worker_id, channel=CH, executor=executor)
    )


def _register_hanging(sched: UnifiedScheduler, worker_id: str = "w-hang") -> WorkerSpec:
    """注册一个永不返回的 worker —— 任务停在 running 直到被回收。"""

    def _hang(req: DispatchRequest):
        time.sleep(30)  # 远超任何测试用的 deadline
        return {"late": True}

    return _register(sched, worker_id, _hang)


def _submit(
    sched: UnifiedScheduler,
    task_id: str,
    *,
    room: str = ROOM,
    timeout: float = 0.2,
    meta: dict | None = None,
):
    return sched.submit(
        DispatchRequest(
            channel=CH,
            action="run",
            payload={},
            timeout_seconds=timeout,
            task_id=task_id,
            meta=meta if meta is not None else {"room": room, "owner_id": OWNER},
        )
    )


# --------------------------------------------------------------------------- #
# 周期回收（此前生产零调用者）
# --------------------------------------------------------------------------- #
class TestPeriodicReclaim:
    def test_reclaim_once_collects_overdue_task(self, sched, bus):
        _register_hanging(sched)
        _submit(sched, "t-overdue")
        time.sleep(0.35)  # 让 deadline 真的过去

        bridge = SchedulerBusBridge(sched, bus)
        reclaimed = bridge.reclaim_once()

        assert "t-overdue" in reclaimed
        assert sched.get_task("t-overdue").status == TASK_RECLAIMED

    def test_reclaim_releases_concurrency_slot(self, sched, bus):
        """回收的真正价值：释放并发名额，否则卡死任务会吃光配额。"""
        _register_hanging(sched)
        _submit(sched, "t-slot", timeout=5.0)
        time.sleep(0.3)  # 等线程真的进入 running

        assert sched.stats()["running"] >= 1
        sched.get_task("t-slot").deadline = sched._clock() - 1  # 强制过期
        SchedulerBusBridge(sched, bus).reclaim_once()

        assert sched.stats()["running"] == 0

    def test_reclaim_broadcasts_to_room(self, sched, bus):
        """回收必须播到总线 —— 用户在总控那头要知道「这个任务超时了」。"""
        _register_hanging(sched)
        _submit(sched, "t-bcast", timeout=5.0)
        time.sleep(0.3)
        sched.get_task("t-bcast").deadline = sched._clock() - 1

        SchedulerBusBridge(sched, bus).reclaim_once()

        messages = bus.history(ROOM)
        assert messages, "回收后房间里应有一条消息"
        last = messages[-1]
        assert last.kind == "system"
        assert last.from_identity == "system"
        assert "超时被回收" in last.content
        assert "t-bcast" in last.content
        assert last.mention == f"owner:{OWNER}"

    def test_no_reclaim_without_overdue(self, sched, bus):
        """没到期的任务不该被收 —— 误收等于凭空杀掉正在干活的活。"""
        _register_hanging(sched)
        _submit(sched, "t-fresh", timeout=30.0)
        time.sleep(0.3)

        reclaimed = SchedulerBusBridge(sched, bus).reclaim_once()

        assert "t-fresh" not in reclaimed
        assert sched.get_task("t-fresh").status == TASK_RUNNING

    def test_beat_thread_runs_and_stops(self, sched, bus):
        """节拍线程真会自己跑起来，也真能在 close() 后退出（线程泄漏是硬缺陷）。"""
        _register_hanging(sched)
        _submit(sched, "t-beat", timeout=5.0)
        time.sleep(0.3)
        sched.get_task("t-beat").deadline = sched._clock() - 1

        bridge = SchedulerBusBridge.attach(sched, bus, interval=1.0)
        try:
            assert bridge.stats()["attached"] is True
            deadline = time.time() + 8.0
            while time.time() < deadline:
                if sched.get_task("t-beat").status == TASK_RECLAIMED:
                    break
                time.sleep(0.1)
            assert sched.get_task("t-beat").status == TASK_RECLAIMED, "节拍没在 8s 内触发回收"
        finally:
            bridge.close()

        assert bridge.stats()["attached"] is False
        assert bridge.stats()["listening"] is False

    def test_close_is_idempotent(self, sched, bus):
        """close 幂等：lifespan 与测试都可能重复调，不能因此抛异常。"""
        bridge = SchedulerBusBridge.attach(sched, bus, interval=1.0)
        bridge.close()
        bridge.close()  # 第二次不许抛

        assert bridge.stats()["attached"] is False

    def test_interval_zero_means_bridge_only(self, sched, bus):
        """interval=0 → 只要状态桥不要节拍线程（不能退化成忙等烧 CPU）。"""
        bridge = SchedulerBusBridge.attach(sched, bus, interval=0)
        try:
            assert bridge.stats()["attached"] is False
            assert bridge.stats()["listening"] is True
        finally:
            bridge.close()

    def test_thread_is_daemon(self, sched, bus):
        """节拍线程必须 daemon —— 否则关停时它会吊住进程。"""
        bridge = SchedulerBusBridge.attach(sched, bus, interval=1.0)
        try:
            assert bridge._thread.daemon is True
        finally:
            bridge.close()


# --------------------------------------------------------------------------- #
# 命名契约：三处总控 id 必须同值
# --------------------------------------------------------------------------- #
class TestCoordinatorIdConsistency:
    def test_three_sources_agree(self):
        """运行时桥接 / 模板层 / DB 默认值必须是同一个 id。

        三处各写各的曾是这个项目的真实病灶：模板层叫 controller、运行时叫
        coordinator，导致「模板下发 → 建团队 → 派单」在 id 上断开。它们不能
        互相 import（runtime 反向 import services/templates 会成环），所以
        只能靠这条测试钉住。
        """
        from find_yourself.db.team_models import TeamDefinition
        from find_yourself.services.templates.scaffold import CONTROLLER_ID

        db_default = TeamDefinition.__table__.c.coordinator_role.default.arg
        assert COORDINATOR_ROLE == CONTROLLER_ID == db_default == "coordinator"


# --------------------------------------------------------------------------- #
# 终态状态桥
# --------------------------------------------------------------------------- #
class TestTerminalBridge:
    def test_success_is_broadcast(self, sched, bus):
        """任务成功要播 —— 用户等的就是「做完没」。"""
        _register(sched, "w-ok", lambda req: {"ok": True})
        _bridge(sched, bus)  # 先挂监听，再提交
        _submit(sched, "t-ok", timeout=10.0)
        assert sched.wait("t-ok", timeout=5.0).status == TASK_SUCCEEDED

        msgs = bus.history(ROOM)
        assert msgs, "成功后房间应有消息"
        assert "已完成" in msgs[-1].content
        assert "t-ok" in msgs[-1].content

    def test_no_room_means_silent(self, sched, bus):
        """没有 room 的任务（内部批处理）静默跳过 —— 不凭空捏造会话。"""
        _register(sched, "w-noroom", lambda req: {"ok": True})
        _submit(sched, "t-noroom", timeout=10.0, meta={})
        assert sched.wait("t-noroom", timeout=5.0).status == TASK_SUCCEEDED

        assert bus.history(ROOM) == []

    def test_unrelated_meta_key_never_broadcast(self, sched, bus):
        """meta 里有别的键但没 room —— 仍应跳过，不能误投到别的房间。"""
        _register(sched, "w-x", lambda req: {"ok": True})
        _submit(sched, "t-x", timeout=10.0, meta={"other": "value"})
        assert sched.wait("t-x", timeout=5.0).status == TASK_SUCCEEDED

        for room in (ROOM, "global", "dm:owner:o-bridge:agent:other"):
            assert bus.history(room) == []

    def test_reclaimed_not_double_broadcast(self, sched, bus):
        """回收只播一次：on_task_event 跳过 reclaimed（由 announce 负责）。"""
        _register_hanging(sched)
        _submit(sched, "t-once", timeout=5.0)
        time.sleep(0.3)
        sched.get_task("t-once").deadline = sched._clock() - 1

        bridge = _bridge(sched, bus)
        bridge.reclaim_once()
        after_first = len(bus.history(ROOM))
        bridge.on_task_event(sched.get_task("t-once"))  # 不应重复播

        assert len(bus.history(ROOM)) == after_first

    def test_failure_reason_is_included(self, sched, bus):
        """失败要带真实原因，不吞不改。"""
        def _boom(req):
            raise RuntimeError("上游 401")

        _register(sched, "w-fail", _boom)
        _bridge(sched, bus)  # 先挂监听，再提交
        _submit(sched, "t-fail", timeout=10.0)
        assert sched.wait("t-fail", timeout=5.0).status == TASK_FAILED

        msgs = bus.history(ROOM)
        assert any("失败" in m.content for m in msgs), [m.content for m in msgs]
        assert any("401" in m.content for m in msgs), [m.content for m in msgs]


# --------------------------------------------------------------------------- #
# 通知失败不阻塞调度（总线是通知通道，不是权威账本）
# --------------------------------------------------------------------------- #
class TestBusFailureIsolated:
    def test_exploding_bus_does_not_break_task(self, sched):
        class _Boom:
            def publish(self, *a, **k):
                raise RuntimeError("bus down")

        _register(sched, "w-boom", lambda req: {"ok": True})
        bridge = SchedulerBusBridge(sched, _Boom())
        bridge._unsubscribe = sched.subscribe(bridge.on_task_event)

        _submit(sched, "t-boom", timeout=10.0)
        rec = sched.wait("t-boom", timeout=5.0)

        assert rec.status == TASK_SUCCEEDED, "总线挂了不该让任务状态受影响"
        assert bridge.stats()["publish_failures"] >= 1

    def test_no_bus_means_no_crash(self, sched):
        """bus=None（未接总线）时监听器必须是安全的空操作。"""
        _register(sched, "w-nobus", lambda req: {"ok": True})
        bridge = SchedulerBusBridge(sched, None)

        _submit(sched, "t-nobus", timeout=10.0)
        rec = sched.wait("t-nobus", timeout=5.0)

        assert rec.status == TASK_SUCCEEDED
        assert bridge.stats()["has_bus"] is False

    def test_close_racing_with_beat_is_safe(self, sched, bus):
        """close 撞上节拍执行 —— 不能抛、不能挂住。"""
        _register_hanging(sched)
        _submit(sched, "t-race", timeout=5.0)
        bridge = SchedulerBusBridge.attach(sched, bus, interval=1.0)
        stopper = threading.Thread(target=bridge.close)
        stopper.start()
        bridge.close()
        stopper.join(timeout=5.0)

        assert not stopper.is_alive(), "close 死锁了"