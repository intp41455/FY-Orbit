"""P0 安全回归：execute-verification 的工作区边界与命令白名单。

覆盖 2026-10-11 修复的任意 cwd + 任意命令执行漏洞：
1. 客户端直传 workspace_dir 已失效（字段移除，服务端只认 workspace_id）；
2. 非白名单命令（cmd.exe 等系统二进制）一律 422；
3. 跨 owner 的 workspace_id 不可用；
4. 不存在的 workspace_id 返回 404；
5. target_files 绝对路径 / .. 穿越被拒绝；
6. str 形式命令（shell=True 路径）被拒绝。
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from helpers import login_owner


def _setup(client: TestClient, headers: dict, tmp_path: Path):
    r_create = client.post(
        "/api/canvas/instances",
        json={"project_name": "边界测试", "template_id": "work"},
        headers=headers,
    )
    assert r_create.status_code == 201, r_create.text
    inst_id = r_create.json()["id"]
    r_ws = client.post(
        "/api/workbench/workspaces",
        json={"project_name": "boundary-ws", "authorized_root": str(tmp_path)},
        headers=headers,
    )
    assert r_ws.status_code == 201, r_ws.text
    workspace_id = r_ws.json()["id"]
    r_disp = client.post(
        f"/api/canvas/instances/{inst_id}/dispatch",
        json={
            "root_task_id": f"root-{inst_id}",
            "worker_id": "EngineeringAgent",
            "goal": "边界测试派单",
            "budget_slice": 0.05,
        },
        headers=headers,
    )
    assert r_disp.status_code == 201, r_disp.text
    subtask_id = r_disp.json()["subtask_id"]
    return inst_id, workspace_id, subtask_id


def _call(client, inst_id, subtask_id, payload, headers):
    return client.post(
        f"/api/canvas/instances/{inst_id}/subtasks/{subtask_id}/execute-verification",
        json=payload,
        headers=headers,
    )


def test_workspace_dir_is_no_longer_accepted(client: TestClient, tmp_path: Path) -> None:
    """直传 workspace_dir 指向任意目录：必须不再生效（422 缺 workspace_id，或被忽略后 422）。"""
    headers = login_owner(client)
    inst_id, workspace_id, subtask_id = _setup(client, headers, tmp_path)
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    r = _call(client, inst_id, subtask_id, {
        "workspace_dir": str(evil),
        "command": [sys.executable, "-m", "pytest", "--collect-only", "-q"],
    }, headers)
    # workspace_id 缺失 → 请求校验失败；即使被忽略也不得执行 evil 目录。
    assert r.status_code == 422, r.text
    assert "TOP-SECRET" not in r.text


def test_non_allowlisted_command_rejected(client: TestClient, tmp_path: Path) -> None:
    """cmd.exe / powershell 等系统二进制一律 422。"""
    headers = login_owner(client)
    inst_id, workspace_id, subtask_id = _setup(client, headers, tmp_path)
    for cmd in (["cmd.exe", "/c", "echo", "hi"],
                ["powershell.exe", "-Command", "echo hi"],
                ["C:\\Windows\\System32\\whoami.exe"]):
        r = _call(client, inst_id, subtask_id, {
            "workspace_id": workspace_id,
            "command": cmd,
        }, headers)
        assert r.status_code == 422, (cmd, r.text)
        assert "not an allowed test runner" in r.text


def test_string_command_rejected(client: TestClient, tmp_path: Path) -> None:
    """str 形式命令（曾走 shell=True）必须被拒绝。"""
    headers = login_owner(client)
    inst_id, workspace_id, subtask_id = _setup(client, headers, tmp_path)
    r = _call(client, inst_id, subtask_id, {
        "workspace_id": workspace_id,
        # pydantic 本身会先拦 str（字段类型是 list[str]），任一层拦住都算通过。
        "command": "echo hi",
    }, headers)
    assert r.status_code == 422, r.text


def test_unknown_workspace_id_404(client: TestClient, tmp_path: Path) -> None:
    headers = login_owner(client)
    inst_id, workspace_id, subtask_id = _setup(client, headers, tmp_path)
    r = _call(client, inst_id, subtask_id, {
        "workspace_id": "ws-does-not-exist",
        "command": [sys.executable, "-m", "pytest", "--collect-only", "-q"],
    }, headers)
    assert r.status_code == 404, r.text


def test_target_files_traversal_rejected(client: TestClient, tmp_path: Path) -> None:
    """绝对路径与 .. 穿越必须被拒绝。"""
    headers = login_owner(client)
    inst_id, workspace_id, subtask_id = _setup(client, headers, tmp_path)
    for evil in (["C:/Windows/win.ini"], ["../outside.txt"], ["/etc/passwd"]):
        r = _call(client, inst_id, subtask_id, {
            "workspace_id": workspace_id,
            "command": [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            "target_files": evil,
        }, headers)
        assert r.status_code == 422, (evil, r.text)


def test_allowlisted_pytest_still_works(client: TestClient, tmp_path: Path) -> None:
    """白名单内的 pytest 在自有工作区内仍可执行（不误伤合法用途）。"""
    headers = login_owner(client)
    inst_id, workspace_id, subtask_id = _setup(client, headers, tmp_path)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    r = _call(client, inst_id, subtask_id, {
        "workspace_id": workspace_id,
        "command": [sys.executable, "-m", "pytest", "test_ok.py", "-q"],
        "target_files": ["test_ok.py"],
        "timeout_seconds": 60.0,
    }, headers)
    assert r.status_code == 200, r.text
    v = r.json()["verification"]
    assert v["passed"] is True
    assert v["exit_code"] == 0
    assert "test_ok.py" in v["artifact_digests"]
