"""API integration tests for Canvas (05 多Agent协作可视化画布)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner


def test_canvas_api_lifecycle(client: TestClient) -> None:
    headers = login_owner(client)

    # 1. Templates & Probing
    r_tmpl = client.get("/api/canvas/templates", headers=headers)
    assert r_tmpl.status_code == 200
    assert len(r_tmpl.json()["items"]) >= 2

    r_conn = client.get("/api/canvas/connectors", headers=headers)
    assert r_conn.status_code == 200
    assert len(r_conn.json()["items"]) >= 6

    # 2. Create Instance
    r_create = client.post(
        "/api/canvas/instances",
        json={"project_name": "多Agent工程协作项目", "template_id": "work"},
        headers=headers,
    )
    assert r_create.status_code == 201
    inst = r_create.json()
    assert inst["orchestrator_id"] == "Codex"
    assert inst["domain"] == "work"
    inst_id = inst["id"]

    # 3. List & Get Instance
    r_list = client.get("/api/canvas/instances", headers=headers)
    assert r_list.status_code == 200
    assert any(i["id"] == inst_id for i in r_list.json()["items"])

    r_get = client.get(f"/api/canvas/instances/{inst_id}", headers=headers)
    assert r_get.status_code == 200
    assert r_get.json()["id"] == inst_id

    # 3b. Create root task for dispatching
    r_task = client.post(
        "/api/tasks",
        json={"goal": "工作研发主任务", "idempotency_key": "task-canvas-root-01", "domain": "work"},
        headers=headers,
    )
    assert r_task.status_code in (200, 201)
    root_task_id = r_task.json()["id"]

    # 4. Dispatch Subtask (OpenCode is not connected -> returns pending_adapter)
    r_disp = client.post(
        f"/api/canvas/instances/{inst_id}/dispatch",
        json={
            "root_task_id": root_task_id,
            "worker_id": "OpenCode",
            "goal": "完成自动化测试套件编写",
            "acceptance_criteria": "所有测试在沙箱中绿色通过",
            "budget_slice": 0.50,
        },
        headers=headers,
    )
    assert r_disp.status_code == 201
    disp = r_disp.json()
    assert disp["worker_id"] == "OpenCode"
    assert disp["state"] == "pending_adapter"
    subtask_id = disp["subtask_id"]

    # 4b. Test budget cap rejection (> 0.50 returns 422)
    r_over_budget = client.post(
        f"/api/canvas/instances/{inst_id}/dispatch",
        json={
            "root_task_id": root_task_id,
            "worker_id": "OpenCode",
            "goal": "超预算派发",
            "budget_slice": 0.51,
        },
        headers=headers,
    )
    assert r_over_budget.status_code == 422

    # 5. Record Handoff
    r_hnd = client.post(
        f"/api/canvas/instances/{inst_id}/handoff",
        json={
            "stage": "code_review",
            "goal": "交付测试并通过审查",
            "source_worker_id": "OpenCode",
            "target_worker_id": "Codex",
            "source_task_id": subtask_id,
            "completed_items": ["新增 12 个 API 单元测试用例", "断言覆盖率达标"],
            "artifact_refs": ["tests/api/test_canvas_api.py"],
            "evidence_refs": ["evidence/finalization/F4-collaboration-canvas.md"],
            "unresolved_issues": [],
            "risks": [],
            "next_steps": ["提交审查记录并并入主线"],
        },
        headers=headers,
    )
    assert r_hnd.status_code == 201
    hnd = r_hnd.json()
    assert hnd["stage"] == "code_review"
    assert len(hnd["completed_items"]) == 2

    # 6. Snapshot & Cursor Replay
    r_snap = client.get(f"/api/canvas/instances/{inst_id}/snapshot", headers=headers)
    assert r_snap.status_code == 200
    snap = r_snap.json()
    assert snap["instance"]["id"] == inst_id
    assert len(snap["dispatches"]) == 1
    assert len(snap["handoffs"]) == 1
    assert len(snap["events"]) >= 5

    r_events = client.get(f"/api/canvas/instances/{inst_id}/events?cursor=0", headers=headers)
    assert r_events.status_code == 200
    assert len(r_events.json()["items"]) == len(snap["events"])


def test_canvas_api_csrf_enforcement(client: TestClient) -> None:
    login_owner(client)
    r = client.post(
        "/api/canvas/instances",
        json={"project_name": "无CSRF请求", "template_id": "personal"},
    )
    assert r.status_code == 403


def test_canvas_api_hermes_dispatch_and_handoff_lifecycle(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Full HTTP API end-to-end test with auth, CSRF, real routing, session management,
    Hermes dispatch, ResearchAgent handoff, and domain mismatch enforcement."""
    # Deterministic hermetic adapter mocking for 100% test portability
    monkeypatch.setattr(
        "find_yourself.adapters.hermes_adapter.HermesAdapter.probe",
        lambda self: {"name": "Hermes", "installed": True, "healthy": True, "version": "0.1.0"},
    )
    monkeypatch.setattr(
        "find_yourself.adapters.hermes_adapter.HermesAdapter.dispatch_and_run",
        lambda self, subtask_id, goal, acceptance_criteria=None, local_execution_id=None: {
            "state": "completed",
            "validation_passed": True,
            "output": "【多智能体协同响应】作为个人协作规划器，我协调任务与数据安全边界。",
            "tokens": 420,
            "model": "hermes-3-llama-3.1-8b",
            "cost_status": "unknown",
            "estimated_cost_usd": 0.0,
            "duration_ms": 320,
            "external_session_id": "sess-mock-01",
            "local_execution_id": local_execution_id,
        },
    )

    headers = login_owner(client)

    # 1. Create personal canvas instance
    r_inst = client.post(
        "/api/canvas/instances",
        json={"project_name": "API端到端多智能体协作", "template_id": "personal"},
        headers=headers,
    )
    assert r_inst.status_code == 201
    inst = r_inst.json()
    inst_id = inst["id"]
    assert inst["domain"] == "personal"
    assert inst["orchestrator_id"] == "Hermes"

    # 2. Create personal root task
    r_task = client.post(
        "/api/tasks",
        json={"goal": "个人生活多智能体主任务", "idempotency_key": "task-pers-api-root-01", "domain": "personal"},
        headers=headers,
    )
    assert r_task.status_code in (200, 201)
    root_task_id = r_task.json()["id"]

    # 3. Create work root task to test domain mismatch rejection via API
    r_work_task = client.post(
        "/api/tasks",
        json={"goal": "工作工程主任务", "idempotency_key": "task-work-api-root-01", "domain": "work"},
        headers=headers,
    )
    assert r_work_task.status_code in (200, 201)
    work_root_task_id = r_work_task.json()["id"]

    # 3a. Dispathing work root task to personal canvas must fail with 422
    r_mismatch = client.post(
        f"/api/canvas/instances/{inst_id}/dispatch",
        json={
            "root_task_id": work_root_task_id,
            "worker_id": "Hermes",
            "goal": "尝试跨域混入工作任务",
            "budget_slice": 0.20,
        },
        headers=headers,
    )
    assert r_mismatch.status_code == 422
    assert "Root task domain 'work' does not match canvas domain 'personal'" in r_mismatch.json().get("error", {}).get("message", "")

    # 3b. Dispathing goal with ungranted sensitive marker must fail with 422
    r_smuggle = client.post(
        f"/api/canvas/instances/{inst_id}/dispatch",
        json={
            "root_task_id": root_task_id,
            "worker_id": "Hermes",
            "goal": "请处理公司的商业机密材料",
            "budget_slice": 0.20,
        },
        headers=headers,
    )
    assert r_smuggle.status_code == 422
    assert "sensitive marker '商业机密'" in r_smuggle.json().get("error", {}).get("message", "")

    # 4. Dispatch valid subtask to Hermes via HTTP API
    r_disp_hermes = client.post(
        f"/api/canvas/instances/{inst_id}/dispatch",
        json={
            "root_task_id": root_task_id,
            "worker_id": "Hermes",
            "goal": "请用简短中文说明你在多智能体协作环境中的核心角色",
            "acceptance_criteria": {"contains": ["多智能体"], "min_length": 5},
            "budget_slice": 0.25,
        },
        headers=headers,
    )
    assert r_disp_hermes.status_code == 201
    disp_h = r_disp_hermes.json()
    assert disp_h["worker_id"] == "Hermes"
    assert disp_h["state"] == "completed"
    subtask_1_id = disp_h["subtask_id"]

    # 5. Record structured handoff packet from Hermes to ResearchAgent
    r_handoff = client.post(
        f"/api/canvas/instances/{inst_id}/handoff",
        json={
            "stage": "research_delegation",
            "goal": "交接深入研究子任务",
            "source_worker_id": "Hermes",
            "target_worker_id": "ResearchAgent",
            "source_task_id": subtask_1_id,
            "completed_items": ["Hermes完成初步角色与策略澄清"],
            "artifact_refs": ["artifacts/hermes-init.json"],
            "evidence_refs": ["evidence/traces/hermes.json"],
            "unresolved_issues": [],
            "risks": [],
            "next_steps": ["由ResearchAgent展开深入文献与方案分析"],
        },
        headers=headers,
    )
    assert r_handoff.status_code == 201
    hnd = r_handoff.json()
    assert hnd["source_worker_id"] == "Hermes"
    assert hnd["target_worker_id"] == "ResearchAgent"
    handoff_id = hnd["id"]

    # 6. Dispatch second subtask to ResearchAgent with handoff packet
    r_disp_research = client.post(
        f"/api/canvas/instances/{inst_id}/dispatch",
        json={
            "root_task_id": root_task_id,
            "worker_id": "ResearchAgent",
            "goal": "根据交接上下文执行跨产品代理方案调研",
            "budget_slice": 0.20,
            "input_ref": {"handoff_id": handoff_id},
        },
        headers=headers,
    )
    assert r_disp_research.status_code == 201
    disp_r = r_disp_research.json()
    assert disp_r["worker_id"] == "ResearchAgent"
    # ResearchAgent enters dispatched (truthful internal staging, not fake auto-completed)
    assert disp_r["state"] == "dispatched"
    subtask_2_id = disp_r["subtask_id"]

    # 6b. Explicitly complete subtask 2 via API endpoint
    r_complete = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_2_id}/complete",
        json={"output": "[ResearchAgent] 已完成跨产品代理方案调研与对比报告。"},
        headers=headers,
    )
    assert r_complete.status_code == 200
    comp_r = r_complete.json()
    assert comp_r["state"] == "completed"

    # 7. Get Snapshot and verify both agents collaborated with events tied to subtask IDs
    r_snap = client.get(f"/api/canvas/instances/{inst_id}/snapshot", headers=headers)
    assert r_snap.status_code == 200
    snap = r_snap.json()
    assert len(snap["dispatches"]) == 2
    assert len(snap["handoffs"]) == 1

    # Verify event sequence and task IDs
    events = snap["events"]
    assert any(e["event_type"] == "agent.task_completed" and e["task_id"] == subtask_1_id for e in events)
    assert any(e["event_type"] == "agent.task_completed" and e["task_id"] == subtask_2_id for e in events)
    assert any(e["event_type"] == "handoff.created" and e["task_id"] == subtask_1_id for e in events)
