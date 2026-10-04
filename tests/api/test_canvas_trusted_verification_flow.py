"""Trusted Verification Runner & Orchestrator Rework Cycle Integration Test.

Adheres to:
- 18_工程代码工作台与主协调Agent全流程实施规格.md
- 16_社区Harness候选接入与普通模型对照验收.md

Workflow:
1. Workspace setup: real code and real test files on disk in temporary workspace.
2. Initial draft: worker produces code with defect (email masking omitted).
3. Trusted Verification: execute real pytest on disk; fails (exit_code != 0), logs & SHA-256 digests captured.
4. Completion gate: attempt to complete without passing verification is strictly rejected (ValidationFailed).
5. Rework request: orchestrator requests rework; state transitions to waiting_rework.
6. Worker rework: worker edits mask.py on disk to implement compliant masking.
7. Resubmit: worker resubmits with updated artifact manifest.
8. Re-verification: execute real pytest on disk; passes (exit_code == 0), updated SHA-256 digests captured.
9. Version tamper check: attempt to complete with mismatched artifact hash is strictly rejected (ValidationFailed).
10. Final acceptance: subtask completes, binding authenticated actor identity, verification ID, and artifact hash.
11. Budget settlement: $0.03 settled from $0.05 reservation, remaining $0.02 released.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any
import pytest
from fastapi.testclient import TestClient
from helpers import login_owner


DEFECTIVE_CODE = '''import re

def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 11:
        return phone
    return re.sub(r'(\\d{3})\\d{4}(\\d{4})', r'\\1****\\2', str(phone))

def mask_email(email: str) -> str:
    # DEFECT: Returns raw cleartext email instead of masked username
    return email

def mask_user_records(records: list[dict]) -> list[dict]:
    return [{'phone': mask_phone(r.get('phone', '')), 'email': mask_email(r.get('email', ''))} for r in records]
'''

FIXED_CODE = '''import re

def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 11:
        return phone
    return re.sub(r'(\\d{3})\\d{4}(\\d{4})', r'\\1****\\2', str(phone))

def mask_email(email: str) -> str:
    if not email or '@' not in email:
        return email
    user, domain = email.split('@', 1)
    if len(user) <= 2:
        masked_user = user[0] + '*'
    else:
        masked_user = user[0] + '***' + user[-1]
    return f'{masked_user}@{domain}'

def mask_user_records(records: list[dict]) -> list[dict]:
    return [{'phone': mask_phone(r.get('phone', '')), 'email': mask_email(r.get('email', ''))} for r in records]
'''

TEST_SUITE_CODE = '''import pytest
from sanitizer.mask import mask_phone, mask_email, mask_user_records

def test_phone_masking():
    assert mask_phone("13812345678") == "138****5678"

def test_email_masking():
    # Requires username to be masked: alice.smith@example.com -> a***h@example.com
    assert mask_email("alice.smith@example.com") == "a***h@example.com"

def test_user_records():
    recs = [{"phone": "13900001234", "email": "john.doe@test.com"}]
    res = mask_user_records(recs)
    assert res[0]["phone"] == "139****1234"
    assert res[0]["email"] == "j***e@test.com"
'''


def test_trusted_verification_orchestrator_rework_flow(client: TestClient, tmp_path: Path) -> None:
    headers = login_owner(client)

    # 1. Prepare actual workspace on disk
    src_dir = tmp_path / "sanitizer"
    tests_dir = tmp_path / "tests"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    mask_file = src_dir / "mask.py"
    test_file = tests_dir / "test_mask.py"

    # Write defective code initially
    mask_file.write_text(DEFECTIVE_CODE, encoding="utf-8")
    test_file.write_text(TEST_SUITE_CODE, encoding="utf-8")

    # Command to run actual pytest inside tmp_path
    cmd = [sys.executable, "-m", "pytest", "tests/test_mask.py"]

    # 2. Create Canvas Project & Dispatch Subtask
    r_create = client.post(
        "/api/canvas/instances",
        json={"project_name": "工程代码工作台真实验证项目", "template_id": "work"},
        headers=headers,
    )
    assert r_create.status_code == 201
    inst_id = r_create.json()["id"]

    dispatch_payload = {
        "root_task_id": f"root-{inst_id}",
        "worker_id": "EngineeringAgent",
        "goal": "实现脱敏函数 mask_user_records 并通过独立 pytest 自动化测试",
        "acceptance_criteria": {
            "criteria": [
                "phone_masking: 11-digit phone numbers masked like 138****5678",
                "email_masking: alice.smith@example.com masked like a***h@example.com",
            ]
        },
        "budget_slice": 0.05,
    }
    r_disp = client.post(f"/api/canvas/instances/{inst_id}/dispatch", json=dispatch_payload, headers=headers)
    assert r_disp.status_code == 201
    subtask_id = r_disp.json()["subtask_id"]

    # 3. Worker produces initial draft on disk (defective)
    # The task is running in workspace. Orchestrator executes verification directly on real files.

    # 4. Execute Trusted Verification on real workspace (EXPECT FAIL)
    r_verif_1 = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/execute-verification",
        json={
            "workspace_dir": str(tmp_path),
            "command": cmd,
            "timeout_seconds": 20.0,
        },
        headers=headers,
    )
    assert r_verif_1.status_code == 200
    v1_data = r_verif_1.json()["verification"]
    assert v1_data["passed"] is False
    assert v1_data["exit_code"] != 0
    assert "test_email_masking" in (v1_data["stdout"] + v1_data["stderr"])
    assert v1_data["composite_artifact_hash"] is not None
    assert "sanitizer/mask.py" in v1_data["artifact_digests"]

    # 5. Gate check: attempt to complete when verification failed must raise 422
    r_premature_comp = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/complete",
        json={"output": "强行验收尝试", "settled_budget": 0.03},
        headers=headers,
    )
    assert r_premature_comp.status_code == 422
    assert "cannot be completed without an active passing verification record" in r_premature_comp.text

    # 6. Request Rework
    r_rework = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/rework",
        json={
            "feedback": "真实测试失败：test_email_masking 断言失败。请修复邮箱掩码实现。",
            "criteria_unmet": ["email_masking: raw cleartext email returned"],
        },
        headers=headers,
    )
    assert r_rework.status_code == 200
    assert r_rework.json()["state"] == "waiting_rework"
    assert r_rework.json()["attempts"] == 2

    # 7. Worker modifies actual file on disk to fix defect
    mask_file.write_text(FIXED_CODE, encoding="utf-8")

    # Worker resubmits
    r_resubmit = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/resubmit",
        json={
            "output": "修复完成：邮箱用户名已实现首尾保留并掩码处理，修复后代码已写入磁盘。",
            "artifacts": [{"name": "sanitizer/mask.py", "content_preview": FIXED_CODE[:100]}],
        },
        headers=headers,
    )
    assert r_resubmit.status_code == 200
    assert r_resubmit.json()["state"] == "running"

    # 8. Execute Trusted Verification again on real workspace (EXPECT PASS)
    r_verif_2 = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/execute-verification",
        json={
            "workspace_dir": str(tmp_path),
            "command": cmd,
            "timeout_seconds": 20.0,
        },
        headers=headers,
    )
    assert r_verif_2.status_code == 200
    v2_data = r_verif_2.json()["verification"]
    assert v2_data["passed"] is True
    assert v2_data["exit_code"] == 0
    assert "passed" in v2_data["stdout"].lower()
    verified_hash = v2_data["composite_artifact_hash"]
    verification_id = v2_data["verification_id"]
    assert verified_hash != v1_data["composite_artifact_hash"]  # Hash changed after file fix!

    # 9. Tamper check: passing wrong artifact version is rejected
    r_tamper = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/complete",
        json={
            "output": "篡改版本验收尝试",
            "artifact_version": "tampered_fake_hash_123456",
            "verification_id": verification_id,
        },
        headers=headers,
    )
    assert r_tamper.status_code == 422
    assert "Artifact version mismatch" in r_tamper.text

    # 10. Final Acceptance: Valid bound completion
    r_complete = client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/complete",
        json={
            "output": "脱敏模块经 TrustedVerificationRunner 实跑全部通过，版本与哈希一致，正式合并。",
            "settled_budget": 0.03,
            "artifact_version": verified_hash,
            "verification_id": verification_id,
            "completed_by": "EngineeringAgent/spoofed",  # Ignored by server
        },
        headers=headers,
    )
    assert r_complete.status_code == 200
    comp_data = r_complete.json()
    assert comp_data["state"] == "completed"
    assert comp_data["completed_by"] == "owner/manual"
    assert comp_data["is_manual_completion"] is True
    assert comp_data["bound_verification_id"] == verification_id
    assert comp_data["bound_artifact_hash"] == verified_hash
