"""PreviewSourceService: P1-A 实时预览窗 — 统一预览源注册协议.

Layers (per the approved P1-A task):

- Layer 1 (``static``): a workspace-relative ``.html/.htm/.md/.markdown`` file
  becomes a preview source. Content is served through a dedicated **read-only**
  endpoint that always answers with ``Content-Security-Policy: sandbox
  allow-scripts`` and ``X-Content-Type-Options: nosniff``. The absolute
  filesystem path never leaves the server — only the workspace-relative path
  and the opaque source id.
- Layer 3 (``process``): delegates to the existing PreviewService (18 §3
  隔离运行预览); the registry records the preview session id and returns its
  loopback access address. Registration/stopping stays with PreviewService.
- Layer 2 (hot refresh) is versioned: the static source version is a SHA-256
  digest of ``(mtime_ns, size)`` recomputed on every version probe, so the
  frontend can poll ``GET .../preview-sources/{id}/version`` and reload the
  iframe only when the content really changed.

Honesty rule: a missing/unreadable source raises a typed error that surfaces as
an explicit failure — the protocol never fakes a renderable preview.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from find_yourself.db.types import utcnow
from find_yourself.db.workbench_models import PreviewSourceRecord
from find_yourself.services.actor import Actor
from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.preview import PreviewService
from find_yourself.services.workspace import WorkspaceService

# Extension → served media type. Markdown is intentionally served as
# ``text/plain`` (raw text + explicit "not rendered yet" labelling upstream) —
# v1 does not pretend to render markdown it does not render.
STATIC_MEDIA_TYPES: dict[str, str] = {
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/plain",
    ".markdown": "text/plain",
}

_CSP_SANDBOX = "sandbox allow-scripts"


def compute_file_version(st: Any) -> str:
    """Version = SHA-256 of (mtime_ns, size) — cheap, monotonic enough for polling."""
    basis = f"{st.st_mtime_ns}:{st.st_size}".encode("utf-8")
    return hashlib.sha256(basis).hexdigest()[:32]


class PreviewSourceService:
    """Register / list / version / read / unregister unified preview sources."""

    def __init__(
        self,
        session: Session,
        workspaces: WorkspaceService,
        previews: PreviewService,
        audit=None,
    ):
        self.session = session
        self.workspaces = workspaces
        self.previews = previews
        self.audit = audit

    # ------------------------------------------------------------------
    def _audit(self, actor: Actor, action: str, target: str, details: dict[str, Any]) -> None:
        if self.audit is None:
            return
        try:
            self.audit.append(actor, action, target, details)
        except Exception:
            # Auditing must never mask the primary operation result.
            pass

    def _get_source(self, actor: Actor, source_id: str) -> PreviewSourceRecord:
        rec = self.session.get(PreviewSourceRecord, source_id)
        if rec is None:
            raise NotFound(f"Preview source not found: {source_id}")
        # Ownership flows through the workspace boundary (same as terminal/git).
        self.workspaces.get_workspace(actor, rec.workspace_id)
        return rec

    def _serialize(self, rec: PreviewSourceRecord, *, url: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": rec.id,
            "workspace_id": rec.workspace_id,
            "kind": rec.kind,
            "path": rec.rel_path,  # workspace-relative only; never absolute
            "media_type": rec.media_type,
            "version": rec.version,
            "state": rec.state,
            "created_at": rec.created_at.isoformat(),
        }
        if rec.kind == "static":
            data["content_url"] = f"/api/workbench/preview-sources/{rec.id}/content"
        else:
            data["preview_session_id"] = rec.preview_session_id
            data["url"] = url
        return data

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_static(self, actor: Actor, workspace_id: str, *, rel_path: str) -> dict[str, Any]:
        actor.require_owner()
        ws = self.workspaces.get_workspace(actor, workspace_id)

        raw = (rel_path or "").strip()
        if not raw:
            raise ValidationFailed("Preview source path is required")
        # resolve_path enforces the full sandbox: absolute/drive/UNC rejection,
        # ``..`` traversal rejection, symlink/junction escape rejection — the
        # same boundary every other workspace file access goes through.
        path = self.workspaces.resolve_path(ws, raw)
        if not path.is_file():
            raise ValidationFailed(f"Preview source is not a file inside the workspace: {raw}")

        media_type = STATIC_MEDIA_TYPES.get(path.suffix.lower())
        if media_type is None:
            raise ValidationFailed(
                f"Unsupported static preview type {path.suffix!r}; "
                "only .html/.htm/.md/.markdown can be registered (v1)"
            )

        version = compute_file_version(path.stat())
        # Store the *normalized* workspace-relative posix path (symlinks already
        # resolved by resolve_path) — never the absolute filesystem location.
        rel_norm = path.relative_to(self.workspaces._root(ws)).as_posix()
        rec = PreviewSourceRecord(
            id=f"psrc-{uuid.uuid4().hex[:12]}",
            workspace_id=ws.id,
            kind="static",
            rel_path=rel_norm,
            media_type=media_type,
            version=version,
            state="active",
            created_by=f"owner/{actor.owner_id}",
        )
        self.session.add(rec)
        self.session.flush()
        self._audit(actor, "preview_source.register", rec.id, {"kind": "static", "path": rec.rel_path})
        return self._serialize(rec)

    def register_process(
        self,
        actor: Actor,
        workspace_id: str,
        *,
        command: list[str],
        target_port: int | None = None,
        process_kind: str = "http",
        entry_path: str = "/",
        rel_cwd: str = "",
        lease_seconds: float = 900.0,
    ) -> dict[str, Any]:
        actor.require_owner()
        ws = self.workspaces.get_workspace(actor, workspace_id)

        # Delegate the real capability to the existing 18 §3 PreviewService:
        # loopback-only binding, health probing, lease, isolation flags.
        status = self.previews.start(
            actor,
            ws.id,
            command=command,
            target_port=target_port,
            kind=process_kind,
            entry_path=entry_path,
            rel_cwd=rel_cwd,
            lease_seconds=lease_seconds,
        )

        rec = PreviewSourceRecord(
            id=f"psrc-{uuid.uuid4().hex[:12]}",
            workspace_id=ws.id,
            kind="process",
            rel_path="",
            media_type="text/html",
            preview_session_id=status["id"],
            version=f"state:{status['state']}:{status['health']}",
            state="active",
            created_by=f"owner/{actor.owner_id}",
        )
        self.session.add(rec)
        self.session.flush()
        self._audit(actor, "preview_source.register", rec.id, {"kind": "process", "session": status["id"]})
        return self._serialize(rec, url=status["url"])

    # ------------------------------------------------------------------
    # Listing / version probe
    # ------------------------------------------------------------------
    def list_sources(self, actor: Actor, workspace_id: str) -> list[dict[str, Any]]:
        ws = self.workspaces.get_workspace(actor, workspace_id)
        rows = self.session.execute(
            select(PreviewSourceRecord)
            .where(PreviewSourceRecord.workspace_id == ws.id)
            .order_by(PreviewSourceRecord.created_at.asc())
        ).scalars().all()
        return [self._serialize(r) for r in rows]

    def version(self, actor: Actor, source_id: str) -> dict[str, Any]:
        rec = self._get_source(actor, source_id)
        if rec.state != "active":
            return {"id": rec.id, "kind": rec.kind, "version": rec.version, "state": rec.state}

        if rec.kind == "static":
            # Recompute from the live file. A vanished file is reported, never
            # papered over with a stale version.
            path = self.workspaces.resolve_path(
                self.workspaces.get_workspace(actor, rec.workspace_id), rec.rel_path
            )
            if not path.is_file():
                raise NotFound(f"Preview source file no longer exists: {rec.rel_path}")
            fresh = compute_file_version(path.stat())
            if fresh != rec.version:
                rec.version = fresh
                self.session.flush()
            return {"id": rec.id, "kind": rec.kind, "version": rec.version, "state": rec.state}

        # process: the version tracks the preview session liveness/health so a
        # state change is observable by the poller.
        try:
            st = self.previews.status(actor, rec.preview_session_id or "")
            fresh = f"state:{st['state']}:{st['health']}"
        except NotFound:
            fresh = "state:gone:offline"
        if fresh != rec.version:
            rec.version = fresh
            self.session.flush()
        return {
            "id": rec.id,
            "kind": rec.kind,
            "version": rec.version,
            "state": rec.state,
            "preview_session_id": rec.preview_session_id,
        }

    # ------------------------------------------------------------------
    # Read-only content (static only)
    # ------------------------------------------------------------------
    def read_content(self, actor: Actor, source_id: str) -> dict[str, Any]:
        rec = self._get_source(actor, source_id)
        if rec.kind != "static":
            raise ValidationFailed(
                "Process preview sources are served from their own loopback address; "
                "no static content is available here"
            )
        if rec.state != "active":
            raise NotFound(f"Preview source is offline: {rec.id}")

        ws = self.workspaces.get_workspace(actor, rec.workspace_id)
        path = self.workspaces.resolve_path(ws, rec.rel_path)
        if not path.is_file():
            raise NotFound(f"Preview source file no longer exists: {rec.rel_path}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValidationFailed(f"Preview source file cannot be read: {rec.rel_path} ({exc})") from exc

        return {
            "data": data,
            "media_type": rec.media_type,
            "path": rec.rel_path,
            "version": rec.version,
            # Response hardening applied by the API layer on top of this payload.
            "headers": {
                "Content-Security-Policy": _CSP_SANDBOX,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                # Lets the hot-refresh poller reconcile the fetched bytes with
                # the version it polled without a second round trip.
                "X-Preview-Version": rec.version,
            },
        }

    # ------------------------------------------------------------------
    # Unregister
    # ------------------------------------------------------------------
    def unregister(self, actor: Actor, source_id: str) -> dict[str, Any]:
        rec = self._get_source(actor, source_id)
        stopped: dict[str, Any] | None = None
        if rec.kind == "process" and rec.preview_session_id:
            try:
                stopped = self.previews.stop(actor, rec.preview_session_id)
            except NotFound:
                stopped = None  # session already gone — offline is still honest
        rec.state = "offline"
        rec.ended_at = utcnow()
        self.session.flush()
        self._audit(actor, "preview_source.unregister", rec.id, {"kind": rec.kind})
        return {"id": rec.id, "state": rec.state, "preview_stopped": stopped}
