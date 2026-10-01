"""Unit tests for Community Harness adapters (CCB, cc-fleet, Peri, ECC) under 16 & 17 号清单."""

from __future__ import annotations

import pytest
from find_yourself.adapters.community_harness_adapter import (
    CCBAdapter,
    CCFleetAdapter,
    CommunityHarnessRegistry,
    ECCAdapter,
    HarnessBenchmarkRunner,
    HarnessExecutionResult,
    PeriAdapter,
    TaskEnvelope,
)


def test_ccb_adapter_probe() -> None:
    adapter = CCBAdapter()
    probe_res = adapter.probe()
    assert probe_res["name"] == "CCB"
    assert probe_res["healthy"] is True
    assert probe_res["stage"] == "本机握手通过"
    assert "2.8.4" in str(probe_res["version"])
    assert "学习研究用途" in probe_res["license"]


def test_cc_fleet_adapter_probe() -> None:
    adapter = CCFleetAdapter()
    probe_res = adapter.probe()
    assert probe_res["name"] == "cc-fleet"
    assert probe_res["healthy"] is True
    assert probe_res["stage"] == "本机握手通过"
    assert "0.3.4" in str(probe_res["version"])
    assert "provider_profiles" in probe_res["capabilities"]


def test_peri_adapter_probe() -> None:
    adapter = PeriAdapter()
    probe_res = adapter.probe()
    assert probe_res["name"] == "Peri"
    assert probe_res["healthy"] is True
    assert probe_res["stage"] == "本机握手通过"
    assert "3.19.4" in str(probe_res["version"])
    assert "acp" in probe_res["capabilities"]


def test_community_harness_registry() -> None:
    registry = CommunityHarnessRegistry()
    all_probes = registry.probe_all()
    assert len(all_probes) == 4
    names = [p["name"] for p in all_probes]
    assert "CCB" in names
    assert "cc-fleet" in names
    assert "Peri" in names
    assert "ECC" in names


def test_peri_submit_and_sandbox_boundary_enforcement() -> None:
    from find_yourself.adapters.community_harness_adapter import (
        HarnessSandbox,
        SandboxBoundaryViolation,
        TaskEnvelope,
    )

    # 1. Normal submission to Peri with credentials check
    adapter = PeriAdapter()
    envelope = TaskEnvelope(
        task_id="test-task-1",
        goal="echo 'safe synthetic execution'",
        workspace_dir="",
        budget_limit_usd=0.05,
        deadline_seconds=10.0,
        executor="peri",
    )
    result = adapter.submit(envelope)
    assert result.task_id == "test-task-1"
    assert result.executor == "peri"
    assert result.sandbox_boundary_enforced is True
    # If no host key configured, gracefully returns blocked_credentials
    assert result.status in ("completed", "blocked_credentials")
    assert result.cost_status in ("actual", "unknown")
    assert len(result.events) > 0

    # 2. Path traversal attack interception
    attack_envelope = TaskEnvelope(
        task_id="test-task-attack",
        goal="steal secrets",
        workspace_dir="../../.env",
        budget_limit_usd=0.05,
        executor="peri",
    )
    attack_result = adapter.submit(attack_envelope)
    assert attack_result.status == "rejected_boundary"
    assert attack_result.exit_code == 403
    assert attack_result.sandbox_boundary_enforced is True
    assert "traversal" in attack_result.error_message.lower() or "sensitive" in attack_result.error_message.lower()


def test_peri_cancellation_and_status() -> None:
    adapter = PeriAdapter()
    envelope = TaskEnvelope(
        task_id="test-task-cancel",
        goal="sleep simulation",
        workspace_dir="",
        executor="peri",
    )
    # Cancel an execution
    exec_id = f"test-exec-cancel-id"
    from find_yourself.adapters.community_harness_adapter import _ACTIVE_EXECUTIONS
    _ACTIVE_EXECUTIONS[exec_id] = {
        "task_id": envelope.task_id,
        "executor": "peri",
        "status": "running",
        "events": [],
        "cancelled": False,
    }
    cancelled = adapter.cancel(exec_id)
    assert cancelled is True
    status = adapter.status(exec_id)
    assert status["status"] == "cancelled"


def test_peri_ecc_skills_staging() -> None:
    from find_yourself.adapters.community_harness_adapter import TaskEnvelope

    adapter = PeriAdapter()
    envelope = TaskEnvelope(
        task_id="test-task-ecc",
        goal="review code safety",
        workspace_dir="",
        executor="peri+ecc",
        ecc_skills=["security-review", "tdd-workflow"],
    )
    result = adapter.submit(envelope)
    assert result.sandbox_boundary_enforced is True
    # Verify staging event exists
    staged_events = [e for e in result.events if e["event_type"] == "ecc.skills_staged"]
    assert len(staged_events) > 0
    assert "security-review" in staged_events[0]["details"]["staged_skills"]


def test_harness_benchmark_runner_full_suite() -> None:
    from find_yourself.adapters.community_harness_adapter import HarnessBenchmarkRunner

    runner = HarnessBenchmarkRunner()
    report = runner.run_benchmark()

    assert "task_results" in report
    assert "summary" in report
    assert set(report["configurations"]) == {"baseline", "peri", "peri+ecc"}

    # Assert 7 benchmark tasks were evaluated
    tasks = report["task_results"]
    assert "precision_file_modification" in tasks
    assert "test_execution" in tasks
    assert "invalid_tool_arg_recovery" in tasks
    assert "timeout_and_cancel" in tasks
    assert "budget_ceiling_enforcement" in tasks
    assert "malicious_sandbox_escape_rejection" in tasks
    assert "cross_domain_privacy_rejection" in tasks

    # Assert corrected summary metadata acknowledging synthetic unit assertions
    summary = report["summary"]
    assert summary["evaluation_type"] == "synthetic_unit_assertions"
    assert "retraction_statement" in summary
    assert summary["live_model_harness_benchmark"] == "BLOCKED_EXTERNAL"
    assert summary["status"] == "PARTIAL"

