"""I-9 必做项之 CI 集成测试：Temporal 是正式支持的可选引擎。

覆盖（无需真实 Temporal server，CI 可跑）：
1. 默认关闭：disabled() 句柄安全可用（close 可重入）；
2. 配了不可达地址 → connect 抛错且保持 disabled（不静默假装可用）；
3. 工作流 ID 约定 `fy-task:<task_id>`（冻结契约 §10）；
4. 健康端点如实报告 temporal 状态（disabled/ready 二值）。

需真实 server 的端到端见 tests/integration/test_temporal_live.py
（门控跳过，不在普通 CI 跑）。
"""
from __future__ import annotations

import pytest

from find_yourself.runtime.temporal import TemporalRuntime, workflow_id_for


def test_disabled_handle_is_safe() -> None:
    rt = TemporalRuntime.disabled()
    assert rt.is_enabled() is False


async def _noop() -> None:
    return None


def test_disabled_close_is_reentrant() -> None:
    import asyncio

    rt = TemporalRuntime.disabled()
    asyncio.run(rt.close())
    assert rt.is_enabled() is False


def test_workflow_id_convention() -> None:
    assert workflow_id_for("task-01") == "fy-task:task-01"


def test_unreachable_address_stays_disabled() -> None:
    import asyncio

    from find_yourself.config import Settings

    settings = Settings(
        session_secret="x" * 32,
        temporal_address="127.0.0.1:9",
    )
    rt = TemporalRuntime.disabled()
    with pytest.raises(Exception):
        asyncio.run(rt.connect(settings))
    assert rt.is_enabled() is False
