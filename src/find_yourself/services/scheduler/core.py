"""统一调度中心（G1 护城河 · 补齐包3 / A-统一接入-08）。

此前进程内派发散落四处、各自为政：``services/agent_dispatch.py``（子 Agent
工具派发）、``runtime/delegation.py``（层级委派）、``services/agent_teams.py``
（团队成员执行）、``services/canvas.py``（画布子任务派发）。本模块把它们收敛到
**一个**调度中心：外部成品 agent 与内部 agent **同池**注册、统一路由、统一
优先级、统一并发上限、统一回收、统一实时状态回传。

核心概念
--------

* :class:`WorkerSpec` —— 一个可被调度的执行单元（内部 agent、外部成品 agent、
  MCP 工具通道、CLI、A2A 入站处理器……）。所有 worker 进同一个注册表（"同池"），
  用 ``channel`` 区分通路、用 ``tags`` 承载能力路由提示。
* :class:`DispatchRequest` —— 一次派发请求：路由（channel + capability）+ 优先级
  （1 最高 ~ 9 最低，默认 5）+ 超时 + 负载。``report`` 回调是调度中心→执行单元的
  下行通道；:meth:`UnifiedScheduler.update_status` 是执行单元→调度中心的上行
  回传，双向状态回传由此构成。
* :class:`TaskRecord` —— 派发生命周期的唯一事实：状态机
  ``queued → dispatched → running → succeeded | failed | reclaimed``（入队溢出或
  无路由时 ``rejected``），全量状态事件流水 ``status_events`` 留档。

诚实边界（不冒充的能力）
------------------------

* Python 无法杀死线程：:meth:`UnifiedScheduler.reclaim_stale` 把超时任务标记为
  ``reclaimed`` 并释放并发名额、拒收该任务的后续回传与迟到结果，但执行线程若
  真的卡死在不可中断调用里，其线程资源仍由解释器持有——这里是标记回收而非强杀。
* 本实现是**进程内**的：重启后任务记录即丢失（与 ``runtime/agent_bus.py`` 同一
  诚实边界）；落库/落盘由调用方决定，本中心不假装持久化。
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# 通道（同池调度的通路维度）
# --------------------------------------------------------------------------- #

CHANNEL_INTERNAL_AGENT = "internal_agent"
CHANNEL_EXTERNAL_AGENT = "external_agent"
CHANNEL_A2A = "a2a"
CHANNEL_MCP = "mcp"
CHANNEL_CLI = "cli"
CHANNEL_PLUGIN = "plugin"

#: 通道白名单——路由与注册校验共用这一份。新通路先在此登记。
KNOWN_CHANNELS: tuple[str, ...] = (
    CHANNEL_INTERNAL_AGENT,
    CHANNEL_EXTERNAL_AGENT,
    CHANNEL_A2A,
    CHANNEL_MCP,
    CHANNEL_CLI,
    CHANNEL_PLUGIN,
)

# --------------------------------------------------------------------------- #
# 任务状态机
# --------------------------------------------------------------------------- #

TASK_QUEUED = "queued"
TASK_DISPATCHED = "dispatched"
TASK_RUNNING = "running"
TASK_SUCCEEDED = "succeeded"
TASK_FAILED = "failed"
TASK_RECLAIMED = "reclaimed"
TASK_REJECTED = "rejected"
TASK_CANCELLED = "cancelled"

TERMINAL_STATES: frozenset[str] = frozenset(
    {TASK_SUCCEEDED, TASK_FAILED, TASK_RECLAIMED, TASK_REJECTED, TASK_CANCELLED}
)

#: 状态机合法状态 = 排队/执行中三态 + 终态。状态机字段只在这中间迁移；
#: 执行单元上报的自定义进度 kind（如 "progress"）只进 status_events 流水，
#: 不改动 record.status，否则会破坏回收竞态守卫与终态判定。
STATE_STATUSES: frozenset[str] = frozenset(
    {TASK_QUEUED, TASK_DISPATCHED, TASK_RUNNING}
) | TERMINAL_STATES

PRIORITY_MIN = 1  # 最高优先级
PRIORITY_MAX = 9  # 最低优先级
DEFAULT_PRIORITY = 5


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #


class SchedulerError(Exception):
    """调度中心基类异常。"""


class NoRouteError(SchedulerError):
    """没有任何 worker 能承接该请求（channel 无注册者 / capability 不匹配）。"""

    def __init__(self, channel: str, capability: str = ""):
        self.channel = channel
        self.capability = capability
        super().__init__(
            f"no_route: channel='{channel}'"
            + (f" capability='{capability}'" if capability else "")
            + " 没有已注册的 worker"
        )


class SchedulerOverloaded(SchedulerError):
    """并发已满且优先级队列也满——诚实拒绝，不静默降级。"""


class UnknownTaskError(SchedulerError):
    """task_id 不存在。"""


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WorkerSpec:
    """一个被调度执行单元的登记项。内部与外部 agent 同池注册。"""

    worker_id: str
    channel: str
    executor: Callable[["DispatchRequest"], Any]
    max_parallel: int = 1
    #: 同通道多 worker 竞争时（负载相同时）的选择偏置，越大越优先。
    priority_bonus: int = 0
    #: 能力路由提示：DispatchRequest.requested_capability 会与 tags / worker_id 匹配。
    tags: tuple[str, ...] = ()
    description: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "channel": self.channel,
            "max_parallel": self.max_parallel,
            "priority_bonus": self.priority_bonus,
            "tags": list(self.tags),
            "description": self.description,
        }


@dataclass
class DispatchRequest:
    """一次派发请求。

    ``report(kind, data)`` 是调度中心下发给执行单元的回传函数（双向状态的下行
    半边）；执行单元 mid-flight 调它即可把进度写进任务的状态事件流水。
    """

    channel: str
    action: str = "run"
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = DEFAULT_PRIORITY
    timeout_seconds: float = 30.0
    requested_capability: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    report: Callable[[str, dict[str, Any]], None] | None = None

    def validate(self) -> None:
        if self.channel not in KNOWN_CHANNELS:
            raise SchedulerError(
                f"unknown channel '{self.channel}'；KNOWN_CHANNELS: {', '.join(KNOWN_CHANNELS)}"
            )
        if not isinstance(self.priority, int) or not (PRIORITY_MIN <= self.priority <= PRIORITY_MAX):
            raise SchedulerError(
                f"priority 必须是 {PRIORITY_MIN}（最高）~ {PRIORITY_MAX}（最低）的整数，"
                f"收到 {self.priority!r}"
            )
        if self.timeout_seconds <= 0:
            raise SchedulerError("timeout_seconds 必须为正数")


@dataclass
class TaskRecord:
    """一次派发的完整生命周期记录（唯一事实源）。"""

    task_id: str
    channel: str
    action: str
    priority: int
    status: str = TASK_QUEUED
    worker_id: str | None = None
    submitted_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    deadline: float | None = None  # clock 单调秒，回收判据
    result: Any = None
    error: str = ""
    attempts: int = 0
    #: 提交时的请求元数据快照（观测用；与 req.meta 解耦，防排队后变更）。
    meta: dict[str, Any] = field(default_factory=dict)
    status_events: list[dict[str, Any]] = field(default_factory=list)
    #: 内部位：并发名额是否已释放（reclaim / cancel / 正常退出三方只释放一次）。
    _slot_released: bool = field(default=False, repr=False, compare=False)

    def to_public(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "channel": self.channel,
            "action": self.action,
            "priority": self.priority,
            "status": self.status,
            "worker_id": self.worker_id,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "attempts": self.attempts,
            "meta": dict(self.meta),
            "status_events": list(self.status_events),
        }


# --------------------------------------------------------------------------- #
# 调度中心
# --------------------------------------------------------------------------- #


class UnifiedScheduler:
    """统一路由 + 优先级队列 + 并发上限 + 回收 + 双向状态回传的调度中心。

    线程模型：每个实际执行的请求占一个工作线程（执行单元跑在
    :class:`WorkerSpec.executor` 里）；``submit`` 立即返回
    :class:`TaskRecord`，同步调用方用 :meth:`submit_and_wait` / :meth:`wait`。
    所有内部状态由一把可重入锁保护；状态监听器在持锁线程内同步调用
    （RLock 保证监听器再入调度中心 API 安全），监听器必须快速返回。
    """

    def __init__(
        self,
        *,
        max_concurrent: int = 8,
        queue_limit: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_concurrent < 1:
            raise SchedulerError("max_concurrent 至少为 1")
        self.max_concurrent = max_concurrent
        self.queue_limit = queue_limit
        self._clock = clock
        self._lock = threading.RLock()
        self._workers: dict[str, WorkerSpec] = {}
        self._tasks: dict[str, TaskRecord] = {}
        self._dones: dict[str, threading.Event] = {}
        self._worker_active: dict[str, int] = {}
        self._queue: list[tuple[int, int, str]] = []  # (priority, seq, task_id)
        self._pending_reqs: dict[str, DispatchRequest] = {}  # 排队中的原始请求
        self._seq = itertools.count(1)
        self._listeners: list[Callable[[TaskRecord], None]] = []

    # -- worker 注册表（同池） ----------------------------------------------- #

    def register_worker(self, spec: WorkerSpec) -> WorkerSpec:
        if not isinstance(spec.worker_id, str) or not spec.worker_id.strip():
            raise SchedulerError("worker_id 必须是非空字符串")
        if spec.channel not in KNOWN_CHANNELS:
            raise SchedulerError(
                f"unknown channel '{spec.channel}'；KNOWN_CHANNELS: {', '.join(KNOWN_CHANNELS)}"
            )
        if not callable(spec.executor):
            raise SchedulerError(f"worker '{spec.worker_id}' 的 executor 必须可调用")
        if spec.max_parallel < 1:
            raise SchedulerError(f"worker '{spec.worker_id}' 的 max_parallel 至少为 1")
        with self._lock:
            self._workers[spec.worker_id] = spec
            self._worker_active.setdefault(spec.worker_id, 0)
        return spec

    def unregister_worker(self, worker_id: str) -> bool:
        with self._lock:
            return self._workers.pop(worker_id, None) is not None

    def workers(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for w in self._workers.values():
                d = w.to_public()
                d["active"] = self._worker_active.get(w.worker_id, 0)
                out.append(d)
            return out

    def get_worker(self, worker_id: str) -> WorkerSpec | None:
        with self._lock:
            return self._workers.get(worker_id)

    # -- 路由 ---------------------------------------------------------------- #

    def _route_locked(self, req: DispatchRequest) -> WorkerSpec | None:
        """channel + capability -> 具体 worker。

        选择规则（确定性）：先按活跃数升序（least-loaded），再按
        priority_bonus 降序，最后按 worker_id 字典序稳定排序。
        """
        cap = (req.requested_capability or "").strip()
        candidates = [
            w
            for w in self._workers.values()
            if w.channel == req.channel
            and (not cap or cap in w.tags or cap == w.worker_id)
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda w: (
                self._worker_active.get(w.worker_id, 0),
                -w.priority_bonus,
                w.worker_id,
            )
        )
        return candidates[0]

    # -- 提交 ---------------------------------------------------------------- #

    def submit(self, req: DispatchRequest) -> TaskRecord:
        """提交一次派发；无路由/过载时记录 ``rejected`` 并抛异常（诚实拒绝）。"""
        req.validate()
        with self._lock:
            task_id = req.task_id or f"sch-{uuid.uuid4().hex[:12]}"
            if task_id in self._tasks:
                raise SchedulerError(f"task_id '{task_id}' 已存在，不可重复提交")
            record = TaskRecord(
                task_id=task_id,
                channel=req.channel,
                action=req.action,
                priority=req.priority,
                meta=dict(req.meta or {}),
            )
            self._tasks[task_id] = record
            self._dones[task_id] = threading.Event()

            worker = self._route_locked(req)
            if worker is None:
                self._emit_locked(record, TASK_REJECTED, detail="no_route")
                record.finished_at = _now_iso()
                self._dones[task_id].set()
                raise NoRouteError(req.channel, req.requested_capability)

            global_free = len(self._running_locked()) < self.max_concurrent
            worker_free = self._worker_active.get(worker.worker_id, 0) < worker.max_parallel
            if global_free and worker_free:
                self._start_locked(record, worker, req)
                return record
            if len(self._queue) >= self.queue_limit:
                self._emit_locked(
                    record, TASK_REJECTED, detail="queue_full", error="调度队列已满，诚实拒绝"
                )
                record.finished_at = _now_iso()
                self._dones[task_id].set()
                raise SchedulerOverloaded(
                    f"并发已满（{self.max_concurrent}）且队列已满（{self.queue_limit}）"
                )
            self._pending_reqs[task_id] = req
            heapq.heappush(self._queue, (req.priority, next(self._seq), task_id))
            self._emit_locked(record, TASK_QUEUED, detail=f"queued behind {len(self._queue)-1} task(s)")
            return record

    def _running_locked(self) -> list[TaskRecord]:
        return [t for t in self._tasks.values() if t.status in (TASK_DISPATCHED, TASK_RUNNING)]

    def _start_locked(self, record: TaskRecord, worker: WorkerSpec, req: DispatchRequest) -> None:
        record.status = TASK_DISPATCHED
        record.worker_id = worker.worker_id
        record.attempts = 1
        record.deadline = self._clock() + req.timeout_seconds
        req.task_id = record.task_id

        def report(kind: str, data: dict[str, Any] | None = None) -> bool:
            return self.update_status(record.task_id, kind, data=data or {})

        req.report = report

        def _run() -> None:
            began = False
            try:
                with self._lock:
                    # 回收竞态：线程还没跑到就被标记 reclaimed，则不再执行。
                    if record.status != TASK_DISPATCHED:
                        return
                    record.status = TASK_RUNNING
                    record.started_at = _now_iso()
                    self._emit_locked(record, TASK_RUNNING)
                    began = True
                output = worker.executor(req)
                with self._lock:
                    if record.status != TASK_RUNNING:
                        return  # 已被回收/取消：迟到结果被丢弃（诚实边界）
                    record.result = output
                    self._finish_locked(record, TASK_SUCCEEDED)
            except Exception as exc:  # noqa: BLE001 — 执行单元失败即失败
                with self._lock:
                    if record.status != TASK_RUNNING:
                        return
                    record.error = f"{type(exc).__name__}: {exc}"
                    self._finish_locked(record, TASK_FAILED)
            finally:
                with self._lock:
                    self._release_slot_locked(record)
                    self._dones[record.task_id].set()
                self._pump()

        thread = threading.Thread(
            target=_run, name=f"fy-scheduler-{record.task_id}", daemon=True
        )
        self._worker_active[worker.worker_id] = self._worker_active.get(worker.worker_id, 0) + 1
        self._emit_locked(record, TASK_DISPATCHED, detail=worker.worker_id)
        thread.start()

    def _release_slot_locked(self, record: TaskRecord) -> None:
        """并发名额只释放一次（reclaim / cancel / 正常退出三方共用）。"""
        if record._slot_released:
            return
        record._slot_released = True
        worker_id = record.worker_id
        if worker_id:
            self._worker_active[worker_id] = max(
                0, self._worker_active.get(worker_id, 0) - 1
            )

    def _finish_locked(self, record: TaskRecord, status: str) -> None:
        record.status = status
        record.finished_at = _now_iso()
        self._emit_locked(record, status)

    def _emit_locked(self, record: TaskRecord, status: str, *, detail: str = "",
                     error: str = "", data: dict[str, Any] | None = None) -> None:
        # 只有状态机合法状态才迁移 record.status；自定义进度 kind 仅入流水。
        if status in STATE_STATUSES and status != record.status:
            record.status = status
        if error:
            record.error = error
        event: dict[str, Any] = {"at": _now_iso(), "status": status, "detail": detail}
        if data:
            event["data"] = dict(data)
        record.status_events.append(event)
        for fn in list(self._listeners):
            try:
                fn(record)
            except Exception:  # noqa: BLE001 — 监听器异常不反噬调度主流程
                pass

    # -- 队列回流 ------------------------------------------------------------ #

    def _pump(self) -> None:
        """有并发空位时按优先级出队（同优先级按提交顺序 FIFO）。"""
        with self._lock:
            while self._queue:
                if len(self._running_locked()) >= self.max_concurrent:
                    return
                priority, _seq, task_id = self._queue[0]
                record = self._tasks.get(task_id)
                req = self._pending_reqs.get(task_id)
                if record is None or req is None or record.status != TASK_QUEUED:
                    heapq.heappop(self._queue)
                    self._pending_reqs.pop(task_id, None)
                    continue
                # 重新路由（worker 可能已被注销）。
                worker = self._route_locked(req)
                if worker is None:
                    heapq.heappop(self._queue)
                    self._pending_reqs.pop(task_id, None)
                    self._emit_locked(record, TASK_REJECTED, detail="no_route_after_queue")
                    record.finished_at = _now_iso()
                    self._dones[task_id].set()
                    continue
                heapq.heappop(self._queue)
                self._pending_reqs.pop(task_id, None)
                self._start_locked(record, worker, req)

    # -- 等待 ---------------------------------------------------------------- #

    def wait(self, task_id: str, *, timeout: float | None = None) -> TaskRecord:
        with self._lock:
            record = self._tasks.get(task_id)
            done = self._dones.get(task_id)
            if record is None or done is None:
                raise UnknownTaskError(f"task '{task_id}' 不存在")
        done.wait(timeout)
        return record

    def submit_and_wait(self, req: DispatchRequest, *, timeout: float | None = None) -> TaskRecord:
        record = self.submit(req)
        return self.wait(record.task_id, timeout=timeout)

    # -- 双向状态回传（上行半边） --------------------------------------------- #

    def update_status(
        self, task_id: str, status: str, *, data: dict[str, Any] | None = None
    ) -> bool:
        """执行单元 mid-flight 回传进度；终态任务拒收（迟到回传被丢弃）。"""
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or record.status in TERMINAL_STATES:
                return False
            self._emit_locked(record, status, data=data)
            return True

    def subscribe(self, listener: Callable[[TaskRecord], None]) -> Callable[[], None]:
        """注册状态监听器（实时回传的对外出口）；返回退订函数。"""
        with self._lock:
            self._listeners.append(listener)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass

        return _unsubscribe

    # -- 回收（reclaim） ------------------------------------------------------ #

    def reclaim_stale(self) -> list[str]:
        """把超过 deadline 仍在跑的任务标记 ``reclaimed`` 并释放并发名额。

        返回被回收的 task_id 列表。被回收任务的后续回传/迟到结果一律拒收；
        若执行线程随后自行退出，其结果按迟到丢弃处理。
        """
        reclaimed: list[str] = []
        with self._lock:
            now = self._clock()
            for record in list(self._tasks.values()):
                if record.status not in (TASK_DISPATCHED, TASK_RUNNING):
                    continue
                if record.deadline is not None and now > record.deadline:
                    self._emit_locked(
                        record, TASK_RECLAIMED,
                        detail="deadline_exceeded; late results are discarded",
                    )
                    record.finished_at = _now_iso()
                    self._release_slot_locked(record)
                    self._dones[record.task_id].set()
                    reclaimed.append(record.task_id)
            if reclaimed:
                self._pump()
        return reclaimed

    def cancel(self, task_id: str) -> bool:
        """取消任务：排队中直接取消；运行中标记取消并回收（合作式，不强杀线程）。"""
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or record.status in TERMINAL_STATES:
                return False
            if record.status == TASK_QUEUED:
                self._queue = [e for e in self._queue if e[2] != task_id]
                heapq.heapify(self._queue)
                self._pending_reqs.pop(task_id, None)
                self._emit_locked(record, TASK_CANCELLED, detail="cancelled while queued")
                record.finished_at = _now_iso()
                self._dones[task_id].set()
                return True
            self._emit_locked(record, TASK_CANCELLED, detail="cancelled while running")
            record.finished_at = _now_iso()
            self._release_slot_locked(record)
            self._dones[task_id].set()
            self._pump()
            return True

    # -- 查询 ---------------------------------------------------------------- #

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, *, status: str | None = None) -> list[TaskRecord]:
        with self._lock:
            tasks = list(self._tasks.values())
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "workers": len(self._workers),
                "running": len(self._running_locked()),
                "queued": len(self._queue),
                "max_concurrent": self.max_concurrent,
                "queue_limit": self.queue_limit,
                "total_submitted": len(self._tasks),
                "channels": sorted({w.channel for w in self._workers.values()}),
            }

    # -- 便捷构造 ------------------------------------------------------------ #

    def register_simple_worker(
        self,
        worker_id: str,
        channel: str,
        executor: Callable[[DispatchRequest], Any],
        *,
        max_parallel: int = 1,
        tags: tuple[str, ...] = (),
        priority_bonus: int = 0,
        description: str = "",
    ) -> WorkerSpec:
        return self.register_worker(
            WorkerSpec(
                worker_id=worker_id,
                channel=channel,
                executor=executor,
                max_parallel=max_parallel,
                tags=tuple(tags),
                priority_bonus=priority_bonus,
                description=description,
            )
        )


#: 进程级共享单例：各派发点（agent_dispatch / delegation / A2A 入站……）统一
#: 经它派发；测试可用独立 :class:`UnifiedScheduler` 实例隔离。
scheduler = UnifiedScheduler()
