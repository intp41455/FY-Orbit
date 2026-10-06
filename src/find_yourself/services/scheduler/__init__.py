"""统一调度中心（services.scheduler）。

公开 API（A-统一接入-08 统一调度中心 + 补齐包3 接线约定）：

* :class:`UnifiedScheduler` —— 统一路由 / 优先级 / 并发上限 / 回收 / 双向状态回传。
* :class:`WorkerSpec` / :class:`DispatchRequest` / :class:`TaskRecord` —— 调度三件套。
* :data:`scheduler` —— 进程级共享单例（agent_dispatch / delegation / A2A 入站
  统一经它派发；外部成品 agent 与内部 agent 同池注册）。
* 通道常量 ``CHANNEL_*`` 与任务状态常量 ``TASK_*``。
"""

from __future__ import annotations

from .core import (
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
