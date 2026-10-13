"""G2 实时推送：DSL run 事件的服务层桥接（L2）。

把**同步**执行器 :func:`~find_yourself.services.dsl_canvas.run_dsl` 从后台线程
导出的观测事件，经进程内事件总线（:data:`find_yourself.runtime.sse.bus`）扇出成
SSE 帧，供 ``GET /api/dsl-canvas/runs/{run_id}/events`` 订阅。

分层（重要）：HTTP 路由**不直连 runtime** —— 运行时的 SSE 总线只被本服务模块
封装，路由经本模块桥接。总线 ``publish`` 是同步方法（内部直接对
``asyncio.Queue`` 做 ``put_nowait``），从后台线程调用不安全，故回调里一律用
``loop.call_soon_threadsafe`` 把发布调度回事件循环线程。

另维护一张**进程内属主登记表**：202 后台运行期间 run 结果尚未落
:class:`~find_yourself.services.dsl_canvas.DslRunStore`，此时只能靠登记表做
属主校验（避免「订阅自己刚提交的 in-flight run」被 404）。
"""

from __future__ import annotations

import asyncio
import threading
from typing import AsyncIterator, Callable

from ..runtime.sse import bus

#: run_id -> 属主标识（``owner_id or service_id``）。进程内、加锁保护。
_OWNERS: dict[str, str] = {}
_OWNERS_LOCK = threading.Lock()


def channel_for(run_id: str) -> str:
    """该 run 在事件总线上的通道名（带前缀，避免与 task 通道撞名）。"""
    return f"dsl-run:{run_id}"


def register_owner(run_id: str, owner: str) -> None:
    with _OWNERS_LOCK:
        _OWNERS[run_id] = owner


def owner_of(run_id: str) -> str | None:
    """未登记返回 ``None``（区别于「登记的属主恰好是空串」）。"""
    with _OWNERS_LOCK:
        return _OWNERS.get(run_id)


def begin_stream_run(run_id: str, owner: str) -> Callable[[str, dict], None]:
    """登记属主并返回一个线程安全的 ``on_event`` 回调。

    **必须在运行中的事件循环内调用**（如 202 分支的 async 端点）：内部用
    ``asyncio.get_running_loop()`` 捕获循环，回调经
    ``loop.call_soon_threadsafe`` 跨线程投递到总线。
    """
    loop = asyncio.get_running_loop()
    register_owner(run_id, owner)
    channel = channel_for(run_id)

    def on_event(event_type: str, data: dict) -> None:
        loop.call_soon_threadsafe(bus.publish, channel, event_type, data)

    return on_event


async def stream_run_events(run_id: str, last_event_id: int = 0,
                            heartbeat_interval: float = 15.0) -> AsyncIterator[str]:
    """订阅该 run 的通道，产出 SSE 字符串帧。"""
    async for ev in bus.subscribe(
            channel_for(run_id), last_event_id=last_event_id,
            heartbeat_interval=heartbeat_interval):
        yield ev.to_sse()
