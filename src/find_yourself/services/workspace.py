"""WorkspaceService: 18 工程代码工作台 — 项目与文件 / 编辑器 / 实时修改.

Implements the backend half of the engineering code workbench required by
``18_工程代码工作台与主协调Agent全流程实施规格.md`` §3 and §5:

- Workspace registration against an **authorized root** (local or cloud).
- Lazy/paged file tree with accurate encoding, size, read-only and error state.
- New / rename / save / search; deletes are recoverable (moved to a trash area).
- ``FileRevision`` optimistic concurrency: every save carries
  ``expected_revision`` and a mismatch is rejected with ``Conflict`` — an agent
  and a user editing the same file can never silently overwrite each other.
- Monotonic ``WorkspaceEvent`` stream carrying version/seq, source actor, task id
  and a before/after diff, so a disconnected client can resync a snapshot and
  then replay the events it missed.

Hard boundary rules (§6 文件边界):
- absolute paths, drive letters, UNC paths and ``..`` traversal are rejected;
- symlink/junction escapes are rejected by resolving the real path and proving
  it stays inside the authorized root;
- case-insensitive collisions on Windows are detected rather than silently
  merging two distinct files;
- ``.git`` internals (config, hooks, objects) are read-only to the workbench so
  a workspace cannot rewrite Git external configuration or hooks;
- credential-looking filenames (``.env``, ``*.pem``, ``id_rsa`` …) are never
  readable through the workbench API.
"""

from __future__ import annotations

import difflib
import hashlib
import os
from pathlib import Path
import shutil
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from find_yourself.db.types import utcnow
from find_yourself.db.workbench_models import (
    FileRevision,
    WorkspaceEvent,
    WorkspaceManifest,
)
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from find_yourself.services.snapshot import pre_write_snapshot

#: Files that may never be read or written through the workbench API.
CREDENTIAL_FILE_PATTERNS = (
    ".env", "config.env", "id_rsa", "id_ed25519", "credentials", "credentials.json",
    ".netrc", ".pgpass", "kubeconfig",
)
CREDENTIAL_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")

#: Path prefixes that are readable but never writable from the workbench.
PROTECTED_WRITE_PREFIXES = (".git/", ".git\\")

TRASH_DIRNAME = ".workbench-trash"

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2 MiB — larger files are reported, not edited inline.


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_unc_or_absolute(raw: str) -> bool:
    """Detect absolute POSIX, Windows drive-letter and UNC style paths."""
    if raw.startswith(("/", "\\")):
        return True
    if len(raw) >= 2 and raw[1] == ":":
        return True
    if raw.startswith("\\\\") or raw.startswith("//"):
        return True
    return False


