"""调度内核下沉 L1 的 re-export 等价性单测（ADR-0001 D3/D6 回归护栏）。

``services/scheduler/core.py`` 的实体已下沉到 L1 ``find_yourself.scheduling``，
``services.scheduler.core`` 退化为纯 re-export 垫片。本测试锁死四件事：

1. **实体真的搬走了**：L1 模块存在，且 ``UnifiedScheduler.__module__`` 字面等于
   ``find_yourself.scheduling``（硬判别——复制一份留在原地会红）。
2. **同一对象而非副本**：垫片 ``core`` 与包 ``services.scheduler`` 转出的每个公开
   名字，与 L1 里的对象 ``is`` 同一。
3. **进程级单例同一性**：``scheduler`` 单例从 L1 / ``services.scheduler.core`` /
   ``services.scheduler``（包）三处拿到必须是**同一个对象**——下沉最容易破的点。
4. **行为不因搬家而变**：dispatch / reclaim_stale / subscribe / 并发上限 / 优先级
   排序 / 常量值 / 异常继承关系全部逐条锁死。

导入方式刻意做成「缺失即断言失败」而非导入期 ImportError：这样在尚未下沉的老代码上
本文件也能正常收集，并以清晰的 AssertionError 报红。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import threading
import time

import pytest

from find_yourself.services import scheduler as pkg
from find_yourself.services.scheduler import core as shim

try:  # 下沉前 find_yourself.scheduling 不存在 → 记为 None，由断言给出可读失败
    l1 = importlib.import_module("find_yourself.scheduling")
except ModuleNotFoundError:  # pragma: no cover - 仅在下沉前触发
    l1 = None

_MISSING = (
    "L1 模块 find_yourself.scheduling 尚未建立：调度内核实体还没有下沉到 L1"
    "（ADR-0001 D3/D6）"
)

#: L1 必须定义的公开名字集合（= 原 core.py 的公开名 minus 私有 ``_now_iso``）。
_L1_PUBLIC = (
    # 通道
    "CHANNEL_INTERNAL_AGENT",
    "CHANNEL_EXTERNAL_AGENT",
    "CHANNEL_A2A",
    "CHANNEL_MCP",
    "CHANNEL_CLI",
    "CHANNEL_PLUGIN",
    "KNOWN_CHANNELS",
    # 任务状态机
    "TASK_QUEUED",
    "TASK_DISPATCHED",
    "TASK_RUNNING",
    "TASK_SUCCEEDED",
    "TASK_FAILED",
    "TASK_RECLAIMED",
    "TASK_REJECTED",
    "TASK_CANCELLED",
    "TERMINAL_STATES",
    "STATE_STATUSES",
    # 优先级
    "PRIORITY_MIN",
    "PRIORITY_MAX",
    "DEFAULT_PRIORITY",
    # 异常
    "SchedulerError",
    "NoRouteError",
    "SchedulerOverloaded",
    "UnknownTaskError",
    # 数据结构 / 调度中心
    "WorkerSpec",
    "DispatchRequest",
    "TaskRecord",
    "UnifiedScheduler",
    "scheduler",
)


def _l1(name: str):
    """取出 L1 模块的公开名；缺失时以断言失败而非 AttributeError 报红。"""
    assert l1 is not None, _MISSING
    assert hasattr(l1, name), f"L1 find_yourself.scheduling 未定义 {name}"
    return getattr(l1, name)


def _l1_scheduler_cls() -> type:
    return _l1("UnifiedScheduler")


# --------------------------------------------------------------------------
# 1. L1 模块存在 + 实体真的搬走了
# --------------------------------------------------------------------------
def test_l1_scheduling_module_exists():
    """L1 必须真实存在调度内核单源模块（下沉前此断言为红）。"""
    assert l1 is not None, _MISSING


def test_unified_scheduler_module_points_at_l1():
    """``__module__`` 必须指向 L1，证明实体搬走了而不是被复制一份留在原地。"""
    cls = _l1_scheduler_cls()
    assert cls.__module__ == "find_yourself.scheduling"


def test_l1_owns_every_public_name():
    """L1 必须承载原 core.py 的全部公开名，一个都不能漏。"""
    assert l1 is not None, _MISSING
    missing = [n for n in _L1_PUBLIC if not hasattr(l1, n)]
    assert not missing, f"L1 find_yourself.scheduling 缺少公开名：{missing}"


def test_l1_scheduling_does_not_depend_on_services():
    """L1 调度模块不得回头 import L2.1 的 services 包（下沉才成立的语义前提）。"""
    assert l1 is not None, _MISSING
    imported = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(l1)))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name and "services" in name for name in imported), (
        f"L1 scheduling 反向依赖 services：{sorted(n for n in imported if n)}"
    )


# --------------------------------------------------------------------------
# 2. 垫片 / 包 与 L1 的同一对象等价性（is，不是 ==）
# --------------------------------------------------------------------------
def test_shim_reexports_the_very_same_objects():
    """core.py 垫片导出的每个名字与 L1 是同一对象，不是副本。"""
    assert l1 is not None, _MISSING
    for name in _L1_PUBLIC:
        assert getattr(shim, name) is getattr(l1, name), (
            f"services.scheduler.core.{name} 与 L1 的对象不是同一实体"
        )


def test_package_reexports_the_very_same_objects():
    """包 ``services.scheduler`` 转出的每个名字与 L1 是同一对象。"""
    assert l1 is not None, _MISSING
    for name in pkg.__all__:
        assert getattr(pkg, name) is getattr(l1, name), (
            f"services.scheduler.{name} 与 L1 的对象不是同一实体"
        )


def test_package_all_is_unchanged_and_complete():
    """``services.scheduler`` 对外公开 API 与下沉前完全一致（28 个名字）。"""
    assert len(pkg.__all__) == 28, f"包 __all__ 应为 28 个名字，实际 {len(pkg.__all__)}"
    assert set(pkg.__all__) == set(_L1_PUBLIC) - {"STATE_STATUSES"}, (
        f"包 __all__ 与下沉前不一致：多 {sorted(set(pkg.__all__) - (set(_L1_PUBLIC) - {'STATE_STATUSES'}))}"
        f" / 少 {sorted((set(_L1_PUBLIC) - {'STATE_STATUSES'}) - set(pkg.__all__))}"
    )


def test_shim_all_covers_every_public_name():
    """core.py 垫片的 ``__all__`` 必须覆盖原文件全部公开名（29 个）。"""
    exported = set(getattr(shim, "__all__", []))
    missing = sorted(set(_L1_PUBLIC) - exported)
    assert not missing, f"core.py 垫片 __all__ 缺少：{missing}"


def test_shim_is_a_thin_reexport_only():
    """core.py 必须是纯垫片：不再定义任何类/函数实体。"""
    tree = ast.parse(inspect.getsource(shim))
    defined = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert not defined, f"core.py 垫片里仍有实体定义：{defined}"


# --------------------------------------------------------------------------
# 3. scheduler 单例的进程级同一性（下沉最易破处）
# --------------------------------------------------------------------------
def test_scheduler_singleton_is_the_same_object_everywhere():
    """三处拿到的 scheduler 必须是同一个对象。"""
    singleton = _l1("scheduler")
    assert shim.scheduler is singleton, "services.scheduler.core.scheduler 不是 L1 单例"
    assert pkg.scheduler is singleton, "services.scheduler.scheduler 不是 L1 单例"
    assert isinstance(singleton, _l1_scheduler_cls())


def test_scheduler_singleton_is_process_wide_shared():
    """单例在多次 import / 属性访问下保持同一（模块缓存未被绕过）。"""
    singleton = _l1("scheduler")
    assert importlib.import_module("find_yourself.scheduling").scheduler is singleton
    assert importlib.import_module("find_yourself.services.scheduler.core").scheduler is singleton
    assert importlib.import_module("find_yourself.services.scheduler").scheduler is singleton


# --------------------------------------------------------------------------
# 4. 常量值不变
# --------------------------------------------------------------------------
def test_channel_constants():
    assert _l1("CHANNEL_INTERNAL_AGENT") == "internal_agent"
    assert _l1("CHANNEL_EXTERNAL_AGENT") == "external_agent"
    assert _l1("CHANNEL_A2A") == "a2a"
    assert _l1("CHANNEL_MCP") == "mcp"
    assert _l1("CHANNEL_CLI") == "cli"
    assert _l1("CHANNEL_PLUGIN") == "plugin"


def test_task_state_constants():
    assert _l1("TASK_QUEUED") == "queued"
    assert _l1("TASK_DISPATCHED") == "dispatched"
    assert _l1("TASK_RUNNING") == "running"
    assert _l1("TASK_SUCCEEDED") == "succeeded"
    assert _l1("TASK_FAILED") == "failed"
    assert _l1("TASK_RECLAIMED") == "reclaimed"
    assert _l1("TASK_REJECTED") == "rejected"
    assert _l1("TASK_CANCELLED") == "cancelled"


def test_known_channels_and_terminal_states():
    known = _l1("KNOWN_CHANNELS")
    assert known == (
        "internal_agent", "external_agent", "a2a", "mcp", "cli", "plugin",
    )
    terminal = _l1("TERMINAL_STATES")
    assert isinstance(terminal, frozenset)
    assert terminal == frozenset({"succeeded", "failed", "reclaimed", "rejected", "cancelled"})


def test_state_statuses_is_queued_dispatched_running_plus_terminal():
    states = _l1("STATE_STATUSES")
    assert isinstance(states, frozenset)
    assert states == frozenset({"queued", "dispatched", "running"}) | _l1("TERMINAL_STATES")


def test_priority_bounds():
    assert _l1("PRIORITY_MIN") == 1
    assert _l1("PRIORITY_MAX") == 9
    assert _l1("DEFAULT_PRIORITY") == 5


# --------------------------------------------------------------------------
# 5. 异常继承关系不变
# --------------------------------------------------------------------------
def test_exception_hierarchy():
    base = _l1("SchedulerError")
    assert issubclass(base, Exception)
    for name in ("NoRouteError", "SchedulerOverloaded", "UnknownTaskError"):
        cls = _l1(name)
        assert issubclass(cls, base), f"{name} 必须继承 SchedulerError"
        assert cls.__module__ == "find_yourself.scheduling"


def test_no_route_error_message_and_fields():
    cls = _l1("NoRouteError")
    err = cls("a2a")
    assert err.channel == "a2a"
    assert err.capability == ""
    assert "no_route" in str(err)
    err2 = cls("mcp", "billing")
    assert err2.capability == "billing"
    assert "capability='billing'" in str(err2)


def test_scheduler_overloaded_and_unknown_task_are_scheduler_errors():
    assert issubclass(_l1("SchedulerOverloaded"), _l1("SchedulerError"))
    assert issubclass(_l1("UnknownTaskError"), _l1("SchedulerError"))


# --------------------------------------------------------------------------
# 6. 关键行为不因搬家而变
# --------------------------------------------------------------------------
def test_dispatch_success_path():
    UnifiedScheduler = _l1_scheduler_cls()
    DispatchRequest = _l1("DispatchRequest")
    sch = UnifiedScheduler()
    seen: list[tuple[str, str]] = []

    def executor(req):
        req.report("progress", {"pct": 50})
        return {"echo": req.payload.get("v")}

    sch.register_simple_worker("w1", "internal_agent", executor)
    unsub = sch.subscribe(lambda rec: seen.append((rec.status, rec.task_id)))
    try:
        record = sch.submit_and_wait(
            DispatchRequest(channel="internal_agent", payload={"v": 7}, timeout_seconds=5),
            timeout=5,
        )
    finally:
        unsub()
    assert record.status == "succeeded"
    assert record.result == {"echo": 7}
    assert record.worker_id == "w1"
    # 下行 report 回传进了 status_events 流水（progress 不改 record.status）
    kinds = [e["status"] for e in record.status_events]
    assert "progress" in kinds
    assert record.status == "succeeded"
    assert [s for s, _ in seen][-1] == "succeeded"
    assert unsub() is None  # 退订函数幂等


def test_dispatch_failure_path_records_error():
    UnifiedScheduler = _l1_scheduler_cls()
    DispatchRequest = _l1("DispatchRequest")
    sch = UnifiedScheduler()

    def boom(req):
        raise ValueError("kaboom")

    sch.register_simple_worker("w2", "mcp", boom)
    record = sch.submit_and_wait(
        DispatchRequest(channel="mcp", timeout_seconds=5), timeout=5
    )
    assert record.status == "failed"
    assert "ValueError: kaboom" in record.error


def test_dispatch_no_route_and_overloaded_are_honest_rejections():
    UnifiedScheduler = _l1_scheduler_cls()
    DispatchRequest = _l1("DispatchRequest")
    NoRouteError = _l1("NoRouteError")
    SchedulerError = _l1("SchedulerError")
    SchedulerOverloaded = _l1("SchedulerOverloaded")

    sch = UnifiedScheduler()
    with pytest.raises(NoRouteError):
        sch.submit(DispatchRequest(channel="a2a"))

    # 并发已满 + 队列已满 → 诚实拒绝
    gate = threading.Event()
    sch2 = UnifiedScheduler(max_concurrent=1, queue_limit=1)
    sch2.register_simple_worker(
        "blk", "cli", lambda req: gate.wait(5), max_parallel=1
    )
    r1 = sch2.submit(DispatchRequest(channel="cli", timeout_seconds=5))
    assert r1.status in ("dispatched", "running")
    r2 = sch2.submit(DispatchRequest(channel="cli", timeout_seconds=5))
    assert r2.status == "queued"
    with pytest.raises(SchedulerOverloaded):
        sch2.submit(DispatchRequest(channel="cli", timeout_seconds=5))
    assert issubclass(SchedulerOverloaded, SchedulerError)
    gate.set()
    sch2.wait(r1.task_id, timeout=5)
    sch2.cancel(r2.task_id)


def test_reclaim_stale_marks_reclaimed_and_frees_slot():
    UnifiedScheduler = _l1_scheduler_cls()
    DispatchRequest = _l1("DispatchRequest")
    clock = {"t": 1000.0}
    sch = UnifiedScheduler(max_concurrent=1, clock=lambda: clock["t"])
    release = threading.Event()
    sch.register_simple_worker(
        "slow", "internal_agent", lambda req: release.wait(5), max_parallel=1
    )
    record = sch.submit(DispatchRequest(channel="internal_agent", timeout_seconds=1))
    # 尚未过 deadline → 不回收
    assert sch.reclaim_stale() == []
    clock["t"] = 1002.0
    assert sch.reclaim_stale() == [record.task_id]
    assert record.status == "reclaimed"
    # 名额已释放：新任务可立即开跑
    nxt = sch.submit(DispatchRequest(channel="internal_agent", timeout_seconds=5))
    assert nxt.status in ("dispatched", "running")
    assert sch.update_status(record.task_id, "running") is False  # 终态拒收回传
    release.set()
    sch.wait(nxt.task_id, timeout=5)


def test_concurrency_limit_is_enforced():
    UnifiedScheduler = _l1_scheduler_cls()
    DispatchRequest = _l1("DispatchRequest")
    sch = UnifiedScheduler(max_concurrent=2)
    gate = threading.Event()
    sch.register_simple_worker(
        "pool", "cli", lambda req: gate.wait(5), max_parallel=5
    )
    running = [
        sch.submit(DispatchRequest(channel="cli", timeout_seconds=5)) for _ in range(2)
    ]
    # 两个名额都被占：状态可能是 dispatched 或已被执行线程推进到 running
    assert [r.status for r in running] == ["dispatched", "dispatched"] or [
        r.status for r in running
    ] == ["running", "dispatched"] or [r.status for r in running] == ["running", "running"], (
        f"两个任务都应已开跑，实际 {[r.status for r in running]}"
    )
    queued = sch.submit(DispatchRequest(channel="cli", timeout_seconds=5))
    assert queued.status == "queued"
    stats = sch.stats()
    assert stats["max_concurrent"] == 2
    assert stats["queued"] == 1
    gate.set()
    sch.wait(queued.task_id, timeout=5)


def test_priority_ordering_drains_highest_first():
    UnifiedScheduler = _l1_scheduler_cls()
    DispatchRequest = _l1("DispatchRequest")
    order: list[int] = []
    gate = threading.Event()
    sch = UnifiedScheduler(max_concurrent=1)
    sch.register_simple_worker(
        "prio",
        "plugin",
        lambda req: (order.append(req.priority), gate.wait(5)),
        max_parallel=1,
    )
    blocker = sch.submit(DispatchRequest(channel="plugin", timeout_seconds=5))
    low = sch.submit(DispatchRequest(channel="plugin", priority=9, timeout_seconds=5))
    high = sch.submit(DispatchRequest(channel="plugin", priority=1, timeout_seconds=5))
    mid = sch.submit(DispatchRequest(channel="plugin", priority=5, timeout_seconds=5))
    assert [low.status, high.status, mid.status] == ["queued"] * 3
    gate.set()
    sch.wait(low.task_id, timeout=5)
    sch.wait(mid.task_id, timeout=5)
    sch.wait(blocker.task_id, timeout=5)
    time.sleep(0.05)
    assert order[0] == _l1("DEFAULT_PRIORITY")  # 阻塞者先跑
    assert order[1:] == [1, 5, 9], f"出队顺序应按优先级，实际 {order[1:]}"


def test_route_picks_least_loaded_then_priority_bonus():
    UnifiedScheduler = _l1_scheduler_cls()
    WorkerSpec = _l1("WorkerSpec")
    DispatchRequest = _l1("DispatchRequest")
    sch = UnifiedScheduler()
    sch.register_worker(WorkerSpec(worker_id="a", channel="a2a",
                                    executor=lambda r: None, priority_bonus=0))
    sch.register_worker(WorkerSpec(worker_id="b", channel="a2a",
                                    executor=lambda r: None, priority_bonus=5))
    # 同为 0 负载 → priority_bonus 高者胜
    assert sch._route_locked(DispatchRequest(channel="a2a")).worker_id == "b"
    # b 变忙 → least-loaded 的 a 胜
    sch._worker_active["b"] = 1
    assert sch._route_locked(DispatchRequest(channel="a2a")).worker_id == "a"
    # capability 路由提示不匹配 → 无路由
    assert sch._route_locked(
        DispatchRequest(channel="a2a", requested_capability="nope")
    ) is None


def test_worker_registry_and_public_projection():
    UnifiedScheduler = _l1_scheduler_cls()
    WorkerSpec = _l1("WorkerSpec")
    sch = UnifiedScheduler()
    sch.register_worker(WorkerSpec(worker_id="x", channel="mcp",
                                    executor=lambda r: None, max_parallel=3,
                                    tags=("t1",), description="d"))
    assert sch.get_worker("x").max_parallel == 3
    listed = sch.workers()[0]
    assert listed["worker_id"] == "x"
    assert listed["tags"] == ["t1"]
    assert listed["active"] == 0
    assert sch.stats()["channels"] == ["mcp"]
    assert sch.unregister_worker("x") is True
    assert sch.unregister_worker("x") is False
    assert sch.get_worker("x") is None


def test_register_validation_rejects_bad_specs():
    UnifiedScheduler = _l1_scheduler_cls()
    WorkerSpec = _l1("WorkerSpec")
    SchedulerError = _l1("SchedulerError")
    sch = UnifiedScheduler()
    with pytest.raises(SchedulerError):
        sch.register_worker(WorkerSpec(worker_id="", channel="cli", executor=lambda r: None))
    with pytest.raises(SchedulerError):
        sch.register_worker(WorkerSpec(worker_id="y", channel="nope", executor=lambda r: None))
    with pytest.raises(SchedulerError):
        sch.register_worker(WorkerSpec(worker_id="y", channel="cli", executor=None))
    with pytest.raises(SchedulerError):
        sch.register_worker(WorkerSpec(worker_id="y", channel="cli",
                                       executor=lambda r: None, max_parallel=0))
    with pytest.raises(SchedulerError):
        UnifiedScheduler(max_concurrent=0)


def test_dispatch_request_validation():
    DispatchRequest = _l1("DispatchRequest")
    SchedulerError = _l1("SchedulerError")
    DispatchRequest(channel="cli").validate()
    with pytest.raises(SchedulerError):
        DispatchRequest(channel="bogus").validate()
    with pytest.raises(SchedulerError):
        DispatchRequest(channel="cli", priority=99).validate()
    with pytest.raises(SchedulerError):
        DispatchRequest(channel="cli", timeout_seconds=0).validate()
    req = DispatchRequest(channel="cli")
    assert req.priority == _l1("DEFAULT_PRIORITY")
    assert req.timeout_seconds == 30.0
    assert req.action == "run"


def test_task_record_public_projection_and_duplicate_task_id():
    UnifiedScheduler = _l1_scheduler_cls()
    DispatchRequest = _l1("DispatchRequest")
    SchedulerError = _l1("SchedulerError")
    sch = UnifiedScheduler()
    sch.register_simple_worker("dup", "cli", lambda r: None)
    req = DispatchRequest(channel="cli", task_id="fixed-1", timeout_seconds=5)
    record = sch.submit_and_wait(req, timeout=5)
    assert record.status == "succeeded"
    with pytest.raises(SchedulerError):
        sch.submit(DispatchRequest(channel="cli", task_id="fixed-1"))
    public = record.to_public()
    for key in ("task_id", "channel", "action", "priority", "status", "worker_id",
                "submitted_at", "started_at", "finished_at", "result", "error",
                "attempts", "meta", "status_events"):
        assert key in public, f"TaskRecord.to_public 缺字段 {key}"
    assert public["attempts"] == 1


def test_cancel_queued_and_running():
    UnifiedScheduler = _l1_scheduler_cls()
    DispatchRequest = _l1("DispatchRequest")
    sch = UnifiedScheduler(max_concurrent=1)
    gate = threading.Event()
    sch.register_simple_worker("c", "cli", lambda r: gate.wait(5))
    running = sch.submit(DispatchRequest(channel="cli", timeout_seconds=5))
    queued = sch.submit(DispatchRequest(channel="cli", timeout_seconds=5))
    assert sch.cancel(queued.task_id) is True
    assert queued.status == "cancelled"
    assert sch.cancel(queued.task_id) is False  # 终态拒收
    assert sch.cancel(running.task_id) is True
    gate.set()
    sch.wait(running.task_id, timeout=5)
    with pytest.raises(_l1("UnknownTaskError")):
        sch.wait("no-such-task")
    assert sch.get_task("no-such-task") is None
    assert sch.cancel("no-such-task") is False


def test_list_tasks_filter_and_wait_unknown():
    UnifiedScheduler = _l1_scheduler_cls()
    DispatchRequest = _l1("DispatchRequest")
    sch = UnifiedScheduler()
    sch.register_simple_worker("l", "cli", lambda r: "ok")
    sch.submit_and_wait(DispatchRequest(channel="cli", timeout_seconds=5), timeout=5)
    assert len(sch.list_tasks()) == 1
    assert len(sch.list_tasks(status="succeeded")) == 1
    assert sch.list_tasks(status="failed") == []
    assert sch.stats()["total_submitted"] == 1


# --------------------------------------------------------------------------
# 7. 数据结构形状
# --------------------------------------------------------------------------
def test_worker_spec_is_frozen_dataclass():
    WorkerSpec = _l1("WorkerSpec")
    spec = WorkerSpec(worker_id="w", channel="cli", executor=lambda r: None)
    with pytest.raises(Exception):
        spec.worker_id = "other"
    assert spec.max_parallel == 1
    assert spec.tags == ()
    assert spec.priority_bonus == 0


def test_dispatch_request_and_task_record_are_dataclasses():
    import dataclasses

    DispatchRequest = _l1("DispatchRequest")
    TaskRecord = _l1("TaskRecord")
    assert dataclasses.is_dataclass(DispatchRequest)
    assert dataclasses.is_dataclass(TaskRecord)
    fields = [f.name for f in dataclasses.fields(TaskRecord)]
    for key in ("task_id", "channel", "action", "priority", "status", "worker_id",
                "submitted_at", "started_at", "finished_at", "deadline", "result",
                "error", "attempts", "meta", "status_events"):
        assert key in fields, f"TaskRecord 缺字段 {key}"
    assert DispatchRequest.__module__ == "find_yourself.scheduling"
    assert TaskRecord.__module__ == "find_yourself.scheduling"
