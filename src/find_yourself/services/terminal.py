"""TerminalService: 18 工程代码工作台 — 真实 PTY 交互终端.

Requirements implemented (18 §3 终端, §5 TerminalSession, §6 第 4 项):

- **Real PTY I/O.** On Windows the session is backed by ConPTY through
  ``pywinpty`` (``PtyProcess``). The backend actually used is recorded on the
  session, and when only a pipe is available the session is honestly labelled
  ``command_log`` — it is never presented as a resizable interactive terminal.
- **Resize, exit code, timeout, stop.** ``resize`` calls ConPTY
  ``setwinsize``; ``stop`` terminates the **whole process tree** and verifies
  the child was actually reaped before reporting success.
- **Session identity.** Output never carries reusable login tokens; the session
  row stores the actor identity and workspace only.
- **Timeout.** A session that outruns its ``timeout_seconds`` is stopped and
  marked ``timeout`` (exit code 124 by convention), not left running.

Process-tree termination is verified rather than assumed: after issuing the
kill we poll the OS to confirm the PID is gone (18 §6.4).
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from find_yourself.db.types import utcnow
from find_yourself.db.workbench_models import TerminalSessionRecord
from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict, NotFound, ValidationFailed
from find_yourself.services.workspace import WorkspaceService

MAX_BUFFER_CHARS = 200_000
DEFAULT_TIMEOUT_SECONDS = 300.0

# Process handles must outlive a single HTTP request: ``get_services`` builds a
# fresh TerminalService per request, so live PTY handles and their output
# buffers live in this module-level registry keyed by session id.
_PROCESS_REGISTRY: dict[str, Any] = {}
_BUFFER_REGISTRY: dict[str, str] = {}
_STOP_EVENTS: dict[str, Any] = {}
_REGISTRY_LOCK = threading.RLock()


def _conpty_available() -> bool:
    try:
        import winpty  # noqa: F401
        return os.name == "nt"
    except Exception:
        return False


def process_alive(pid: int) -> bool:
    """OS-level liveness check used to verify a process tree was really reaped."""
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        try:
            # Read raw bytes: tasklist output is locale-encoded (GBK on a Chinese
            # Windows), so decoding as UTF-8 would raise and hide a live process.
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, timeout=10, check=False,
            )
            return str(pid).encode("ascii") in out.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # POSIX：已被 SIGKILL 但父进程尚未 wait() 的子进程处于**僵尸态**，
    # 此时 os.kill(pid, 0) 依然成功 —— 会被误判为「仍存活」，导致
    # kill_process_tree 的 reaped 验证永远为 False（Linux CI 实证）。
    # 僵尸不占用 CPU/内存，只占一个表项，语义上应视为已回收。
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            # 格式：pid (comm) state ...；comm 可能含空格/括号，故取最后一个 ')' 之后。
            fields = fh.read().rsplit(b")", 1)[-1].split()
        if fields and fields[0] == b"Z":
            return False
    except OSError:
        pass
    return True


def kill_process_tree(pid: int) -> dict[str, Any]:
    """Terminate a process tree and verify it was reaped.

    Returns an evidence dict: ``{"attempted": True, "tree_signal": ..., "reaped": bool}``.
    A single ``proc.kill()`` on the leader is not enough — the tree must be
    terminated (18 §6.4 "需进程树终止与回收验证").
    """
    result: dict[str, Any] = {"attempted": True, "pid": pid, "tree_signal": None, "reaped": False}
    if pid is None or pid <= 0:
        result["attempted"] = False
        return result

    if os.name == "nt":
        result["tree_signal"] = "taskkill /F /T"
        # Bytes mode (no text=True): taskkill output is GBK on a Chinese Windows;
        # decoding as UTF-8 would raise UnicodeDecodeError in a background reader
        # thread. The output is unused here, so bytes are fine.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=20, check=False,
        )
    else:
        result["tree_signal"] = "SIGKILL process group"
        try:
            pgid = os.getpgid(pid)
            if pgid != os.getpgrp():
                os.killpg(pgid, 9)
            else:
                os.kill(pid, 9)
        except Exception:
            try:
                os.kill(pid, 9)
            except Exception:
                pass

    for _ in range(40):
        if not process_alive(pid):
            result["reaped"] = True
            break
        time.sleep(0.05)

    if not result["reaped"] and os.name == "nt":
        # Belt and braces: some trees survive the first signal.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=20, check=False,
        )
        for _ in range(20):
            if not process_alive(pid):
                result["reaped"] = True
                break
            time.sleep(0.05)

    return result


class TerminalService:
    """Real PTY terminal sessions bound to an authorized workspace."""

    def __init__(self, session: Session, workspaces: WorkspaceService):
        self.session = session
        self.workspaces = workspaces
        self._procs = _PROCESS_REGISTRY
        self._buffers = _BUFFER_REGISTRY
        self._threads: dict[str, threading.Thread] = {}
        self._stop_events = _STOP_EVENTS
        self._lock = _REGISTRY_LOCK

    def _reader_loop(self, session_id: str, proc: Any, backend: str) -> None:
        """Continuously drain the PTY/pipe into the session buffer.

        ConPTY's ``read()`` blocks until data arrives, so output is collected on
        a daemon thread and ``read()`` simply returns whatever has accumulated.
        """
        ev = self._stop_events.get(session_id)
        while ev is None or not ev.is_set():
            try:
                if backend == "conpty":
                    chunk = proc.read()
                else:
                    import select as _select
                    if proc.stdout is None:
                        break
                    if _select.select([proc.stdout], [], [], 0.2)[0]:
                        raw = proc.stdout.read1(8192)
                        chunk = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                    else:
                        if proc.poll() is not None:
                            break
                        continue
            except (EOFError, OSError, ValueError):
                break
            except Exception:
                break
            if chunk:
                with self._lock:
                    buf = self._buffers.get(session_id, "") + chunk
                    self._buffers[session_id] = buf[-MAX_BUFFER_CHARS:]
            elif backend != "conpty" and proc.poll() is not None:
                break

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def create_session(
        self,
        actor: Actor,
        workspace_id: str,
        *,
        shell: str | None = None,
        cols: int = 120,
        rows: int = 30,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        rel_cwd: str = "",
    ) -> dict[str, Any]:
        ws = self.workspaces.get_workspace(actor, workspace_id)
        if timeout_seconds <= 0:
            raise ValidationFailed("timeout_seconds must be positive")
        cols = max(20, min(int(cols), 400))
        rows = max(5, min(int(rows), 200))

        cwd = self.workspaces.resolve_path(ws, rel_cwd) if rel_cwd else self.workspaces._root(ws)
        if not cwd.is_dir():
            raise ValidationFailed(f"Working directory is not a directory: {rel_cwd}")

        actor_label = f"service/{actor.service_id}" if actor.service_id else f"owner/{actor.owner_id}"
        use_conpty = _conpty_available()
        backend = "conpty" if use_conpty else "pipe"
        shell_cmd = shell or ("cmd.exe" if os.name == "nt" else "/bin/bash")

        rec = TerminalSessionRecord(
            id=f"term-{uuid4().hex[:12]}",
            workspace_id=ws.id,
            actor_identity=actor_label,
            shell=shell_cmd,
            pty_backend=backend,
            cols=cols,
            rows=rows,
            state="created",
            timeout_seconds=float(timeout_seconds),
        )

        if use_conpty:
            from winpty import PtyProcess
            proc = PtyProcess.spawn(shell_cmd, cwd=str(cwd), dimensions=(rows, cols))
            rec.pid = proc.pid
            self._procs[rec.id] = proc
        else:
            # Bytes mode (not text=True): on a Chinese Windows the shell emits GBK,
            # and decoding as UTF-8 in the background reader thread raises
            # UnicodeDecodeError. We decode explicitly with errors="replace" below.
            popen_kw: dict[str, Any] = {}
            if os.name != "nt":
                popen_kw["start_new_session"] = True
            proc = subprocess.Popen(
                [shell_cmd], cwd=str(cwd), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
                **popen_kw,
            )
            rec.pid = proc.pid
            self._procs[rec.id] = proc

        rec.state = "running"
        self._buffers[rec.id] = ""
        self._stop_events[rec.id] = threading.Event()
        self.session.add(rec)
        self.session.flush()

        t = threading.Thread(
            target=self._reader_loop, args=(rec.id, proc, backend),
            name=f"pty-reader-{rec.id}", daemon=True,
        )
        self._threads[rec.id] = t
        t.start()
        return self.describe(actor, rec.id)

    def get_session(self, actor: Actor, session_id: str) -> TerminalSessionRecord:
        rec = self.session.get(TerminalSessionRecord, session_id)
        if rec is None:
            raise NotFound(f"Terminal session not found: {session_id}")
        # Workspace membership check enforces the owner boundary.
        self.workspaces.get_workspace(actor, rec.workspace_id)
        return rec

    def describe(self, actor: Actor, session_id: str) -> dict[str, Any]:
        rec = self.get_session(actor, session_id)
        pty_backend = rec.pty_backend
        return {
            "id": rec.id,
            "workspace_id": rec.workspace_id,
            "actor_identity": rec.actor_identity,
            "shell": rec.shell,
            "pid": rec.pid,
            "cols": rec.cols,
            "rows": rec.rows,
            "state": rec.state,
            "pty_backend": pty_backend,
            "interactive": pty_backend == "conpty",
            "presentation": ("interactive_pty" if pty_backend == "conpty" else "command_log"),
            "exit_code": rec.exit_code,
            "stop_reason": rec.stop_reason,
            "timeout_seconds": rec.timeout_seconds,
            "cursor": rec.event_cursor,
            "created_at": rec.created_at.isoformat(),
            "ended_at": rec.ended_at.isoformat() if rec.ended_at else None,
        }

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------
    def write(self, actor: Actor, session_id: str, data: str) -> dict[str, Any]:
        rec = self.get_session(actor, session_id)
        if rec.state != "running":
            raise Conflict(f"Terminal session is not running (state={rec.state})")
        proc = self._procs.get(session_id)
        if proc is None:
            raise NotFound(f"Terminal process not attached: {session_id}")
        try:
            if rec.pty_backend == "conpty":
                proc.write(data)
            else:
                if proc.stdin is None:
                    raise ValidationFailed("Pipe-backed session has no stdin")
                proc.stdin.write(data.encode("utf-8", "replace"))
                proc.stdin.flush()
        except Exception as exc:
            raise ValidationFailed(f"Failed to write to terminal: {exc}") from exc
        return {"id": rec.id, "written": len(data)}

    def read(self, actor: Actor, session_id: str, *, wait_seconds: float = 0.5) -> dict[str, Any]:
        """Return output accumulated since the committed cursor.

        The reader thread does the blocking; this only waits politely.
        """
        rec = self.get_session(actor, session_id)
        proc = self._procs.get(session_id)

        deadline = time.time() + max(0.0, wait_seconds)
        while time.time() < deadline:
            with self._lock:
                have = len(self._buffers.get(session_id, ""))
            if have > rec.event_cursor:
                break
            time.sleep(0.05)

        self._refresh_state(rec, proc)

        with self._lock:
            buf = self._buffers.get(session_id, "")
        cursor = rec.event_cursor
        tail = buf[cursor:]
        return {
            "id": rec.id,
            "output": tail,
            "cursor": cursor,
            "new_cursor": len(buf),
            "state": rec.state,
            "exit_code": rec.exit_code,
            "interactive": rec.pty_backend == "conpty",
        }

    def commit_cursor(self, actor: Actor, session_id: str, cursor: int) -> dict[str, Any]:
        rec = self.get_session(actor, session_id)
        rec.event_cursor = max(0, int(cursor))
        self.session.flush()
        return {"id": rec.id, "cursor": rec.event_cursor}

    def resize(self, actor: Actor, session_id: str, cols: int, rows: int) -> dict[str, Any]:
        rec = self.get_session(actor, session_id)
        if rec.pty_backend != "conpty":
            raise ValidationFailed(
                "Resize requires a real PTY session; this session is pipe-backed and "
                "is presented as a command log only."
            )
        cols = max(20, min(int(cols), 400))
        rows = max(5, min(int(rows), 200))
        proc = self._procs.get(session_id)
        if proc is None:
            raise Conflict("Terminal process is not attached; cannot resize")
        proc.setwinsize(rows, cols)
        rec.cols, rec.rows = cols, rows
        self.session.flush()
        return {"id": rec.id, "cols": cols, "rows": rows, "resized": True}

    # ------------------------------------------------------------------
    # Stop / timeout
    # ------------------------------------------------------------------
    def _refresh_state(self, rec: TerminalSessionRecord, proc: Any) -> None:
        if rec.state in ("exited", "stopped", "timeout", "failed"):
            return
        alive = False
        if proc is not None:
            try:
                alive = proc.isalive() if hasattr(proc, "isalive") else (proc.poll() is None)
            except Exception:
                alive = False
        if not alive:
            code = None
            try:
                code = proc.exitstatus if hasattr(proc, "exitstatus") else proc.returncode
            except Exception:
                code = None
            rec.state = "exited"
            rec.exit_code = code
            rec.ended_at = utcnow()
            self.session.flush()
            self._release(rec.id)

    def _release(self, session_id: str) -> None:
        ev = self._stop_events.pop(session_id, None)
        if ev is not None:
            ev.set()
        self._procs.pop(session_id, None)
        self._threads.pop(session_id, None)

    def stop(self, actor: Actor, session_id: str, reason: str = "user_stop") -> dict[str, Any]:
        rec = self.get_session(actor, session_id)
        evidence = kill_process_tree(rec.pid) if rec.pid else {"attempted": False, "reaped": True}

        # Late results must never flip a stopped session back to a success state.
        if rec.state in ("exited", "stopped", "timeout", "failed"):
            return {
                "id": rec.id, "state": rec.state, "exit_code": rec.exit_code,
                "process_tree_killed": bool(evidence.get("reaped")),
                "already_terminal": True, "evidence": evidence,
            }

        rec.state = "stopped" if reason != "timeout" else "timeout"
        rec.stop_reason = reason
        rec.ended_at = utcnow()
        rec.exit_code = 124 if reason == "timeout" else rec.exit_code
        self.session.flush()
        self._release(rec.id)

        return {
            "id": rec.id,
            "state": rec.state,
            "exit_code": rec.exit_code,
            "stop_reason": reason,
            "process_tree_killed": bool(evidence.get("reaped")),
            "pid": rec.pid,
            "evidence": evidence,
        }

    def enforce_timeout(self, actor: Actor, session_id: str, *, now: float | None = None) -> dict[str, Any]:
        rec = self.get_session(actor, session_id)
        if rec.state != "running":
            return {"id": rec.id, "state": rec.state, "timed_out": False}
        elapsed = (utcnow() - rec.created_at).total_seconds()
        if elapsed > float(rec.timeout_seconds):
            res = self.stop(actor, session_id, reason="timeout")
            res["timed_out"] = True
            res["elapsed_seconds"] = round(elapsed, 3)
            return res
        return {"id": rec.id, "state": rec.state, "timed_out": False,
                "elapsed_seconds": round(elapsed, 3)}

    def list_sessions(self, actor: Actor, workspace_id: str) -> list[dict[str, Any]]:
        ws = self.workspaces.get_workspace(actor, workspace_id)
        rows = self.session.execute(
            select(TerminalSessionRecord).where(TerminalSessionRecord.workspace_id == ws.id)
            .order_by(TerminalSessionRecord.created_at.asc())
        ).scalars().all()
        return [
            {
                "id": r.id, "shell": r.shell, "state": r.state, "pid": r.pid,
                "pty_backend": r.pty_backend,
                "interactive": r.pty_backend == "conpty",
                "presentation": ("interactive_pty" if r.pty_backend == "conpty" else "command_log"),
                "exit_code": r.exit_code, "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    def reap_orphans(self, actor: Actor, workspace_id: str) -> dict[str, Any]:
        """Reconcile records whose OS process is gone (e.g. after a restart)."""
        ws = self.workspaces.get_workspace(actor, workspace_id)
        rows = self.session.execute(
            select(TerminalSessionRecord).where(
                TerminalSessionRecord.workspace_id == ws.id,
                TerminalSessionRecord.state == "running",
            )
        ).scalars().all()
        reconciled: list[str] = []
        for r in rows:
            if self._procs.get(r.id) is None:
                r.state = "exited"
                r.stop_reason = "reaped_after_restart"
                r.ended_at = utcnow()
                reconciled.append(r.id)
        self.session.flush()
        return {"workspace_id": ws.id, "reconciled": reconciled, "count": len(reconciled)}
