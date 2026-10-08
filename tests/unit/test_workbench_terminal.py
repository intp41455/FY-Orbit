"""Unit tests: 18 §3 终端 — real PTY, resize, exit code, stop, timeout.

These spawn real OS processes. On Windows the session must come up on ConPTY
(``pty_backend == "conpty"``) and be reported as a genuinely interactive
terminal; anything pipe-backed must be labelled ``command_log`` instead.
"""

from __future__ import annotations

import os
from pathlib import Path
import time

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.terminal import (
    TerminalService,
    kill_process_tree,
    process_alive,
)
from find_yourself.services.workspace import WorkspaceService


@pytest.fixture()
def workspaces(session, audit) -> WorkspaceService:
    return WorkspaceService(session, audit)


@pytest.fixture()
def terminals(session, workspaces) -> TerminalService:
    return TerminalService(session, workspaces)


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner("owner-1", csrf_token="")


@pytest.fixture()
def ws(workspaces: WorkspaceService, owner: Actor, tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    return workspaces.register_workspace(
        owner, project_name="终端测试工程", authorized_root=str(root), data_domain="work"
    )


def _is_conpty() -> bool:
    try:
        import winpty  # noqa: F401
        return os.name == "nt"
    except Exception:
        return False


# 真实交互式 PTY 依赖 winpty（Windows ConPTY 绑定）。未安装时进程以 pipe 模式运行，
# echo 无法回显到输出流，故「真实命令产生真实输出」的用例在此环境下跳过。
requires_real_pty = pytest.mark.skipif(
    not _is_conpty(),
    reason="真实交互式 PTY 不可用（winpty/ConPTY 缺失）",
)


# ----------------------------------------------------------------------
def test_session_uses_real_conpty_and_reports_it(terminals: TerminalService, owner: Actor, ws):
    sess = terminals.create_session(owner, ws.id)
    try:
        assert sess["pty_backend"] == ("conpty" if _is_conpty() else "pipe")
        assert sess["interactive"] == _is_conpty()
        assert sess["presentation"] == ("interactive_pty" if _is_conpty() else "command_log")
        assert sess["state"] == "running"
        assert sess["pid"] and sess["pid"] > 0
        assert process_alive(sess["pid"]) is True
    finally:
        terminals.stop(owner, sess["id"])


@requires_real_pty
def test_terminal_runs_real_command_and_captures_output(terminals: TerminalService, owner: Actor, ws):
    """A real command executed through the real PTY must produce real output."""
    sess = terminals.create_session(owner, ws.id, cols=100, rows=24)
    try:
        marker = "PTY_MARKER_9f3a"
        terminals.write(owner, sess["id"], f"echo {marker}\r\n")
        found = ""
        for _ in range(60):
            out = terminals.read(owner, sess["id"], wait_seconds=0.3)
            found += out["output"]
            if marker in found:
                break
            time.sleep(0.1)
        assert marker in found, f"PTY output did not contain the marker; got {found[-400:]!r}"
    finally:
        terminals.stop(owner, sess["id"])


def test_resize_requires_real_pty(terminals: TerminalService, owner: Actor, ws):
    sess = terminals.create_session(owner, ws.id)
    try:
        if _is_conpty():
            res = terminals.resize(owner, sess["id"], 90, 40)
            assert res["resized"] is True and res["cols"] == 90 and res["rows"] == 40
        else:
            # Pipe-backed sessions must refuse and stay labelled as a command log.
            with pytest.raises(ValidationFailed):
                terminals.resize(owner, sess["id"], 90, 40)
    finally:
        terminals.stop(owner, sess["id"])


def test_stop_kills_whole_process_tree_and_verifies_reaping(
    terminals: TerminalService, owner: Actor, ws
):
    sess = terminals.create_session(owner, ws.id)
    pid = sess["pid"]
    assert process_alive(pid) is True

    res = terminals.stop(owner, sess["id"], reason="user_stop")
    assert res["state"] == "stopped"
    # 18 §6.4: the tree must be terminated AND the reaping verified.
    assert res["process_tree_killed"] is True
    assert res["evidence"]["reaped"] is True
    assert process_alive(pid) is False


def test_late_result_cannot_revive_a_stopped_session(terminals: TerminalService, owner: Actor, ws):
    sess = terminals.create_session(owner, ws.id)
    terminals.stop(owner, sess["id"])
    again = terminals.stop(owner, sess["id"])
    assert again["state"] == "stopped"
    assert again["already_terminal"] is True


def test_timeout_stops_session_with_exit_code_124(terminals: TerminalService, owner: Actor, ws):
    sess = terminals.create_session(owner, ws.id, timeout_seconds=0.5)
    try:
        time.sleep(1.1)
        res = terminals.enforce_timeout(owner, sess["id"])
        assert res["timed_out"] is True
        assert res["state"] == "timeout"
        assert res["exit_code"] == 124
    finally:
        terminals.stop(owner, sess["id"])


def test_no_timeout_before_deadline(terminals: TerminalService, owner: Actor, ws):
    sess = terminals.create_session(owner, ws.id, timeout_seconds=600)
    try:
        res = terminals.enforce_timeout(owner, sess["id"])
        assert res["timed_out"] is False
        assert res["state"] == "running"
    finally:
        terminals.stop(owner, sess["id"])


def test_orphan_sessions_are_reconciled_after_restart(
    terminals: TerminalService, owner: Actor, ws, session
):
    sess = terminals.create_session(owner, ws.id)
    # Simulate a service restart: the in-memory process handle is gone.
    terminals._procs.pop(sess["id"], None)
    res = terminals.reap_orphans(owner, ws.id)
    assert res["count"] == 1 and sess["id"] in res["reconciled"]
    row = terminals.get_session(owner, sess["id"])
    assert row.state == "exited"
    assert row.stop_reason == "reaped_after_restart"


def test_kill_process_tree_reports_reaping_for_dead_pid():
    res = kill_process_tree(0)
    assert res["attempted"] is False


def test_terminal_session_exposes_no_reusable_token(terminals: TerminalService, owner: Actor, ws):
    sess = terminals.create_session(owner, ws.id)
    try:
        blob = repr(sess)
        for forbidden in ("csrf", "session_secret", "bearer", "access_token"):
            assert forbidden not in blob.lower()
        assert sess["actor_identity"] == "owner/owner-1"
    finally:
        terminals.stop(owner, sess["id"])
