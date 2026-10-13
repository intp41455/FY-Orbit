"""统一调度中心（G1 护城河 · 补齐包3 / A-统一接入-08）—— 兼容垫片。

调度内核实体已下沉至 L1 契约单源 :mod:`find_yourself.scheduling`（ADR-0001 D3/D6），
此处仅为向后兼容的 re-export 垫片，供既有调用方继续
``from .core import ...`` / ``from ..services.scheduler.core import ...``。
``runtime``（L2.0）此前正是因此产生 ``runtime → services`` 的 D3R 禁止方向。
新代码请直接 ``from ..scheduling import ...``。
"""

from ...scheduling import (
    CHANNEL_A2A,
    CHANNEL_CLI,
    CHANNEL_EXTERNAL_AGENT,
    CHANNEL_INTERNAL_AGENT,
    CHANNEL_MCP,
    CHANNEL_PLUGIN,
    DEFAULT_PRIORITY,
    KNOWN_CHANNELS,
    PRIORITY_MAX,
    PRIORITY_MIN,
    STATE_STATUSES,
    TASK_CANCELLED,
    TASK_DISPATCHED,
    TASK_FAILED,
    TASK_QUEUED,
    TASK_RECLAIMED,
    TASK_REJECTED,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    TERMINAL_STATES,
    DispatchRequest,
    NoRouteError,
    SchedulerError,
    SchedulerOverloaded,
    TaskRecord,
    UnifiedScheduler,
    UnknownTaskError,
    WorkerSpec,
    scheduler,
)

__all__ = [
    "CHANNEL_A2A",
    "CHANNEL_CLI",
    "CHANNEL_EXTERNAL_AGENT",
    "CHANNEL_INTERNAL_AGENT",
    "CHANNEL_MCP",
    "CHANNEL_PLUGIN",
    "DEFAULT_PRIORITY",
    "KNOWN_CHANNELS",
    "PRIORITY_MAX",
    "PRIORITY_MIN",
    "STATE_STATUSES",
    "TASK_CANCELLED",
    "TASK_DISPATCHED",
    "TASK_FAILED",
    "TASK_QUEUED",
    "TASK_RECLAIMED",
    "TASK_REJECTED",
    "TASK_RUNNING",
    "TASK_SUCCEEDED",
    "TERMINAL_STATES",
    "DispatchRequest",
    "NoRouteError",
    "SchedulerError",
    "SchedulerOverloaded",
    "TaskRecord",
    "UnifiedScheduler",
    "UnknownTaskError",
    "WorkerSpec",
    "scheduler",
]