"""API integration tests for Canvas (05 多Agent协作可视化画布)."""

from __future__ import annotations

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
