"""统一调度中心单测（A-统一接入-08 · 补齐包3）。

覆盖：同池注册与路由、优先级队列、并发上限（全局 + worker 级）、回收
（reclaim 后迟到结果丢弃）、双向状态回传、取消、诚实拒绝（无路由/队列满）。
线程时序一律用 Event + eventually 轮询，不靠 sleep 碰运气。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from find_yourself.services.scheduler import (
    CHANNEL_A2A,
    CHANNEL_EXTERNAL_AGENT,
    CHANNEL_INTERNAL_AGENT,
    CHANNEL_MCP,
    DispatchRequest,
    NoRouteError,
    SchedulerError,
    SchedulerOverloaded,
    TaskRecord,
    UnifiedScheduler,
    UnknownTaskError,
    WorkerSpec,
)


def eventually(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _ok_worker(req: DispatchRequest) -> Any:
    return {"echo": req.payload, "worker": True}


# --------------------------------------------------------------------------- #
# 路由与同池
# --------------------------------------------------------------------------- #


def test_no_route_rejects_and_records():
    sch = UnifiedScheduler()
    with pytest.raises(NoRouteError):
        sch.submit(DispatchRequest(channel=CHANNEL_MCP, payload={"x": 1}))
    # 记录仍留档，状态诚实为 rejected
    tasks = sch.list_tasks(status="rejected")
    assert len(tasks) == 1
    assert tasks[0].status == "rejected"
    assert any(e["status"] == "rejected" for e in tasks[0].status_events)


def test_same_pool_internal_and_external_workers():
    """内部 agent 与外部成品 agent 注册进同一个注册表，只按 channel 路由。"""
    sch = UnifiedScheduler()
    sch.register_simple_worker("internal-butler", CHANNEL_INTERNAL_AGENT,
                               lambda r: {"side": "internal"})
    sch.register_simple_worker("external-peri", CHANNEL_EXTERNAL_AGENT,
                               lambda r: {"side": "external"})
    rec_in = sch.submit_and_wait(
        DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    rec_ext = sch.submit_and_wait(
        DispatchRequest(channel=CHANNEL_EXTERNAL_AGENT, payload={}))
    assert rec_in.status == "succeeded" and rec_in.result == {"side": "internal"}
    assert rec_ext.status == "succeeded" and rec_ext.result == {"side": "external"}
    channels = {w["channel"] for w in sch.workers()}
    assert channels == {CHANNEL_INTERNAL_AGENT, CHANNEL_EXTERNAL_AGENT}


def test_capability_routing_matches_tags_and_worker_id():
    sch = UnifiedScheduler()
    sch.register_simple_worker("generic", CHANNEL_INTERNAL_AGENT,
                               lambda r: "generic", tags=("generic",))
    sch.register_simple_worker("researcher", CHANNEL_INTERNAL_AGENT,
                               lambda r: "researcher", tags=("research", "web"))
    rec = sch.submit_and_wait(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, requested_capability="research", payload={}))
    assert rec.result == "researcher"
    assert rec.worker_id == "researcher"
    # worker_id 本身也可作为 capability 定点路由
    rec2 = sch.submit_and_wait(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, requested_capability="generic", payload={}))
    assert rec2.worker_id == "generic"


def test_route_prefers_least_loaded_then_priority_bonus():
    sch = UnifiedScheduler()
    gate = threading.Event()
    started = threading.Event()

    def blocker(req: DispatchRequest) -> str:
        started.set()
        gate.wait(5)
        return "slow"

    # 阻塞任务占住 bonus 高的 "preferred"；负载相同时 bonus 决定选择。
    sch.register_simple_worker("plain", CHANNEL_A2A,
                               lambda r: "plain", priority_bonus=0)
    sch.register_simple_worker("preferred", CHANNEL_A2A, blocker, priority_bonus=5)
    first = sch.submit(DispatchRequest(channel=CHANNEL_A2A,
                                       requested_capability="preferred", payload={}))
    assert eventually(started.is_set)
    # preferred active=1 → least-loaded 的 plain 被选中
    second = sch.submit_and_wait(DispatchRequest(channel=CHANNEL_A2A, payload={}))
    assert second.worker_id == "plain"
    gate.set()
    assert eventually(lambda: first.status == "succeeded")
    # 双双空闲后：bonus 高者优先
    third = sch.submit_and_wait(DispatchRequest(channel=CHANNEL_A2A, payload={}))
    assert third.worker_id == "preferred"


# --------------------------------------------------------------------------- #
# 优先级队列与并发上限
# --------------------------------------------------------------------------- #


def test_priority_order_under_contention():
    """并发占满后排队任务按优先级出队：3 → 5 → 9（同优先级 FIFO 由另条测试覆盖）。"""
    sch = UnifiedScheduler(max_concurrent=1)
    gate = threading.Event()
    started_first = threading.Event()
    start_order: list[str] = []
    lock = threading.Lock()

    def runner(req: DispatchRequest) -> str:
        with lock:
            start_order.append(req.payload["name"])
        if req.payload["name"] == "first":
            started_first.set()
            gate.wait(5)
        return req.payload["name"]

    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, runner)
    first = sch.submit(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, payload={"name": "first"}))
    assert eventually(started_first.is_set)
    t_low = sch.submit(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, priority=9, payload={"name": "low"}))
    t_high = sch.submit(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, priority=3, payload={"name": "high"}))
    t_mid = sch.submit(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, priority=5, payload={"name": "mid"}))
    assert t_low.status == t_high.status == t_mid.status == "queued"
    gate.set()
    for t in (first, t_low, t_high, t_mid):
        assert sch.wait(t.task_id, timeout=5).status == "succeeded"
    assert start_order == ["first", "high", "mid", "low"]


def test_fifo_within_same_priority():
    sch = UnifiedScheduler(max_concurrent=1)
    gate = threading.Event()
    release = {"on": False}

    def _run(req: DispatchRequest) -> int:
        if not release["on"]:
            gate.wait(5)
        return req.payload["n"]

    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, _run)
    first = sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={"n": 0}))
    # first 占住名额后，同优先级 1/2/3 按提交顺序出队
    queued = [
        sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, priority=5, payload={"n": i}))
        for i in (1, 2, 3)
    ]
    release["on"] = True
    gate.set()
    results = [sch.wait(t.task_id, timeout=5).result for t in queued]
    assert results == [1, 2, 3]
    assert sch.wait(first.task_id, timeout=5).result == 0


def test_global_concurrency_limit_queues_across_workers():
    sch = UnifiedScheduler(max_concurrent=1)
    gate = threading.Event()
    started = threading.Event()

    def blocker(req: DispatchRequest) -> str:
        started.set()
        gate.wait(5)
        return "slow"

    sch.register_simple_worker("slow", CHANNEL_INTERNAL_AGENT, blocker)
    sch.register_simple_worker("fast", CHANNEL_MCP, lambda r: "fast")
    first = sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    assert eventually(started.is_set)
    second = sch.submit(DispatchRequest(channel=CHANNEL_MCP, payload={}))
    assert second.status == "queued"
    gate.set()
    assert eventually(lambda: sch.wait(second.task_id, timeout=5).status == "succeeded")
    assert sch.wait(first.task_id, timeout=5).result == "slow"


def test_worker_parallel_limit():
    sch = UnifiedScheduler()
    gate = threading.Event()
    started = threading.Event()
    concurrent_peak = {"now": 0, "max": 0}
    lock = threading.Lock()

    def worker(req: DispatchRequest) -> int:
        with lock:
            concurrent_peak["now"] += 1
            concurrent_peak["max"] = max(concurrent_peak["max"], concurrent_peak["now"])
        started.set()
        gate.wait(5)
        with lock:
            concurrent_peak["now"] -= 1
        return 1

    sch.register_simple_worker("solo", CHANNEL_A2A, worker, max_parallel=1)
    first = sch.submit(DispatchRequest(channel=CHANNEL_A2A, payload={}))
    assert eventually(started.is_set)
    second = sch.submit(DispatchRequest(channel=CHANNEL_A2A, payload={}))
    assert second.status == "queued"
    gate.set()
    assert eventually(lambda: sch.wait(second.task_id, timeout=5).status == "succeeded")
    assert sch.wait(first.task_id, timeout=5).status == "succeeded"
    assert concurrent_peak["max"] == 1, "worker max_parallel=1 不允许并行超 1"


def test_queue_limit_overload_rejects():
    sch = UnifiedScheduler(max_concurrent=1, queue_limit=1)
    gate = threading.Event()
    started = threading.Event()

    def blocker(req: DispatchRequest) -> str:
        started.set()
        gate.wait(5)
        return "x"

    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, blocker)
    running = sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    assert eventually(started.is_set)
    queued = sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    assert queued.status == "queued"
    with pytest.raises(SchedulerOverloaded):
        sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    gate.set()
    assert eventually(lambda: sch.wait(running.task_id, timeout=5).status == "succeeded")
    assert eventually(lambda: sch.wait(queued.task_id, timeout=5).status == "succeeded")


# --------------------------------------------------------------------------- #
# 回收 / 取消
# --------------------------------------------------------------------------- #


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_reclaim_stale_frees_slot_and_discards_late_result():
    clock = FakeClock()
    sch = UnifiedScheduler(clock=clock)
    gate = threading.Event()
    entered = threading.Event()

    def stuck(req: DispatchRequest) -> str:
        entered.set()
        gate.wait(5)
        return "too late"

    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, stuck, max_parallel=1)
    first = sch.submit(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, payload={}, timeout_seconds=1.0))
    assert eventually(entered.is_set)
    clock.now += 2.0
    reclaimed = sch.reclaim_stale()
    assert reclaimed == [first.task_id]
    record = sch.get_task(first.task_id)
    assert record.status == "reclaimed"
    # 名额已释放：同 worker 新任务可立即派发
    second = sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    assert second.status in ("dispatched", "running")
    # 迟到结果被丢弃
    gate.set()
    assert eventually(lambda: second.status == "succeeded")
    time.sleep(0.2)
    assert sch.get_task(first.task_id).status == "reclaimed"
    assert sch.get_task(first.task_id).result is None
    # 终态后回传被拒收
    assert sch.update_status(first.task_id, "running") is False


def test_reclaim_then_progress_report_refused():
    clock = FakeClock()
    sch = UnifiedScheduler(clock=clock)
    gate = threading.Event()
    entered = threading.Event()
    reported_after_reclaim = {"value": None}

    def runner(req: DispatchRequest) -> str:
        entered.set()
        gate.wait(5)
        reported_after_reclaim["value"] = req.report("progress", {"pct": 50})
        return "done"

    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, runner)
    task = sch.submit(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, payload={}, timeout_seconds=0.5))
    assert eventually(entered.is_set)
    clock.now += 1.0
    sch.reclaim_stale()
    gate.set()
    assert eventually(lambda: task.status == "reclaimed" or sch.get_task(task.task_id).status == "reclaimed")
    # 线程稍后结束并尝试回传：update_status 拒收（终态）
    assert sch.update_status(task.task_id, "progress", data={"pct": 50}) is False


def test_cancel_queued_task():
    sch = UnifiedScheduler(max_concurrent=1)
    gate = threading.Event()
    started = threading.Event()

    def blocker(req: DispatchRequest) -> str:
        started.set()
        gate.wait(5)
        return "x"

    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, blocker)
    first = sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    assert eventually(started.is_set)
    queued = sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    assert queued.status == "queued"
    assert sch.cancel(queued.task_id) is True
    assert queued.status == "cancelled"
    gate.set()
    assert eventually(lambda: first.status == "succeeded")


# --------------------------------------------------------------------------- #
# 双向状态回传
# --------------------------------------------------------------------------- #


def test_executor_progress_report_bidirectional():
    sch = UnifiedScheduler()
    seen: list[TaskRecord] = []
    unsubscribe = sch.subscribe(lambda record: seen.append(record))

    def runner(req: DispatchRequest) -> dict:
        assert req.report is not None
        ok = req.report("progress", {"step": 1, "total": 2})
        assert ok is True
        return {"done": True}

    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, runner)
    task = sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    sch.wait(task.task_id, timeout=5)
    kinds = [e["status"] for e in task.status_events]
    assert "progress" in kinds
    progress = next(e for e in task.status_events if e["status"] == "progress")
    assert progress["data"] == {"step": 1, "total": 2}
    assert kinds[-1] == "succeeded"
    unsubscribe()
    before = len(seen)
    sch.submit_and_wait(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    assert len(seen) == before, "退订后不再收到回传"


def test_listener_receives_terminal_event():
    sch = UnifiedScheduler()
    terminal: list[str] = []
    sch.subscribe(lambda record: (
        terminal.append(record.task_id) if record.status in ("succeeded", "failed") else None
    ))
    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, _ok_worker)
    task = sch.submit_and_wait(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    assert task.task_id in terminal


# --------------------------------------------------------------------------- #
# 校验与查询
# --------------------------------------------------------------------------- #


def test_request_validation_rejects_bad_channel_priority_timeout():
    sch = UnifiedScheduler()
    with pytest.raises(SchedulerError):
        sch.submit(DispatchRequest(channel="carrier-pigeon", payload={}))
    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, _ok_worker)
    with pytest.raises(SchedulerError):
        sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, priority=0, payload={}))
    with pytest.raises(SchedulerError):
        sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, priority=10, payload={}))
    with pytest.raises(SchedulerError):
        sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, timeout_seconds=0, payload={}))


def test_duplicate_task_id_and_unknown_task():
    sch = UnifiedScheduler()
    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, _ok_worker)
    req = DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}, task_id="fixed-id")
    sch.submit_and_wait(req)
    with pytest.raises(SchedulerError):
        sch.submit(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}, task_id="fixed-id"))
    with pytest.raises(UnknownTaskError):
        sch.wait("nope")


def test_executor_failure_captures_error():
    sch = UnifiedScheduler()

    def boom(req: DispatchRequest) -> None:
        raise RuntimeError("executor exploded")

    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, boom)
    task = sch.submit_and_wait(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={}))
    assert task.status == "failed"
    assert "RuntimeError" in task.error and "executor exploded" in task.error


def test_stats_and_workers_shape():
    sch = UnifiedScheduler(max_concurrent=3, queue_limit=7)
    sch.register_simple_worker("a", CHANNEL_INTERNAL_AGENT, _ok_worker)
    sch.register_simple_worker("b", CHANNEL_A2A, _ok_worker)
    stats = sch.stats()
    assert stats["workers"] == 2
    assert stats["max_concurrent"] == 3
    assert stats["queue_limit"] == 7
    assert stats["channels"] == [CHANNEL_A2A, CHANNEL_INTERNAL_AGENT]
    workers = sch.workers()
    assert {w["worker_id"] for w in workers} == {"a", "b"}
    assert all({"channel", "max_parallel", "active", "tags"} <= set(w) for w in workers)


def test_task_record_public_shape():
    sch = UnifiedScheduler()
    sch.register_simple_worker("w", CHANNEL_INTERNAL_AGENT, _ok_worker)
    task = sch.submit_and_wait(DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={"k": "v"}))
    public = task.to_public()
    assert public["status"] == "succeeded"
    assert public["worker_id"] == "w"
    assert public["result"] == {"echo": {"k": "v"}, "worker": True}
    assert {"task_id", "channel", "action", "priority", "status_events"} <= set(public)


def test_same_channel_workers_route_by_capability_tags():
    """回归：同通道多个内部 worker（dispatch / delegation）互不串扰。

    接线约定：每个 worker 用 tags 声明能力，请求用 requested_capability 钉住。
    """
    sch = UnifiedScheduler()
    sch.register_simple_worker(
        "internal.dispatch-child", CHANNEL_INTERNAL_AGENT,
        lambda r: r.payload["run"](), tags=("dispatch",))
    sch.register_simple_worker(
        "internal.delegation-subtask", CHANNEL_INTERNAL_AGENT,
        lambda r: r.payload["runner"](), tags=("delegation",))
    a = sch.submit_and_wait(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, requested_capability="dispatch",
        payload={"run": lambda: "ran-dispatch"}))
    b = sch.submit_and_wait(DispatchRequest(
        channel=CHANNEL_INTERNAL_AGENT, requested_capability="delegation",
        payload={"runner": lambda: "ran-delegation"}))
    assert a.worker_id == "internal.dispatch-child" and a.result == "ran-dispatch"
    assert b.worker_id == "internal.delegation-subtask" and b.result == "ran-delegation"
