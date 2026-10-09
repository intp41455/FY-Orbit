"""A2A 入站派发单测（§八 C · 补齐包3）。

覆盖：``message/send`` 入站打通（经统一调度中心）、未配置时保持诚实
``-32001``、调度任务可经 ``tasks/get`` 轮询、路由层 ``a2a_upstream_url``
防御式读取语义。
"""

from __future__ import annotations

import pytest

from find_yourself.adapters.a2a import (
    ERR_AGENT_DRAINING,
    ERR_INVALID_PARAMS,
    ERR_UPSTREAM_NOT_CONFIGURED,
    A2ADispatcher,
    A2AInboundError,
    message_text,
)
from find_yourself.services.scheduler import (
    CHANNEL_A2A,
    DispatchRequest,
    UnifiedScheduler,
)


def _message(text: str = "帮我研究一下") -> dict:
    return {"role": "agent", "parts": [{"kind": "text", "text": text}]}


# --------------------------------------------------------------------------- #
# 消息文本提取
# --------------------------------------------------------------------------- #


def test_message_text_extracts_text_parts():
    msg = {"role": "agent", "parts": [
        {"kind": "text", "text": "第一段"},
        {"type": "text", "text": "第二段"},
        {"kind": "file", "name": "x.bin"},  # 非文本 part 忽略
    ]}
    assert message_text(msg) == "第一段\n第二段"
    assert message_text({"role": "agent", "parts": []}) == ""


# --------------------------------------------------------------------------- #
# 入站派发打通（§八 C）
# --------------------------------------------------------------------------- #


def test_message_send_dispatches_through_handler_and_returns_real_task():
    seen = {}

    def handler(params: dict) -> dict:
        seen["params"] = params
        return {"id": "task-123", "contextId": "ctx-1",
                "status": {"state": "completed"}}

    dispatcher = A2ADispatcher(upstream_configured=False, dispatch_handler=handler)
    result = dispatcher.dispatch({
        "jsonrpc": "2.0", "id": "req-1", "method": "message/send",
        "params": {"message": _message()},
    })
    assert "error" not in result
    assert result["result"]["id"] == "task-123"
    assert result["result"]["status"]["state"] == "completed"
    assert seen["params"]["message"]["parts"][0]["text"] == "帮我研究一下"


def test_message_send_handler_failure_maps_to_honest_32001():
    def handler(params: dict) -> dict:
        raise A2AInboundError("A2A 入站 worker 未注册")

    dispatcher = A2ADispatcher(upstream_configured=True, dispatch_handler=handler)
    result = dispatcher.dispatch({
        "jsonrpc": "2.0", "id": 2, "method": "message/send",
        "params": {"message": _message()},
    })
    assert result["error"]["code"] == ERR_UPSTREAM_NOT_CONFIGURED
    assert "worker 未注册" in result["error"]["message"]


def test_message_send_without_handler_still_honest_32001():
    dispatcher = A2ADispatcher(upstream_configured=False)
    result = dispatcher.dispatch({
        "jsonrpc": "2.0", "id": 3, "method": "message/send",
        "params": {"message": _message()},
    })
    assert result["error"]["code"] == ERR_UPSTREAM_NOT_CONFIGURED
    assert "refusing to fabricate" in result["error"]["message"]
    # 遗留语义保留：声明 upstream 但没装配 handler 仍不伪造
    legacy = A2ADispatcher(upstream_configured=True)
    result2 = legacy.dispatch({
        "jsonrpc": "2.0", "id": 4, "method": "message/send",
        "params": {"message": _message()},
    })
    assert result2["error"]["code"] == ERR_UPSTREAM_NOT_CONFIGURED


def test_message_send_validation_and_draining_unchanged():
    dispatcher = A2ADispatcher(upstream_configured=False)
    bad = dispatcher.dispatch({
        "jsonrpc": "2.0", "id": 5, "method": "message/send", "params": {}})
    assert bad["error"]["code"] == ERR_INVALID_PARAMS
    draining = A2ADispatcher(upstream_configured=False, draining=True,
                             dispatch_handler=lambda p: {"id": "x"})
    res = draining.dispatch({
        "jsonrpc": "2.0", "id": 6, "method": "message/send",
        "params": {"message": _message()}})
    assert res["error"]["code"] == ERR_AGENT_DRAINING


def test_inbound_task_pollable_via_tasks_get_through_scheduler():
    """入站任务走调度中心后，tasks/get 用同一 task_id 可查到状态。"""
    from find_yourself.api.routes.a2a import (
        A2A_INBOUND_CAPABILITY,
        A2A_INBOUND_WORKER_ID,
        build_inbound_handler,
    )

    sch = UnifiedScheduler()
    sch.register_simple_worker(
        A2A_INBOUND_WORKER_ID, CHANNEL_A2A,
        lambda req: {"success": True, "artifact_ids": ["a1"], "error": None},
        tags=(A2A_INBOUND_CAPABILITY,))
    handler = build_inbound_handler(scheduler=sch)
    task = handler({"message": _message("统计一下")})
    assert task["status"]["state"] == "completed"
    record = sch.get_task(task["id"])
    assert record is not None and record.status == "succeeded"
    assert record.action == "a2a.message/send"
    assert record.channel == CHANNEL_A2A


def test_inbound_handler_without_worker_raises_honest_inbound_error():
    from find_yourself.api.routes.a2a import build_inbound_handler

    handler = build_inbound_handler(scheduler=UnifiedScheduler())
    with pytest.raises(A2AInboundError):
        handler({"message": _message()})


def test_inbound_handler_rejects_empty_text_message():
    from find_yourself.api.routes.a2a import build_inbound_handler

    handler = build_inbound_handler(scheduler=UnifiedScheduler())
    with pytest.raises(A2AInboundError):
        handler({"message": {"role": "agent", "parts": [{"kind": "file"}]}})


def test_inbound_executor_failure_maps_to_failed_state():
    from find_yourself.api.routes.a2a import (
        A2A_INBOUND_CAPABILITY,
        A2A_INBOUND_WORKER_ID,
        build_inbound_handler,
    )

    sch = UnifiedScheduler()

    def boom(req: DispatchRequest) -> dict:
        raise RuntimeError("agent exploded")

    sch.register_simple_worker(A2A_INBOUND_WORKER_ID, CHANNEL_A2A, boom,
                               tags=(A2A_INBOUND_CAPABILITY,))
    handler = build_inbound_handler(scheduler=sch)
    task = handler({"message": _message()})
    assert task["status"]["state"] == "failed"
    assert "agent exploded" in task["status"]["message"]["parts"][0]["text"]


def test_route_defensive_a2a_upstream_url_read():
    """路由语义：getattr 防御式读取；未配置 → 无 handler（保持 -32001 语义）。"""
    from find_yourself.api.routes.a2a import build_inbound_handler

    class SettingsWithoutField:
        pass

    upstream = getattr(SettingsWithoutField(), "a2a_upstream_url", None)
    assert upstream is None  # 字段不存在也不炸（包1 未合并时安全）

    class SettingsWithField:
        a2a_upstream_url = "https://upstream.example.com"

    upstream2 = getattr(SettingsWithField(), "a2a_upstream_url", None)
    assert upstream2  # 配置了才装配入站派发 handler
    assert callable(build_inbound_handler())
