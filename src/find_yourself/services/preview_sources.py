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

import csv
import hashlib
import json
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


# ---------------------------------------------------------------------------
# P2 · Layer 4 (data → chart render spec). 图像/图表渲染层：把工作区里的
# 结构化数据文件（.json/.csv）解析成前端可直接渲染的 render spec。
# 诚实性规则与既有三层完全一致：不可读/不可渲染一律抛显式错误，
# 绝不返回可被当作「成功」的空预览（铁律 1）。
# ---------------------------------------------------------------------------
DATA_MEDIA_TYPES: dict[str, str] = {
    ".json": "application/json",
    ".csv": "text/csv",
}
CHART_TYPES: tuple[str, ...] = ("bar", "line", "pie")


def _spec_from_json(text: str) -> dict[str, Any]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON cannot be parsed as chart data: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("JSON chart data must be an object")
    chart = doc.get("chart")
    if chart not in CHART_TYPES:
        raise ValueError(
            f"Unknown chart type {chart!r}; expected one of {list(CHART_TYPES)}"
        )
    series = doc.get("series")
    if not isinstance(series, list) or not series:
        raise ValueError('"series" must be a non-empty array of {label, value}')
    clean: list[dict[str, Any]] = []
    for i, item in enumerate(series):
        if not isinstance(item, dict) or set(item) - {"label", "value"}:
            raise ValueError(f"series[{i}] must be an object with only label/value")
        label, value = item.get("label"), item.get("value")
        if not isinstance(label, str):
            raise ValueError(f"series[{i}].label must be a string")
        # bool 是 int 的子类，显式排除——True/1 不是可作图数值。
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"series[{i}].value must be a number")
        clean.append({"label": label, "value": value})
    spec: dict[str, Any] = {"chart": chart, "series": clean}
    title = doc.get("title")
    if title is not None:
        if not isinstance(title, str):
            raise ValueError('"title" must be a string')
        spec["title"] = title
    return spec


def _spec_from_csv(text: str) -> dict[str, Any]:
    rows = [r for r in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if r.strip()]
    if len(rows) < 2:
        raise ValueError("CSV chart data needs a header row and at least one data row")
    header = next(csv.reader([rows[0]]))
    if len(header) < 2:
        raise ValueError("CSV chart data needs at least two columns (label, value)")
    series: list[dict[str, Any]] = []
    for i, line in enumerate(rows[1:], start=2):
        cells = next(csv.reader([line]))
        if len(cells) < 2:
            raise ValueError(f"CSV row {i} has fewer than two columns")
        try:
            value = float(cells[1])
        except ValueError as exc:
            raise ValueError(
                f"CSV row {i} second column {cells[1]!r} is not numeric"
            ) from exc
        series.append({"label": cells[0], "value": value})
    return {"chart": "bar", "series": series}


def build_render_spec(text: str, rel_path: str) -> dict[str, Any]:
    """P2 · 把结构化数据文件内容解析为 render spec（纯函数，可独立单测）。

    任何不可渲染的输入都抛 :class:`ValueError`（带人类可读原因），
    由调用方转成显式失败——协议里不存在「空预览冒充成功」。
    """
    lower = rel_path.lower()
    if lower.endswith(".json"):
        return _spec_from_json(text)
    if lower.endswith(".csv"):
        return _spec_from_csv(text)
    raise ValueError(f"Unsupported data preview type: {rel_path!r}")


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
        elif rec.kind == "data":
            # P2 · Layer 4：渲染规格读取地址（服务层能力；HTTP 暴露待路由授权）。
            data["render_url"] = f"/api/workbench/preview-sources/{rec.id}/render-spec"
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
    # P2 · Layer 4 registration / render spec (data → chart)
    # ------------------------------------------------------------------
    def register_data(self, actor: Actor, workspace_id: str, *, rel_path: str) -> dict[str, Any]:
        """注册一个结构化数据预览源（kind=``data``，Layer 4）。

        与 Layer 1 同源的沙箱边界（``resolve_path``）与诚实性规则：
        不存在的文件、不支持的扩展名一律显式失败。注册时**不**解析内容
        （文件随后仍会变化），解析发生在读取渲染规格时。
        """
        actor.require_owner()
        ws = self.workspaces.get_workspace(actor, workspace_id)

        raw = (rel_path or "").strip()
        if not raw:
            raise ValidationFailed("Preview source path is required")
        path = self.workspaces.resolve_path(ws, raw)
        if not path.is_file():
            raise ValidationFailed(f"Preview source is not a file inside the workspace: {raw}")

        media_type = DATA_MEDIA_TYPES.get(path.suffix.lower())
        if media_type is None:
            raise ValidationFailed(
                f"Unsupported data preview type {path.suffix!r}; "
                "only .json/.csv can be registered as chart data (Layer 4)"
            )

        version = compute_file_version(path.stat())
        rel_norm = path.relative_to(self.workspaces._root(ws)).as_posix()
        rec = PreviewSourceRecord(
            id=f"psrc-{uuid.uuid4().hex[:12]}",
            workspace_id=ws.id,
            kind="data",
            rel_path=rel_norm,
            media_type=media_type,
            version=version,
            state="active",
            created_by=f"owner/{actor.owner_id}",
        )
        self.session.add(rec)
        self.session.flush()
        self._audit(actor, "preview_source.register", rec.id, {"kind": "data", "path": rec.rel_path})
        return self._serialize(rec)

    def read_render_spec(self, actor: Actor, source_id: str) -> dict[str, Any]:
        """读取 Layer 4 数据源的**图表渲染规格**（诚实失败，绝不空预览）。

        文件消失/不可读/不可渲染（坏 JSON、非数值列、未知图型……）分别抛
        ``NotFound`` / ``ValidationFailed``——每个失败原因都直接来自
        :func:`build_render_spec` 的解析器。
        """
        rec = self._get_source(actor, source_id)
        if rec.kind != "data":
            raise ValidationFailed(
                "Render specs are only available for data (Layer 4) preview sources"
            )
        if rec.state != "active":
            raise NotFound(f"Preview source is offline: {rec.id}")

        ws = self.workspaces.get_workspace(actor, rec.workspace_id)
        path = self.workspaces.resolve_path(ws, rec.rel_path)
        if not path.is_file():
            raise NotFound(f"Preview source file no longer exists: {rec.rel_path}")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationFailed(
                f"Preview source file cannot be read: {rec.rel_path} ({exc})"
            ) from exc
        except UnicodeDecodeError as exc:
            raise ValidationFailed(
                f"Preview source file is not valid UTF-8 text: {rec.rel_path} ({exc})"
            ) from exc

        try:
            spec = build_render_spec(text, rec.rel_path)
        except ValueError as exc:
            raise ValidationFailed(
                f"Preview source cannot be rendered as a chart: {rec.rel_path} ({exc})"
            ) from exc

        # 版本随内容变化（mtime+size 已足够触发前端重拉）。
        fresh = compute_file_version(path.stat())
        if fresh != rec.version:
            rec.version = fresh
            self.session.flush()
        return {
            "id": rec.id,
            "path": rec.rel_path,
            "version": rec.version,
            "spec": spec,
        }

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

        if rec.kind in ("static", "data"):
            # Recompute from the live file. A vanished file is reported, never
            # papered over with a stale version. (P2: Layer 4 data sources
            # follow the exact same version probe semantics as Layer 1 static.)
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