def decode_text(data: bytes) -> tuple[str, str, bool]:
    """Decode bytes, returning ``(text, encoding_label, decoded_cleanly)``.

    Binary content is reported honestly as ``binary`` rather than being mangled.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), "utf-8-sig", True
        except UnicodeDecodeError:
            pass
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16", True
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8"), "utf-8", True
    except UnicodeDecodeError:
        pass
    if b"\x00" in data[:4096]:
        return "", "binary", False
    try:
        return data.decode("cp1252"), "cp1252", True
    except UnicodeDecodeError:
        return "", "binary", False


class WorkspaceService:
    """Authorized workspace + file revision management for the code workbench."""

    def __init__(self, session: Session, audit: AuditService | None = None):
        self.session = session
        self.audit = audit

    # ------------------------------------------------------------------
    # Workspace registration
    # ------------------------------------------------------------------
    def register_workspace(
        self,
        actor: Actor,
        *,
        project_name: str,
        authorized_root: str,
        mode: str = "local",
        data_domain: str = "work",
        branch: str = "",
        task_refs: list[str] | None = None,
        resource_limits: dict[str, Any] | None = None,
        exec_identity: str = "",
    ) -> WorkspaceManifest:
        actor.require_owner()
        if mode not in ("local", "cloud"):
            raise ValidationFailed(f"Unsupported workspace mode: {mode}")
        if data_domain not in ("personal", "work", "shared"):
            raise ValidationFailed(f"Unsupported data domain: {data_domain}")
        if _is_unc_or_absolute(authorized_root) is False:
            raise ValidationFailed("Authorized root must be an absolute path")

        raw_root = Path(authorized_root)
        if not raw_root.exists() or not raw_root.is_dir():
            raise ValidationFailed(f"Authorized root does not exist or is not a directory: {authorized_root}")

        # Resolve the real path so the stored root is not itself a symlink/junction
        # that later resolves somewhere else.
        root = raw_root.resolve()

        ws = WorkspaceManifest(
            id=f"ws-{uuid4().hex[:12]}",
            owner_id=actor.owner_id,
            project_name=project_name,
            data_domain=data_domain,
            mode=mode,
            authorized_root=str(root),
            exec_identity=exec_identity or f"owner/{actor.owner_id}",
            branch=branch,
            task_refs=list(task_refs or []),
            resource_limits=dict(resource_limits or {"max_file_bytes": MAX_TEXT_BYTES, "max_open_terminals": 2}),
            state="active",
        )
        self.session.add(ws)
        self.session.flush()
        self._audit(actor, "workspace.register", ws.id, {"root": str(root), "mode": mode})
        return ws

    def get_workspace(self, actor: Actor, workspace_id: str) -> WorkspaceManifest:
        ws = self.session.get(WorkspaceManifest, workspace_id)
        if ws is None:
            raise NotFound(f"Workspace not found: {workspace_id}")
        if actor.subject_type == "owner":
            if ws.owner_id != actor.owner_id:
                raise PermissionDenied("workspace_forbidden", "Workspace belongs to another owner", 403)
        else:
            # Service identities must be explicitly bound to this workspace's task.
            if not ws.task_refs or (actor.bound_task_id not in (ws.task_refs or [])):
                raise PermissionDenied(
                    "workspace_forbidden", "Service identity is not bound to this workspace", 403
                )
        return ws

    def list_workspaces(self, actor: Actor) -> list[WorkspaceManifest]:
        if actor.subject_type == "owner":
            stmt = select(WorkspaceManifest).where(WorkspaceManifest.owner_id == actor.owner_id)
        else:
            stmt = select(WorkspaceManifest).where(WorkspaceManifest.task_refs.contains(actor.bound_task_id or ""))
        return list(self.session.execute(stmt.order_by(WorkspaceManifest.created_at.asc())).scalars().all())

    # ------------------------------------------------------------------
    # Path boundary enforcement
    # ------------------------------------------------------------------
    def _root(self, ws: WorkspaceManifest) -> Path:
        root = Path(ws.authorized_root).resolve()
        if not root.exists():
            raise NotFound(f"Authorized root no longer exists: {root}")
        return root

    def resolve_path(self, ws: WorkspaceManifest, rel_path: str, *, for_write: bool = False) -> Path:
        """Resolve ``rel_path`` inside the authorized root or raise.

        Rejects absolute/drive/UNC paths, ``..`` traversal, symlink or junction
        escapes, credential files, and (for writes) ``.git`` internals.
        """
        if rel_path is None:
            raise ValidationFailed("Path is required")
        raw = str(rel_path).strip()
        if raw in ("", "."):
            raw = ""
        if "\x00" in raw:
            raise ValidationFailed("Path contains a NUL byte")

        if _is_unc_or_absolute(raw):
            raise ValidationFailed(
                f"Absolute, drive-letter and UNC paths are rejected by workspace policy: {raw!r}"
            )

        root = self._root(ws)
        candidate = root / raw

        # Resolve symlinks/junctions. For a not-yet-existing target, resolve the
        # nearest existing parent and append the remainder so we still prove the
        # final location stays inside the root.
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ValidationFailed(f"Path cannot be resolved: {raw!r} ({exc})") from exc

        if resolved != root:
            try:
                resolved.relative_to(root)
            except ValueError:
                raise PermissionDenied(
                    "path_escape",
                    f"Path {raw!r} resolves outside the authorized workspace root",
                    403,
                ) from None

        # Reject a symlinked leaf that points outside the root (resolve() covers
        # this, but a broken symlink would not be caught above).
        if candidate.is_symlink():
            target = candidate.resolve()
            try:
                target.relative_to(root)
            except ValueError:
                raise PermissionDenied(
                    "path_escape", f"Symlink {raw!r} escapes the authorized workspace root", 403
                ) from None

        # Directory junctions (Windows reparse points) are covered explicitly —
        # 18 §6 requires the boundary to handle symlink/junction, not just "..".
        is_junction = getattr(candidate, "is_junction", None)
        if callable(is_junction):
            try:
                if candidate.is_junction():
                    target = candidate.resolve()
                    try:
                        target.relative_to(root)
                    except ValueError:
                        raise PermissionDenied(
                            "path_escape",
                            f"Directory junction {raw!r} escapes the authorized workspace root",
                            403,
                        ) from None
            except PermissionDenied:
                raise
            except OSError:
                pass

        # Also reject any link in the parent chain that leaves the root.
        for parent in candidate.parents:
            if parent == root:
                break
            if not parent.exists():
                continue
            linked = parent.is_symlink() or (
                callable(getattr(parent, "is_junction", None)) and parent.is_junction()
            )
            if linked:
                try:
                    parent.resolve().relative_to(root)
                except ValueError:
                    raise PermissionDenied(
                        "path_escape",
                        f"Path {raw!r} traverses a link ({parent.name}) that escapes "
                        "the authorized workspace root",
                        403,
                    ) from None

        rel_norm = resolved.relative_to(root).as_posix() if resolved != root else ""

        # Windows case-insensitive collision detection.
        if os.name == "nt" and rel_norm:
            parent = resolved.parent
            if parent.exists():
                wanted = resolved.name.lower()
                for entry in parent.iterdir():
                    if entry.name.lower() == wanted and entry.name != resolved.name:
                        raise Conflict(
                            f"Case-insensitive name collision: {entry.name!r} already exists "
                            f"and differs only in case from {resolved.name!r}"
                        )

        lower = rel_norm.lower()
        name = Path(rel_norm).name.lower() if rel_norm else ""
        if name in CREDENTIAL_FILE_PATTERNS or name.endswith(CREDENTIAL_SUFFIXES):
            raise PermissionDenied(
                "credential_file_blocked",
                f"Access to credential file {rel_norm!r} is blocked by workspace policy",
                403,
            )

        if for_write:
            for prefix in PROTECTED_WRITE_PREFIXES:
                if lower.startswith(prefix.replace("\\", "/")):
                    raise PermissionDenied(
                        "git_internals_protected",
                        f"Writes into Git internals ({rel_norm!r}) are rejected: "
                        "a workspace cannot rewrite Git external configuration or hooks",
                        403,
                    )

        return resolved

    def _rel(self, ws: WorkspaceManifest, path: Path) -> str:
        return path.resolve().relative_to(self._root(ws)).as_posix()

    # ------------------------------------------------------------------
    # File tree
    # ------------------------------------------------------------------
    def list_tree(
        self,
        actor: Actor,
        workspace_id: str,
        rel_path: str = "",
        *,
        depth: int = 1,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Lazy/paged directory listing with encoding, size and read-only state."""
        ws = self.get_workspace(actor, workspace_id)
        target = self.resolve_path(ws, rel_path)
        if not target.exists():
            raise NotFound(f"Directory not found: {rel_path}")
        if not target.is_dir():
            raise ValidationFailed(f"Not a directory: {rel_path}")

        entries: list[dict[str, Any]] = []
        skipped_credential = 0
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name == TRASH_DIRNAME:
                continue
            lower = child.name.lower()
            if lower in CREDENTIAL_FILE_PATTERNS or lower.endswith(CREDENTIAL_SUFFIXES):
                skipped_credential += 1
                continue
            entries.append(self._describe(ws, child, depth=depth))

        total = len(entries)
        page = entries[offset: offset + max(1, limit)]
        return {
            "workspace_id": ws.id,
            "path": rel_path,
            "entries": page,
            "total": total,
            "offset": offset,
            "limit": limit,
            "truncated": offset + len(page) < total,
            "blocked_credential_entries": skipped_credential,
        }

    def _describe(self, ws: WorkspaceManifest, path: Path, *, depth: int = 1) -> dict[str, Any]:
        info: dict[str, Any] = {
            "name": path.name,
            "path": self._rel(ws, path),
            "type": "dir" if path.is_dir() else "file",
            "error": None,
        }
        try:
            st = path.stat()
            info["size_bytes"] = st.st_size if path.is_file() else None
            info["mtime"] = st.st_mtime
            info["read_only"] = not os.access(path, os.W_OK)
            if path.is_file():
                data = path.read_bytes()[:4096]
                _, enc, clean = decode_text(data)
                info["encoding"] = enc
                info["binary"] = not clean
                info["revision"] = self._current_revision(ws, path)["revision"]
            else:
                info["encoding"] = None
                info["binary"] = False
                info["has_children"] = any(True for _ in path.iterdir())
        except OSError as exc:
            info["error"] = f"{type(exc).__name__}: {exc}"
            info["read_only"] = True
        return info

    # ------------------------------------------------------------------
    # Revisions
    # ------------------------------------------------------------------
    def _latest_revision_row(self, workspace_id: str, rel_path: str) -> FileRevision | None:
        stmt = (
            select(FileRevision)
            .where(FileRevision.workspace_id == workspace_id, FileRevision.rel_path == rel_path)
            .order_by(FileRevision.revision.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def _current_revision(self, ws: WorkspaceManifest, path: Path) -> dict[str, Any]:
        """Reconcile the stored revision with what is actually on disk.

        If the file changed underneath us (an agent wrote it directly, or the user
        edited it in an external editor) the revision is bumped so a later save
        carrying the stale ``expected_revision`` conflicts instead of silently
        overwriting the other writer's work.
        """
        rel = self._rel(ws, path)
        row = self._latest_revision_row(ws.id, rel)
        if not path.exists():
            return {"rel_path": rel, "revision": row.revision if row else 0, "sha256": None, "exists": False}

        disk = _sha256_bytes(path.read_bytes())
        if row is None:
            created = FileRevision(
                id=f"rev-{uuid4().hex[:12]}",
                workspace_id=ws.id,
                rel_path=rel,
                revision=1,
                content_sha256=disk,
                size_bytes=path.stat().st_size,
                source_actor="external/untracked",
            )
            self.session.add(created)
            self.session.flush()
            return {"rel_path": rel, "revision": 1, "sha256": disk, "exists": True}

        if row.content_sha256 != disk:
            bumped = FileRevision(
                id=f"rev-{uuid4().hex[:12]}",
                workspace_id=ws.id,
                rel_path=rel,
                revision=row.revision + 1,
                content_sha256=disk,
                size_bytes=path.stat().st_size,
                before_sha256=row.content_sha256,
                source_actor="external/detected",
            )
            self.session.add(bumped)
            self.session.flush()
            self._emit_event(
                ws.id,
                "file.external_change_detected",
                rel_path=rel,
                revision=bumped.revision,
                source_actor="external/detected",
                diff={"before_sha256": row.content_sha256, "after_sha256": disk},
            )
            return {"rel_path": rel, "revision": bumped.revision, "sha256": disk, "exists": True}

        return {"rel_path": rel, "revision": row.revision, "sha256": disk, "exists": True}

    def read_file(self, actor: Actor, workspace_id: str, rel_path: str) -> dict[str, Any]:
        ws = self.get_workspace(actor, workspace_id)
        path = self.resolve_path(ws, rel_path)
        if not path.exists() or not path.is_file():
            raise NotFound(f"File not found: {rel_path}")
        if path.stat().st_size > MAX_TEXT_BYTES:
            raise ValidationFailed(
                f"File exceeds the {MAX_TEXT_BYTES} byte inline-edit limit; use the terminal or download instead"
            )
        raw = path.read_bytes()
        text, encoding, clean = decode_text(raw)
        cur = self._current_revision(ws, path)
        return {
            "workspace_id": ws.id,
            "path": cur["rel_path"],
            "content": text,
            "encoding": encoding,
            "binary": not clean,
            "editable": clean,
            "size_bytes": len(raw),
            "sha256": cur["sha256"],
            "revision": cur["revision"],
            "read_only": not os.access(path, os.W_OK),
        }

    def write_file(
        self,
        actor: Actor,
        workspace_id: str,
        rel_path: str,
        content: str,
        *,
        expected_revision: int | None = None,
        source_task_id: str | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Save a file with optimistic concurrency.

        ``expected_revision`` is mandatory for an existing file; a mismatch raises
        ``Conflict`` so concurrent agent/user edits never silently overwrite.
        """
        ws = self.get_workspace(actor, workspace_id)
        path = self.resolve_path(ws, rel_path, for_write=True)
        actor_label = f"service/{actor.service_id}" if actor.service_id else f"owner/{actor.owner_id}"

        exists = path.exists()
        cur = self._current_revision(ws, path) if exists else {"revision": 0, "sha256": None}

        if exists:
            if not os.access(path, os.W_OK):
                raise PermissionDenied("read_only", f"File is read-only: {rel_path}", 403)
            if expected_revision is None:
                raise ValidationFailed(
                    f"Saving an existing file requires expected_revision (current is {cur['revision']})"
                )
            if int(expected_revision) != int(cur["revision"]):
                raise Conflict(
                    f"Revision conflict on {rel_path!r}: expected {expected_revision}, "
                    f"current {cur['revision']}. The file changed since it was loaded — "
                    "re-read and merge instead of overwriting."
                )
        elif expected_revision not in (None, 0):
            raise Conflict(
                f"Revision conflict on {rel_path!r}: expected {expected_revision} but the file does not exist"
            )

        try:
            data = content.encode(encoding)
        except (UnicodeEncodeError, LookupError) as exc:
            raise ValidationFailed(f"Cannot encode content as {encoding}: {exc}") from exc

        path.parent.mkdir(parents=True, exist_ok=True)
        before_raw = path.read_bytes() if exists else b""
        before_text, _, _ = decode_text(before_raw) if exists else ("", "utf-8", True)

        # T6-F（补 G6）：高危写前置快照——被覆盖文件的现状先落快照（sha256
        # manifest + WorkStash + 审计帧），agent 误写可经恢复中心回滚。
        # fail-closed：快照失败即拒绝写入——没有退路的覆盖不许发生。
        snap = pre_write_snapshot(
            self.session, self.audit, actor,
            files=[str(path)],
            reason=f"workspace.write_file:{rel_path}",
            task_id=source_task_id or "",
        )

        path.write_bytes(data)

        new_revision = int(cur["revision"]) + 1
        new_sha = _sha256_bytes(data)
        row = FileRevision(
            id=f"rev-{uuid4().hex[:12]}",
            workspace_id=ws.id,
            rel_path=cur.get("rel_path") or self._rel(ws, path),
            revision=new_revision,
            content_sha256=new_sha,
            size_bytes=len(data),
            encoding=encoding,
            source_actor=actor_label,
            source_task_id=source_task_id,
            before_sha256=cur.get("sha256"),
        )
        self.session.add(row)
        self.session.flush()

        diff = self._unified_diff(before_text, content, cur.get("rel_path") or rel_path)
        self._emit_event(
            ws.id,
            "file.saved",
            rel_path=row.rel_path,
            revision=new_revision,
            source_actor=actor_label,
            source_task_id=source_task_id,
            diff=diff,
        )
        self._audit(actor, "workspace.file.save", ws.id, {"path": row.rel_path, "revision": new_revision})

        return {
            "workspace_id": ws.id,
            "path": row.rel_path,
            "revision": new_revision,
            "sha256": new_sha,
            "size_bytes": len(data),
            "encoding": encoding,
            "diff": diff,
            "snapshot_id": snap["snapshot_id"],
        }

    def rename_file(
        self,
        actor: Actor,
        workspace_id: str,
        rel_path: str,
        new_rel_path: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        ws = self.get_workspace(actor, workspace_id)
        src = self.resolve_path(ws, rel_path, for_write=True)
        dst = self.resolve_path(ws, new_rel_path, for_write=True)
        if not src.exists():
            raise NotFound(f"File not found: {rel_path}")
        if dst.exists():
            raise Conflict(f"Rename target already exists: {new_rel_path}")

        cur = self._current_revision(ws, src)
        if expected_revision is not None and int(expected_revision) != int(cur["revision"]):
            raise Conflict(
                f"Revision conflict on {rel_path!r}: expected {expected_revision}, current {cur['revision']}"
            )

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        actor_label = f"service/{actor.service_id}" if actor.service_id else f"owner/{actor.owner_id}"
        new_sha = _sha256_bytes(dst.read_bytes())
        row = FileRevision(
            id=f"rev-{uuid4().hex[:12]}",
            workspace_id=ws.id,
            rel_path=self._rel(ws, dst),
            revision=1,
            content_sha256=new_sha,
            size_bytes=dst.stat().st_size,
            source_actor=actor_label,
            before_sha256=cur.get("sha256"),
        )
        self.session.add(row)
        self.session.flush()
        self._emit_event(
            ws.id,
            "file.renamed",
            rel_path=row.rel_path,
            revision=1,
            source_actor=actor_label,
            diff={"from": cur.get("rel_path"), "to": row.rel_path},
        )
        return {"workspace_id": ws.id, "path": row.rel_path, "from": cur.get("rel_path"), "revision": 1}

    def delete_file(
        self,
        actor: Actor,
        workspace_id: str,
        rel_path: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Recoverable delete: the file is moved into the workspace trash area."""
        ws = self.get_workspace(actor, workspace_id)
        path = self.resolve_path(ws, rel_path, for_write=True)
        if not path.exists():
            raise NotFound(f"File not found: {rel_path}")
        cur = self._current_revision(ws, path)
        if expected_revision is not None and int(expected_revision) != int(cur["revision"]):
            raise Conflict(
                f"Revision conflict on {rel_path!r}: expected {expected_revision}, current {cur['revision']}"
            )

        root = self._root(ws)
        trash = root / TRASH_DIRNAME / f"{int(time.time() * 1000)}-{uuid4().hex[:6]}"
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / Path(cur["rel_path"]).name
        shutil.move(str(path), str(dest))

        actor_label = f"service/{actor.service_id}" if actor.service_id else f"owner/{actor.owner_id}"
        row = FileRevision(
            id=f"rev-{uuid4().hex[:12]}",
            workspace_id=ws.id,
            rel_path=cur["rel_path"],
            revision=int(cur["revision"]) + 1,
            content_sha256=cur.get("sha256") or "",
            size_bytes=0,
            source_actor=actor_label,
            is_deleted=True,
            before_sha256=cur.get("sha256"),
        )
        self.session.add(row)
        self.session.flush()
        self._emit_event(
            ws.id,
            "file.deleted",
            rel_path=cur["rel_path"],
            revision=row.revision,
            source_actor=actor_label,
            diff={"recoverable": True, "trash_path": str(dest.relative_to(root).as_posix())},
        )
        return {
            "workspace_id": ws.id,
            "path": cur["rel_path"],
            "revision": row.revision,
            "recoverable": True,
            "trash_ref": dest.relative_to(root).as_posix(),
        }

    def search(
        self,
        actor: Actor,
        workspace_id: str,
        query: str,
        *,
        mode: str = "name",
        limit: int = 50,
    ) -> dict[str, Any]:
        ws = self.get_workspace(actor, workspace_id)
        if not query.strip():
            raise ValidationFailed("Search query is required")
        if mode not in ("name", "content"):
            raise ValidationFailed(f"Unsupported search mode: {mode}")
        root = self._root(ws)
        needle = query.lower()
        hits: list[dict[str, Any]] = []
        truncated = False
        scanned = 0

        for path in sorted(root.rglob("*")):
            if len(hits) >= limit:
                truncated = True
                break
            if TRASH_DIRNAME in path.parts:
                continue
            name = path.name.lower()
            if name in CREDENTIAL_FILE_PATTERNS or name.endswith(CREDENTIAL_SUFFIXES):
                continue
            scanned += 1
            try:
                if mode == "name":
                    if needle in name:
                        hits.append({"path": self._rel(ws, path), "type": "dir" if path.is_dir() else "file", "match": "name"})
                elif path.is_file() and path.stat().st_size <= MAX_TEXT_BYTES:
                    text, _, clean = decode_text(path.read_bytes())
                    if clean and needle in text.lower():
                        idx = text.lower().index(needle)
                        hits.append({
                            "path": self._rel(ws, path),
                            "type": "file",
                            "match": "content",
                            "preview": text[max(0, idx - 40): idx + 80].replace("\n", " "),
                        })
            except OSError:
                continue

        return {"workspace_id": ws.id, "query": query, "mode": mode, "hits": hits,
                "count": len(hits), "truncated": truncated, "scanned": scanned}

    # ------------------------------------------------------------------
    # Events / snapshot
    # ------------------------------------------------------------------
    def _next_seq(self, workspace_id: str) -> int:
        stmt = select(func.max(WorkspaceEvent.seq)).where(WorkspaceEvent.workspace_id == workspace_id)
        current = self.session.execute(stmt).scalar_one_or_none()
        return int(current or 0) + 1

    def _emit_event(
        self,
        workspace_id: str,
        event_type: str,
        *,
        rel_path: str | None = None,
        revision: int | None = None,
        source_actor: str = "",
        source_task_id: str | None = None,
        diff: dict[str, Any] | None = None,
    ) -> WorkspaceEvent:
        evt = WorkspaceEvent(
            id=f"wsevt-{uuid4().hex[:12]}",
            workspace_id=workspace_id,
            seq=self._next_seq(workspace_id),
            event_type=event_type,
            rel_path=rel_path,
            revision=revision,
            source_actor=source_actor,
            source_task_id=source_task_id,
            diff=diff or {},
        )
        self.session.add(evt)
        self.session.flush()
        return evt

    def get_events(self, actor: Actor, workspace_id: str, cursor: int = 0) -> list[dict[str, Any]]:
        self.get_workspace(actor, workspace_id)
        stmt = (
            select(WorkspaceEvent)
            .where(WorkspaceEvent.workspace_id == workspace_id, WorkspaceEvent.seq > cursor)
            .order_by(WorkspaceEvent.seq.asc())
        )
        rows = self.session.execute(stmt).scalars().all()
        return [
            {
                "seq": r.seq,
                "event_type": r.event_type,
                "path": r.rel_path,
                "revision": r.revision,
                "source_actor": r.source_actor,
                "source_task_id": r.source_task_id,
                "diff": r.diff,
                "at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    def get_snapshot(self, actor: Actor, workspace_id: str, rel_path: str = "") -> dict[str, Any]:
        """Full tree snapshot + latest cursor, so a reconnecting client resyncs."""
        ws = self.get_workspace(actor, workspace_id)
        tree = self.list_tree(actor, workspace_id, rel_path, depth=3, limit=1000)
        cursor = self.session.execute(
            select(func.max(WorkspaceEvent.seq)).where(WorkspaceEvent.workspace_id == workspace_id)
        ).scalar_one_or_none()
        return {
            "workspace_id": ws.id,
            "authorized_root": ws.authorized_root,
            "mode": ws.mode,
            "data_domain": ws.data_domain,
            "branch": ws.branch,
            "state": ws.state,
            "cursor": int(cursor or 0),
            "tree": tree["entries"],
            "truncated": tree["truncated"],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _unified_diff(before: str, after: str, label: str, *, max_lines: int = 200) -> dict[str, Any]:
        diff_lines = list(
            difflib.unified_diff(
                before.splitlines(), after.splitlines(),
                fromfile=f"a/{label}", tofile=f"b/{label}", lineterm="",
            )
        )
        truncated = len(diff_lines) > max_lines
        return {
            "label": label,
            "added": sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")),
            "removed": sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---")),
            "text": "\n".join(diff_lines[:max_lines]),
            "truncated": truncated,
        }

    def _audit(self, actor: Actor, action: str, target: str, details: dict[str, Any]) -> None:
        if self.audit is None:
            return
        try:
            self.audit.append(actor, action, target, details)
        except Exception:
            # Auditing must never mask the primary operation result.
            pass
