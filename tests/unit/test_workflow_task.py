"""Deterministic tests for the Temporal task workflow.

These use the in-memory :class:`InMemoryPorts` adapter and Temporal's
time-skipping test environment. They are LOCAL unit tests: no real Postgres,
no real Temporal cluster, no model calls. Real integration verification is
tracked separately as NOT_RUN until containers exist.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from find_yourself.workflows.activities import Activities
from find_yourself.workflows.fake import InMemoryPorts
from find_yourself.workflows.workflow import TaskWorkflow


def _input(task_id: str = "t-1", *, max_steps: int = 8, max_retries: int = 2,
           deadline_offset_s: int = 3600) -> dict:
    return {
        "task_id": task_id,
        "owner_id": "owner-1",
        "goal": "deterministic test goal",
        "domain": "personal",
        "mode": "listen",
        "idempotency_key": "idem-" + task_id,
        "limits": {
            "max_steps": max_steps,
            "max_retries": max_retries,
            "max_cost_usd": 0.5,
            "deadline": (datetime.now(timezone.utc) + timedelta(seconds=deadline_offset_s)).isoformat(),
        },
        "context": {},
    }


async def _wait_status(handle, pred, tries: int = 300) -> dict:
    for _ in range(tries):
        s = await handle.query(TaskWorkflow.status)
        if pred(s):
            return s
        await asyncio.sleep(0.05)
    raise AssertionError("workflow status never matched predicate")


@pytest.fixture
def ports() -> InMemoryPorts:
    return InMemoryPorts()


@pytest.fixture
async def env():
    e = await WorkflowEnvironment.start_time_skipping()
    try:
        yield e
    finally:
        await e.shutdown()


async def _start(env, ports: InMemoryPorts, queue: str, inp: dict, worker: Worker):
    async with worker:
        handle = await env.client.start_workflow(
            TaskWorkflow.run, inp, id="fy-task:" + inp["task_id"], task_queue=queue
        )
        return handle


# ---------------------------------------------------------------------------
async def test_simple_tool_then_finish(env, ports: InMemoryPorts):
    queue = "q-complete"
    ports.plan_script = [
        {"kind": "tool_call", "tool": "echo", "payload": {"estimated_usd": 0.01}},
    ]
    ports.tool_script = [{"status": "ok", "spent_usd": 0.01}]
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(), id="fy-task:t-1", task_queue=queue)
        res = await h.result()
    assert res["status"] == "completed"
    assert len(ports.tool_calls) == 1


async def test_approval_executes_effect_once(env, ports: InMemoryPorts):
    queue = "q-approval"
    far = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    ports.plan_script = [
        {"kind": "external_side_effect", "proposal_id": "p1", "expected_digest": "d1",
         "expected_version": 0, "proposal_expires_at": far},
        {"kind": "finish"},
    ]
    ports.proposals["p1"] = {"status": "approved_pending_execution", "digest": "d1",
                              "expected_version": 0}
    ports.external_script = [{"status": "ok", "spent_usd": 0.0}]
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-2"), id="fy-task:t-2", task_queue=queue)
        await _wait_status(h, lambda s: s.get("state") == "awaiting_approval")
        await h.signal("approval", args=["p1", "d1", "approve"])
        res = await h.result()
    assert res["status"] == "completed"
    assert len(ports.external_calls) == 1  # exactly once


async def test_recovers_while_waiting_approval(env, ports: InMemoryPorts):
    # Recovery while parked on an approval: after the approval signal arrives,
    # the workflow must resume from its checkpoint and execute the external
    # effect exactly once (the outbox claim is the single-consumption guard).
    # A real worker-process restart against a Temporal cluster is covered by
    # integration (NOT_RUN); here we exercise the checkpoint-resume path.
    queue = "q-restart"
    far = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    ports.plan_script = [
        {"kind": "external_side_effect", "proposal_id": "p2", "expected_digest": "d2",
         "expected_version": 0, "proposal_expires_at": far},
        {"kind": "finish"},
    ]
    ports.proposals["p2"] = {"status": "approved_pending_execution", "digest": "d2",
                              "expected_version": 0}
    ports.external_script = [{"status": "ok", "spent_usd": 0.0}]

    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-3"), id="fy-task:t-3", task_queue=queue)
        # Park at the approval checkpoint.
        await _wait_status(h, lambda s: s.get("state") == "awaiting_approval")
        # The workflow is parked on checkpoint task:t-3:attempt:1:stage:awaiting_approval.
        parked = await h.query(TaskWorkflow.status)
        assert parked["last_checkpoint_key"] == "task:t-3:attempt:1:stage:awaiting_approval"
        # Deliver the approval; resume from the checkpoint and run the effect once.
        await h.signal("approval", args=["p2", "d2", "approve"])
        res = await h.result()
    assert res["status"] == "completed"
    assert len(ports.external_calls) == 1


async def test_duplicate_approval_signal_executes_once(env, ports: InMemoryPorts):
    queue = "q-dup"
    far = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    ports.plan_script = [
        {"kind": "external_side_effect", "proposal_id": "p3", "expected_digest": "d3",
         "expected_version": 0, "proposal_expires_at": far},
        {"kind": "finish"},
    ]
    ports.proposals["p3"] = {"status": "approved_pending_execution", "digest": "d3",
                              "expected_version": 0}
    ports.external_script = [{"status": "ok", "spent_usd": 0.0}]
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-4"), id="fy-task:t-4", task_queue=queue)
        await _wait_status(h, lambda s: s.get("state") == "awaiting_approval")
        for _ in range(3):
            await h.signal("approval", args=["p3", "d3", "approve"])
        res = await h.result()
    assert res["status"] == "completed"
    assert len(ports.external_calls) == 1


async def test_approval_signal_not_trusted_rejected(env, ports: InMemoryPorts):
    queue = "q-mistrust"
    far = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    ports.plan_script = [
        {"kind": "external_side_effect", "proposal_id": "p4", "expected_digest": "d4",
         "expected_version": 0, "proposal_expires_at": far},
    ]
    # The DB says REJECTED even though a signal claims approval.
    ports.proposals["p4"] = {"status": "rejected", "digest": "d4", "expected_version": 0}
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-5"), id="fy-task:t-5", task_queue=queue)
        await _wait_status(h, lambda s: s.get("state") == "awaiting_approval")
        await h.signal("approval", args=["p4", "d4", "approve"])
        res = await h.result()
    assert res["status"] == "failed"
    assert len(ports.external_calls) == 0  # signal alone must not execute


async def test_cancel_while_waiting_input(env, ports: InMemoryPorts):
    queue = "q-cancel"
    ports.plan_script = [{"kind": "wait_input"}]
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-6"), id="fy-task:t-6", task_queue=queue)
        await _wait_status(h, lambda s: s.get("stage") == "awaiting_input")
        await h.signal("cancel", args=["owner requested stop"])
        res = await h.result()
    assert res["status"] == "cancelled"
    assert ports.cancelled_reason == "owner requested stop"


async def test_deadline_timeout(env, ports: InMemoryPorts):
    queue = "q-deadline"
    ports.plan_script = [{"kind": "wait_input"}]
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-7", deadline_offset_s=5),
            id="fy-task:t-7", task_queue=queue)
        res = await h.result()
    assert res["status"] == "failed"
    assert res["failure"]["code"] == "deadline_exceeded"


async def test_tool_unknown_does_not_blind_retry(env, ports: InMemoryPorts):
    queue = "q-unknown"
    ports.plan_script = [
        {"kind": "tool_call", "tool": "net", "payload": {"estimated_usd": 0.01}},
    ]
    ports.tool_script = [{"status": "unknown", "spent_usd": 0.0}]
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-8"), id="fy-task:t-8", task_queue=queue)
        res = await h.result()
    assert res["status"] == "awaiting_reconciliation"
    assert len(ports.tool_calls) == 1  # no blind retry


async def test_max_steps_stops(env, ports: InMemoryPorts):
    queue = "q-steps"
    ports.plan_script = [
        {"kind": "tool_call", "tool": "t", "payload": {"estimated_usd": 0.01}},
        {"kind": "tool_call", "tool": "t", "payload": {"estimated_usd": 0.01}},
        {"kind": "tool_call", "tool": "t", "payload": {"estimated_usd": 0.01}},
        {"kind": "finish"},
    ]
    ports.tool_script = [{"status": "ok", "spent_usd": 0.01} for _ in range(3)]
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-9", max_steps=2),
            id="fy-task:t-9", task_queue=queue)
        res = await h.result()
    assert res["status"] == "failed"
    assert res["failure"]["code"] == "max_steps_reached"
    assert len(ports.tool_calls) == 2


async def test_budget_exceeded_stops(env, ports: InMemoryPorts):
    queue = "q-budget"
    ports.budget_remaining = 0.0
    ports.plan_script = [
        {"kind": "tool_call", "tool": "t", "payload": {"estimated_usd": 0.1}},
    ]
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-10"), id="fy-task:t-10", task_queue=queue)
        res = await h.result()
    assert res["status"] == "failed"
    assert res["failure"]["code"] == "budget_exceeded"
    assert len(ports.tool_calls) == 0


async def test_retryable_error_then_success(env, ports: InMemoryPorts):
    queue = "q-retry"
    ports.plan_script = [
        {"kind": "tool_call", "tool": "t", "payload": {"estimated_usd": 0.01}},
        {"kind": "finish"},
    ]
    ports.tool_script = [
        {"_raise": "transient network blip"},
        {"status": "ok", "spent_usd": 0.01},
    ]
    worker = Worker(env.client, task_queue=queue, workflows=[TaskWorkflow],
                    activities=Activities(ports).all())
    async with worker:
        h = await env.client.start_workflow(
            TaskWorkflow.run, _input(task_id="t-11", max_retries=2),
            id="fy-task:t-11", task_queue=queue)
        res = await h.result()
    assert res["status"] == "completed"
    assert len(ports.tool_calls) == 2
