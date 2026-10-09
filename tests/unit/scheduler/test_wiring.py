"""接线证据单测（A-统一接入-08 · 补齐包3）。

证明：``services/agent_dispatch.AgentDispatchService.dispatch`` 与
``runtime.delegation.DelegationCoordinator.dispatch`` 的子任务执行**真实经过
统一调度中心**（独立 scheduler 实例注入，任务记录可查、同池 worker 注册）。
"""

from __future__ import annotations

import pytest

from find_yourself.runtime.delegation import DelegationCoordinator
from find_yourself.services.scheduler import (
    CHANNEL_INTERNAL_AGENT,
    TASK_SUCCEEDED,
    UnifiedScheduler,
)
from find_yourself.services.tool_registry import ToolRegistryService


@pytest.fixture()
def registry() -> ToolRegistryService:
    reg = ToolRegistryService()
    reg.register(
        name="echo",
        description="回显（内置确定性执行器）",
        parameters={"type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"]},
        entry={"type": "builtin", "executor": "echo"},
    )
    return reg


def test_agent_dispatch_runs_through_scheduler(registry, tmp_path):
    from find_yourself.services.agent_dispatch import AgentDispatchService

    sch = UnifiedScheduler()
    svc = AgentDispatchService(archive_dir=tmp_path, registry=registry, scheduler=sch)
    parent = svc.dispatch(
        {"capability": "回显", "payload": {"message": "你好"},
         "acceptance": {"type": "output_contains", "contains": ["你好"]},
         "env_contract": {}},
        [{"name": "echo", "arguments": {"message": "你好"}}],
    )
    assert parent["status"] == "verified"
    # 证据 1：调度中心留有任务记录且成功
    record = sch.get_task(parent["scheduler_task_id"])
    assert record is not None and record.status == TASK_SUCCEEDED
    assert record.action == "run_child"
    # 证据 2：worker 已注册进同池注册表（内部 agent 通道）
    worker = sch.get_worker("internal.dispatch-child")
    assert worker is not None and worker.channel == CHANNEL_INTERNAL_AGENT
    assert "dispatch" in worker.tags
    # 证据 3：状态回传流水完整（queued→dispatched→running→succeeded 或
    # dispatched→running→succeeded 取决于并发是否空闲）
    statuses = [e["status"] for e in record.status_events]
    assert statuses[0] in ("queued", "dispatched")
    assert statuses[-1] == "succeeded"


def test_delegation_runs_through_scheduler():
    from find_yourself.services.scheduler import TASK_SUCCEEDED as _OK  # noqa: F401

    sch = UnifiedScheduler()
    coord = DelegationCoordinator(max_depth=3, max_concurrency=2, max_retries=2,
                                  root_budget_usd=0.02, scheduler=sch)
    res = coord.dispatch(parent_task_id="root-1", subtask_id="sub-1",
                         current_depth=0, runner_fn=lambda: {"done": True},
                         cost_usd=0.005)
    assert res["status"] == "completed"
    worker = sch.get_worker("internal.delegation-subtask")
    assert worker is not None and worker.channel == CHANNEL_INTERNAL_AGENT
    assert "delegation" in worker.tags
    records = [r for r in sch.list_tasks() if r.action == "delegation_subtask"]
    assert records and records[-1].status == TASK_SUCCEEDED
    assert records[-1].result == {"done": True}
    assert records[-1].meta["parent_task_id"] == "root-1"


def test_delegation_scheduler_failure_counts_as_attempt_and_retries():
    sch = UnifiedScheduler()
    coord = DelegationCoordinator(max_depth=3, max_concurrency=2, max_retries=2,
                                  root_budget_usd=0.02, scheduler=sch)
    calls = {"n": 0}

    def flaky() -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return {"done": True}

    res = coord.dispatch(parent_task_id="root-2", subtask_id="sub-2",
                         current_depth=0, runner_fn=flaky, cost_usd=0.005)
    assert res["status"] == "completed" and res["attempts"] == 2
    assert calls["n"] == 2
    failed = [r for r in sch.list_tasks(status="failed")]
    succeeded = [r for r in sch.list_tasks(status="succeeded")]
    assert len(failed) == 1 and len(succeeded) == 1


def test_dispatch_same_pool_internal_and_a2a_workers_share_scheduler(registry):
    """同池证据：内部 dispatch worker 与 A2A 入站 worker 注册进同一调度中心。"""
    from find_yourself.api.routes.a2a import ensure_inbound_worker
    from find_yourself.services.agent_dispatch import AgentDispatchService

    sch = UnifiedScheduler()
    svc = AgentDispatchService(registry=registry, scheduler=sch)
    svc._ensure_worker()
    ensure_inbound_worker(sch)
    channels = {w["channel"] for w in sch.workers()}
    assert channels == {CHANNEL_INTERNAL_AGENT, "a2a"}
    ids = {w["worker_id"] for w in sch.workers()}
    assert {"internal.dispatch-child", "a2a.inbound"} <= ids
