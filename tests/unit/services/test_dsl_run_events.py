"""G2 实时推送后端：``services/dsl_run_events`` 服务层确定性测试。

规避 TestClient 的 ASGI/SSE 循环语义差异，直接从**运行中的事件循环**起一个
场景：后台线程跑 ``run_dsl``（同步导出事件），主循环订阅同一通道并收集 SSE 帧，
直到出现 ``run_finished`` 或超时。核心验证两点：

* 事件经 ``loop.call_soon_threadsafe`` 跨线程安全投递（后台线程绝不直接碰
  ``asyncio.Queue.put_nowait``）；
* 帧序列包含 ``run_started`` / 每节点 ``node_started``+``node_finished`` /
  ``run_finished``，且 ``run_finished`` 的 status=succeeded。
"""

from __future__ import annotations

import asyncio
import json
import threading

from find_yourself.services import dsl_run_events
from find_yourself.services.dsl_canvas import run_dsl

_DOC = {
    "version": "1",
    "nodes": [
        {"id": "src", "type": "input",
         "params": {"kind": "literal", "value": [{"name": "张三", "age": 34}]}},
        {"id": "tpl", "type": "transform", "verb": "template",
         "params": {"template": "你好，{name}！你今年 {age} 岁。"}},
        {"id": "out", "type": "output", "params": {"format": "text"}},
    ],
    "edges": [{"from": "src", "to": "tpl"}, {"from": "tpl", "to": "out"}],
}


def _event_name(frame: str):
    for line in frame.splitlines():
        if line.startswith("event: "):
            return line[len("event: "):]
    return None


def _data_json(frame: str) -> dict:
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    return {}


def test_channel_for_prefixes_run_id():
    assert dsl_run_events.channel_for("abc123") == "dsl-run:abc123"


def test_owner_registry_register_and_lookup():
    dsl_run_events.register_owner("dsl-owner-x", "owner-x")
    assert dsl_run_events.owner_of("dsl-owner-x") == "owner-x"
    assert dsl_run_events.owner_of("dsl-not-registered") is None


def test_service_layer_stream_emits_full_frame_sequence():
    rid = "dsl-stream-svc-1"

    async def scenario():
        # begin_stream_run 必须在运行中的 loop 内调用（异步端点语义）。
        cb = dsl_run_events.begin_stream_run(rid, "owner-1")
        worker = threading.Thread(
            target=lambda: run_dsl(_DOC, run_id=rid, on_event=cb))
        worker.start()
        frames: list[str] = []
        try:
            async with asyncio.timeout(15):
                async for frame in dsl_run_events.stream_run_events(
                        rid, 0, heartbeat_interval=5.0):
                    frames.append(frame)
                    if _event_name(frame) == "run_finished":
                        break
        finally:
            worker.join(timeout=5)
        return frames

    frames = asyncio.run(scenario())
    events = [e for e in (_event_name(f) for f in frames) if e]

    assert events[0] == "run_started", f"首帧应为 run_started，实际: {events}"
    assert events.count("node_started") == 3, f"node_started 应 3 次: {events}"
    assert events.count("node_finished") == 3, f"node_finished 应 3 次: {events}"
    assert events[-1] == "run_finished", f"末帧应为 run_finished: {events}"

    finished = _data_json([f for f in frames
                           if _event_name(f) == "run_finished"][0])
    assert finished["status"] == "succeeded"
    assert finished["run_id"] == rid
    assert finished["output"] == "你好，张三！你今年 34 岁。"

    # node_started 载荷逐字对齐契约。
    node_started = _data_json([f for f in frames
                               if _event_name(f) == "node_started"][0])
    assert set(node_started) == {"node_id", "node_type", "verb"}


def test_callback_exception_never_breaks_run():
    """观测侧回调抛异常绝不允许污染/中断一次运行（on_event 是叠加通道）。"""
    def boom(_event_type: str, _data: dict) -> None:
        raise RuntimeError("observer failure")

    result = run_dsl(_DOC, run_id="dsl-svc-boom", on_event=boom)
    assert result.status == "succeeded"
    assert result.output == "你好，张三！你今年 34 岁。"
