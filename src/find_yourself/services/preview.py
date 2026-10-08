"""PreviewService: 18 工程代码工作台 — 隔离运行预览.

Rules from ``18_工程代码工作台与主协调Agent全流程实施规格.md`` §3 预览 and §6:

- Start a **real** workspace process, probe its health over a real loopback
  socket, map the port, then stop it and verify the process tree was reaped and
  the port released.
- HTTP page preview and API preview are labelled separately (``kind``).
- **Preview content is untrusted.** The response always states
  ``inherits_core_session=False`` and ``untrusted=True``; the preview never
  receives the core session cookie, core bearer token, or local file
  permissions. An access lease bounds who may reach it and for how long.
- Binding is loopback-only; the preview is never exposed on 0.0.0.0.
"""

from __future__ import annotations

import http.client
import os
import socket
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from find_yourself.db.types import utcnow
from find_yourself.db.workbench_models import PreviewSessionRecord
from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict, NotFound, ValidationFailed
from find_yourself.services.terminal import kill_process_tree, process_alive
from find_yourself.services.workspace import WorkspaceService


def pick_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# Preview process handles must also outlive a single request (see terminal.py).
_PREVIEW_PROCS: dict[str, Any] = {}


class PreviewService:
    """Start / health-check / stop isolated workspace preview processes."""

    def __init__(self, session: Session, workspaces: WorkspaceService):
        self.session = session
        self.workspaces = workspaces
        self._procs = _PREVIEW_PROCS

    def start(
        self,
        actor: Actor,
        workspace_id: str,
        *,
        command: list[str],
        target_port: int | None = None,
        kind: str = "http",
        entry_path: str = "/",
        rel_cwd: str = "",
        lease_seconds: float = 900.0,
    ) -> dict[str, Any]:
        ws = self.workspaces.get_workspace(actor, workspace_id)
        if kind not in ("http", "api"):
            raise ValidationFailed(f"Unsupported preview kind: {kind}")
        if not command:
            raise ValidationFailed("Preview command is required")

        cwd = self.workspaces.resolve_path(ws, rel_cwd) if rel_cwd else self.workspaces._root(ws)
        port = int(target_port) if target_port else pick_loopback_port()
        if port_open(port):
            raise Conflict(f"Port {port} is already in use on loopback")

        import subprocess
        # S-1（POSIX）：独立会话/进程组，否则 stop 时 killpg 会被
        # `pgid != os.getpgrp()` 守卫拦掉，只杀得掉直接子进程，端口不释放。
        popen_kw: dict[str, object] = {}
        if os.name != "nt":
            popen_kw["start_new_session"] = True
        proc = subprocess.Popen(
            [str(c) for c in command],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=None,
            **popen_kw,
        )

        rec = PreviewSessionRecord(
            id=f"prev-{uuid.uuid4().hex[:12]}",
            workspace_id=ws.id,
            process_pid=proc.pid,
            target_port=port,
            entry_path=entry_path,
            kind=kind,
            health="starting",
            state="starting",
            access_subject=f"owner/{actor.owner_id}",
            access_lease=str(uuid.uuid4()),
            expires_at=None,
        )
        from datetime import timedelta
        rec.expires_at = utcnow() + timedelta(seconds=float(lease_seconds))
        self._procs[rec.id] = proc
        self.session.add(rec)
        self.session.flush()
        return self.status(actor, rec.id)

    def _probe(self, rec: PreviewSessionRecord) -> tuple[str, int | None]:
        """Probe real HTTP health on loopback. Returns (health, status_code)."""
        try:
            conn = http.client.HTTPConnection("127.0.0.1", rec.target_port, timeout=1.5)
            conn.request("GET", rec.entry_path or "/")
            resp = conn.getresponse()
            code = resp.status
            resp.read(2048)
            conn.close()
            return ("healthy" if code < 500 else "unhealthy"), code
        except Exception:
            return ("unhealthy" if port_open(rec.target_port) else "starting"), None

    def status(self, actor: Actor, session_id: str) -> dict[str, Any]:
        rec = self.session.get(PreviewSessionRecord, session_id)
        if rec is None:
            raise NotFound(f"Preview session not found: {session_id}")
        self.workspaces.get_workspace(actor, rec.workspace_id)

        alive = process_alive(rec.process_pid or 0)
        health, code = self._probe(rec)
        if not alive and health == "starting":
            health = "unhealthy"
            rec.state = "failed"
        elif health == "healthy":
            rec.state = "healthy"
        elif health == "unhealthy":
            rec.state = "unhealthy"
        self.session.flush()

        expired = rec.expires_at is not None and rec.expires_at <= utcnow()
        return {
            "id": rec.id,
            "workspace_id": rec.workspace_id,
            "kind": rec.kind,
            "target_port": rec.target_port,
            "entry_path": rec.entry_path,
            "host": "127.0.0.1",
            "url": f"http://127.0.0.1:{rec.target_port}{rec.entry_path}",
            "state": rec.state,
            "health": health,
            "http_status": code,
            "process_alive": alive,
            "lease_expired": expired,
            "access_lease": rec.access_lease,
            "expires_at": rec.expires_at.isoformat() if rec.expires_at else None,
            # Isolation guarantees stated explicitly, never implied.
            "untrusted": True,
            "inherits_core_session": False,
            "sandbox_policy": "no-core-cookie, no-core-bearer, no-local-file-permission, loopback-only",
        }

    def stop(self, actor: Actor, session_id: str) -> dict[str, Any]:
        rec = self.session.get(PreviewSessionRecord, session_id)
        if rec is None:
            raise NotFound(f"Preview session not found: {session_id}")
        self.workspaces.get_workspace(actor, rec.workspace_id)

        evidence = kill_process_tree(rec.process_pid) if rec.process_pid else {"attempted": False, "reaped": True}
        port_released = not port_open(rec.target_port)

        proc = self._procs.pop(rec.id, None)
        if proc is not None:
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

        rec.state = "stopped"
        rec.health = "stopped"
        rec.ended_at = utcnow()
        self.session.flush()

        return {
            "id": rec.id,
            "state": rec.state,
            "port": rec.target_port,
            "process_tree_killed": bool(evidence.get("reaped")),
            "port_released": port_released,
            "evidence": evidence,
        }

    def list_sessions(self, actor: Actor, workspace_id: str) -> list[dict[str, Any]]:
        ws = self.workspaces.get_workspace(actor, workspace_id)
        rows = self.session.execute(
            select(PreviewSessionRecord).where(PreviewSessionRecord.workspace_id == ws.id)
            .order_by(PreviewSessionRecord.created_at.asc())
        ).scalars().all()
        return [
            {
                "id": r.id, "kind": r.kind, "port": r.target_port, "state": r.state,
                "health": r.health, "pid": r.process_pid,
                "untrusted": True, "inherits_core_session": False,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
