"""API Integration test for complete Orchestrator Rework & Verification Cycle (规划→派发→接收进度→返工→独立验证→验收).

Adheres to:
- 05_多Agent协作可视化画布功能规格.md
- 13_双端产品续作与验收清单.md
- 16_社区Harness候选接入与普通模型对照验收.md

Workflow:
1. Orchestrator plans canvas project and subtask with strict acceptance criteria.
2. Subtask is dispatched to worker with budget reservation ($0.05).
3. Worker submits initial draft with an identified defect (unmet criteria).
4. Reviewer/Orchestrator requests rework with structured feedback; state enters waiting_rework.
5. Worker addresses feedback and resubmits; state transitions to running.
6. Independent Verification agent runs tests and records passing verification.
7. Orchestrator accepts subtask, settles budget ($0.03), and creates handoff packet.
8. Full machine trace exported to evidence/process-traces/07-orchestrator-rework-verification-cycle.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest
from fastapi.testclient import TestClient
from helpers import login_owner


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_full_orchestrator_rework_verification_acceptance_cycle(
    client: TestClient, tmp_path: Path,
) -> None:
    headers = login_owner(client)
    trace_events: list[dict[str, Any]] = []

    def record_step(step_name: str, payload: Any, response_data: Any) -> None:
        trace_events.append({
            "step": step_name,
            "request": payload,
            "response": response_data,
        })

    # -------------------------------------------------------------------------
    # Step 1: Canvas Instance Creation (主协调规划准备)
    # -------------------------------------------------------------------------
    create_payload = {
        "project_name": "企业级数据脱敏与合规治理系统",
        "template_id": "work",
    }
    r_create = client.post("/api/canvas/instances", json=create_payload, headers=headers)
    assert r_create.status_code == 201, r_create.text
    inst = r_create.json()
    inst_id = inst["id"]
    record_step("1_canvas_create", create_payload, inst)

    # -------------------------------------------------------------------------
    # Step 2: 主协调 Agent 规划与派发 (Planning & Dispatch with Budget Reservation)
    # -------------------------------------------------------------------------
    root_task_id = f"root-{inst_id}"
    dispatch_payload = {
        "root_task_id": root_task_id,
        "worker_id": "EngineeringAgent",
        "goal": "开发用户联系数据脱敏函数 mask_user_records()：对11位手机号保留后4位，对邮箱用户名保留首尾字母并掩码中间部分，确保边界条件健壮性。",
        "acceptance_criteria": {
            "criteria": [
                "phone_masking: 11-digit phone numbers masked like 138****5678",
                "email_masking: alice.smith@example.com masked like a***h@example.com",
                "edge_cases: null/empty strings returned safely without throwing unhandled exceptions",
                "unit_tests: automated pytest suite with 100% assertions passed",
            ]
        },
        "budget_slice": 0.05,
    }
    r_disp = client.post(f"/api/canvas/instances/{inst_id}/dispatch", json=dispatch_payload, headers=headers)
    assert r_disp.status_code == 201, r_disp.text
    disp_data = r_disp.json()
    subtask_id = disp_data["subtask_id"]
    assert disp_data["state"] in ("dispatched", "running")
    assert disp_data["budget_slice"] == 0.05
    record_step("2_subtask_dispatch", dispatch_payload, disp_data)

    # -------------------------------------------------------------------------
    # Step 3: 接收进度 (Initial Progress with Defect)
    # Worker returns initial code, but email masking was omitted/defective
    # -------------------------------------------------------------------------
    initial_draft = {
        "code": "def mask_records(r):\n    r['phone'] = re.sub(r'(\\d{3})\\d{4}(\\d{4})', r'\\1****\\2', r.get('phone', ''))\n    return r\n",
        "notes": "Implemented phone masking. Email masking pending clarification.",
    }
    record_step("3_progress_intake", {"subtask_id": subtask_id}, initial_draft)

    # -------------------------------------------------------------------------
    # Step 4: 审查发现缺陷并要求返工 (Review & Request Rework)
    # -------------------------------------------------------------------------
    rework_payload = {
        "feedback": "审查未通过：邮箱脱敏规则未实现，返回了明文邮箱。请实现用户名首尾保留、中间掩码逻辑（如 a***h@example.com），并补充完整的单元测试用例。",
        "criteria_unmet": [
            "email_masking: raw cleartext email returned in output records",
            "unit_tests: missing email test assertions",
        ],
    }
    r_rework = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/rework",
        json=rework_payload,
        headers=headers,
    )
    assert r_rework.status_code == 200, r_rework.text
    rework_data = r_rework.json()
    assert rework_data["state"] == "waiting_rework"
    assert rework_data["attempts"] == 2
    assert len(rework_data["rework_history"]) == 1
    record_step("4_request_rework", rework_payload, rework_data)

    # -------------------------------------------------------------------------
    # Step 5: 工人根据反馈完成返工并重提 (Worker Rework & Resubmission)
    # -------------------------------------------------------------------------
    reworked_code = (
        "import re\n\n"
        "def mask_phone(phone: str) -> str:\n"
        "    if not phone or len(phone) < 11:\n"
        "        return phone\n"
        "    return re.sub(r'(\\d{3})\\d{4}(\\d{4})', r'\\1****\\2', str(phone))\n\n"
        "def mask_email(email: str) -> str:\n"
        "    if not email or '@' not in email:\n"
        "        return email\n"
        "    user, domain = email.split('@', 1)\n"
        "    if len(user) <= 2:\n"
        "        masked_user = user[0] + '*'\n"
        "    else:\n"
        "        masked_user = user[0] + '***' + user[-1]\n"
        "    return f'{masked_user}@{domain}'\n\n"
        "def mask_user_records(records: list[dict]) -> list[dict]:\n"
        "    return [{'phone': mask_phone(r.get('phone', '')), 'email': mask_email(r.get('email', ''))} for r in records]\n"
    )

    resubmit_payload = {
        "output": "已修复邮箱脱敏缺陷并增加边界保护：手机号与邮箱均已通过规范掩码处理。",
        "artifacts": [
            {
                "name": "src/sanitizer/mask.py",
                "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "content_preview": reworked_code[:200],
            }
        ],
    }
    r_resubmit = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/resubmit",
        json=resubmit_payload,
        headers=headers,
    )
    assert r_resubmit.status_code == 200, r_resubmit.text
    resubmit_data = r_resubmit.json()
    assert resubmit_data["state"] == "running"
    record_step("5_resubmit_subtask", resubmit_payload, resubmit_data)

    # -------------------------------------------------------------------------
    # Step 6: 独立验证 (Independent Verification)
    # -------------------------------------------------------------------------
    artifact_hash = resubmit_payload["artifacts"][0]["sha256"]
    verify_payload = {
        "artifact_hash": artifact_hash,
        "test_results": {
            "passed": True,
            "tests_run": 4,
            "failures": 0,
            "test_cases": [
                {"name": "test_phone_masking", "input": "13812345678", "output": "138****5678", "status": "PASS"},
                {"name": "test_email_masking", "input": "alice.smith@example.com", "output": "a***h@example.com", "status": "PASS"},
                {"name": "test_short_email_masking", "input": "jo@test.com", "output": "j*@test.com", "status": "PASS"},
                {"name": "test_empty_edge_cases", "input": "", "output": "", "status": "PASS"},
            ],
            "execution_time_ms": 45,
            "coverage_pct": 100.0,
            "composite_artifact_hash": artifact_hash,
        },
    }
    r_verify = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/verify",
        json=verify_payload,
        headers=headers,
    )
    assert r_verify.status_code == 200, r_verify.text
    verify_data = r_verify.json()
    assert verify_data["verification"]["passed"] is True
    verification_id = verify_data["verification"]["verification_id"]
    record_step("6_independent_verification", verify_payload, verify_data)

    # -------------------------------------------------------------------------
    # Step 7: 最终验收与预算结算 (Final Acceptance & Budget Settlement)
    # -------------------------------------------------------------------------
    # Executor identity is determined strictly by authentication actor (cannot be spoofed by request body).
    complete_payload = {
        "output": "脱敏模块经独立测试验证全部达标，正式验收合并。返工轮次：1，最终状态：合格。",
        "settled_budget": 0.03,
        "artifact_version": artifact_hash,
        "verification_id": verification_id,
        "completed_by": "EngineeringAgent/spoofed_attempt",  # Ignored by server
    }
    r_complete = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/complete",
        json=complete_payload,
        headers=headers,
    )
    assert r_complete.status_code == 200, r_complete.text
    complete_data = r_complete.json()
    assert complete_data["state"] == "completed"
    # Identity is determined by server authentication (owner session -> owner/manual), not spoofed body string
    assert complete_data["completed_by"] == "owner/manual"
    assert complete_data["is_manual_completion"] is True
    assert complete_data["bound_verification_id"] == verification_id
    assert complete_data["bound_artifact_hash"] == artifact_hash
    record_step("7_final_acceptance", complete_payload, complete_data)

    # -------------------------------------------------------------------------
    # Step 8: 结构化交接记录 (Downstream Handoff Packet)
    # -------------------------------------------------------------------------
    handoff_payload = {
        "stage": "sanitization_verified",
        "goal": "将经返工与独立验证通过的脱敏模块交接给审计分析 Agent 进行合规归档",
        "source_worker_id": "EngineeringAgent",
        "target_worker_id": "AuditAgent",
        "source_task_id": subtask_id,
        "completed_items": [
            "11位手机号掩码 (138****5678)",
            "邮箱用户名保留首尾字母掩码 (a***h@example.com)",
            "空值与边界保护",
            "独立 pytest 4 项测试 100% 通过",
        ],
        "artifact_refs": ["src/sanitizer/mask.py"],
        "evidence_refs": ["evidence/process-traces/07-orchestrator-rework-verification-cycle.json"],
        "unresolved_issues": [],
        "risks": [],
        "next_steps": ["执行审计基线归档与加密持久化"],
    }
    r_handoff = client.post(
        f"/api/canvas/instances/{inst_id}/handoff",
        json=handoff_payload,
        headers=headers,
    )
    assert r_handoff.status_code == 201, r_handoff.text
    handoff_data = r_handoff.json()
    record_step("8_record_handoff", handoff_payload, handoff_data)

    # -------------------------------------------------------------------------
    # Step 9: 验证事件流时间戳与完整状态快照 (Audit Timeline & Snapshot)
    # -------------------------------------------------------------------------
    r_events = client.get(f"/api/canvas/instances/{inst_id}/events", headers=headers)
    assert r_events.status_code == 200
    events = r_events.json()["items"]
    event_types = [e["event_type"] for e in events]

    assert "canvas.instance.created" in event_types
    assert "task.dispatched" in event_types
    assert "agent.rework_requested" in event_types
    assert "agent.subtask_resubmitted" in event_types
    assert "agent.independent_verification_passed" in event_types
    assert "agent.task_completed" in event_types

    r_snap = client.get(f"/api/canvas/instances/{inst_id}/snapshot", headers=headers)
    assert r_snap.status_code == 200
    snap = r_snap.json()
    assert len(snap["dispatches"]) >= 1
    assert snap["dispatches"][0]["state"] == "completed"
    assert len(snap["handoffs"]) >= 1

    # -------------------------------------------------------------------------
    # Step 10: 导出真实机器审计 Trace
    # -------------------------------------------------------------------------
    # 落盘到 pytest 的 tmp_path，**不写仓库路径**（见 test_f9_unified_evaluations.py 同类说明）。
    # 历史行为是写 REPO_ROOT/evidence/process-traces/...（被 git 跟踪），
    # 导致每次跑测试污染工作区、并与并行会话/git am 冲突。
    trace_file = tmp_path / "07-orchestrator-rework-verification-cycle.json"

    export_payload = {
        "lifecycle_type": "orchestrator_rework_independent_verification_cycle",
        "project": "Find Yourself Multi-Agent Canvas",
        "instance_id": inst_id,
        "subtask_id": subtask_id,
        "events_summary": event_types,
        "timeline_steps": trace_events,
        "events_log": events,
        "snapshot": snap,
        "verdict": {
            "status": "PARTIAL",
            "cycle_completed": True,
            "rework_performed": True,
            "independent_verification_passed": True,
            "budget_reserved_usd": 0.05,
            "budget_settled_usd": 0.03,
            "external_live_model": "BLOCKED_EXTERNAL",
        },
    }
    trace_file.write_text(json.dumps(export_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    assert trace_file.exists()
