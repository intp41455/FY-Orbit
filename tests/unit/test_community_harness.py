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

    # §6.7: each task is labeled with an evidence type and real vs synthetic are split
    for task_name, configs in report["task_results"].items():
        for cfg, res in configs.items():
            assert "evidence_type" in res
    assert summary["real_execution_evals"] >= 1  # test_execution is a real subprocess run
    assert summary["synthetic_evals"] >= 1
    # No-credential items must NOT be counted as real Harness success.
    assert summary["real_harness_success_rate"] is not None or summary["real_execution_evals"] == 0


def test_s6_authorization_gate_rejects_unauthorized() -> None:
    from find_yourself.adapters.community_harness_adapter import PeriAdapter, TaskEnvelope

    adapter = PeriAdapter()
    env = TaskEnvelope(
        task_id="s6-auth", goal="anything", workspace_dir="",
        executor="peri", authorization="unauthorized",
    )
    result = adapter.submit(env)
    assert result.status == "rejected_untrusted"
    assert result.exit_code == 403
    # trusted mode must be clearly flagged; we never claim an OS security sandbox.
    assert result.trusted_mode is True
    assert result.isolation_level == "trusted_local"


def test_s6_budget_gateway_enforces_ceiling() -> None:
    from find_yourself.adapters.community_harness_adapter import (
        HarnessBudgetGateway, PeriAdapter, TaskEnvelope,
    )

    root = f"s6-budget-{int(__import__('time').time() * 1000)}"
    # Pre-reserve the full limit so a second reserve must exceed it (§6.2).
    gw = HarnessBudgetGateway()
    first = gw.reserve(root, 0.05)
    assert first["allowed"] is True

    adapter = PeriAdapter()
    env = TaskEnvelope(
        task_id=root, goal="g", workspace_dir="", executor="peri",
        budget_limit_usd=0.05,
    )
    result = adapter.submit(env)
    assert result.status == "budget_exceeded"
    assert result.budget_status == "enforced"
    # cleanup reservation
    gw.release(root)


def test_s6_dispatcher_routes_by_capability() -> None:
    from find_yourself.adapters.community_harness_adapter import (
        CommunityHarnessRegistry, HarnessDispatcher, TaskEnvelope,
    )

    d = HarnessDispatcher()
    for executor in ("ccb", "cc-fleet", "internal"):
        res = d.dispatch(TaskEnvelope(task_id="s6", goal="g", workspace_dir="", executor=executor))
        assert res.status == "not_integrated", executor
        # The harness must refuse to silently route unsupported executors to Peri.
        assert "no submit gateway" in (res.error_message or "")

    # Peri (and Peri+ECC) are the only executors with a real submit path.
    assert "submit" in d.capability("peri")
    assert "submit" in d.capability("peri+ecc")

    registry = CommunityHarnessRegistry()
    r2 = registry.dispatch(TaskEnvelope(task_id="s6", goal="g", workspace_dir="", executor="ccb"))
    assert r2.status == "not_integrated"


def test_s6_ecc_verification_records_approval() -> None:
    from find_yourself.adapters.community_harness_adapter import PeriAdapter, TaskEnvelope

    adapter = PeriAdapter()
    env = TaskEnvelope(
        task_id="s6-ecc", goal="review", workspace_dir="",
        executor="peri+ecc", ecc_skills=["security-review", "tdd-workflow"],
    )
    result = adapter.submit(env)
    # Staging occurred and an approval manifest was consulted.
    staged = [e for e in result.events if e["event_type"] == "ecc.skills_staged"]
    verified = [e for e in result.events if e["event_type"] == "ecc.verification"]
    assert staged, "expected ecc.skills_staged event"
    assert verified, "expected ecc.verification event (copying != approval)"
    # The result must honestly report whether the model was *instructed* vs *proven*.
    assert result.ecc_instructed is True
    # With a valid local approval manifest the skills verify True.
    assert result.ecc_verified is True


def test_s6_process_tree_kill_and_reclamation() -> None:
    import subprocess as _sp
    import sys as _sys
    import time as _time
    from find_yourself.adapters.community_harness_adapter import kill_process_tree, process_alive

    # Spawn a child that outlives its parent on its own (a detached tree).
    # Use the real interpreter (sys.executable) instead of the bare "python"
    # name: on some Windows installs "python" resolves to the launcher (py.exe),
    # which can exit immediately after spawning the real interpreter, making the
    # returned PID already dead and the test's premise invalid.
    proc = _sp.Popen(
        [_sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
    )
    pid = proc.pid
    # The OS process table / tasklist may not enumerate a just-spawned PID on the
    # first pass (snapshot lag, AV scan). Give the child a short grace window and
    # confirm it is genuinely running — via BOTH the Popen handle and the
    # production liveness check — before we test reclamation.
    alive_seen = False
    for _ in range(100):
        if proc.poll() is None and process_alive(pid):
            alive_seen = True
            break
        _time.sleep(0.02)
    assert alive_seen, f"child pid={pid} never reported alive (handle poll + tasklist)"
    evidence = kill_process_tree(pid)
    assert evidence["attempted"] is True
    assert evidence["reaped"] is True, f"process tree not reaped: {evidence}"
    assert process_alive(pid) is False


def test_s6_active_executions_persist_and_reconcile() -> None:
    import json as _json
    from find_yourself.adapters.community_harness_adapter import (
        _ACTIVE_EXECUTIONS, persist_active_executions, reconcile_active_executions,
        HARNESS_LAB_DIR,
    )

    exec_id = f"s6-persist-{int(__import__('time').time() * 1000)}"
    _ACTIVE_EXECUTIONS[exec_id] = {
        "task_id": "s6-task", "executor": "peri", "status": "running",
        "events": [], "cancelled": False, "finalized": False,
        "process": None, "sandbox": None,
    }
    persist_active_executions()
    snap_path = HARNESS_LAB_DIR / "active_executions.json"
    assert snap_path.exists()
    snap = _json.loads(snap_path.read_text(encoding="utf-8"))
    assert exec_id in snap

    # Reconcile: an entry whose process is gone and not finalized is tombstoned.
    summary = reconcile_active_executions()
    assert summary["reconciled"] >= 1
    # cleanup test entry
    _ACTIVE_EXECUTIONS.pop(exec_id, None)


