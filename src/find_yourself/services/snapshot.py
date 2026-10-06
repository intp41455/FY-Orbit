"""T6-F 高危写操作前置快照与回滚（补 G6）。

G6 现场：``git_service.revert_*`` 依赖**已 commit** 的历史；agent 误删/误覆盖
**未入库**的内容找不回（S6 违反）。

本模块提供「写前快照」：

* ``pre_write_snapshot``：在任何覆盖性写入**之前**，把目标文件现状复制到
  ``.runtime/snapshots/<snapshot_id>/``，manifest 逐文件记录 sha256/size/原路径；
  同时自动暂存一份 manifest 到 WorkStash（主库，重启后仍在）+ 审计挂帧。
* ``restore_preview``：回滚前先出 **diff 预览**（当前 sha vs 快照 sha）——
  不落任何写操作（红线：回滚前有预览）。
* ``restore_apply``：先校验快照副本完整性（sha256 对 manifest），再逐文件
  「写临时 → 验 sha → 原子替换 → 回读再验」；快照时点不存在的文件**不**
  自动删除，如实标注留给用户决定（诚实边界）。

挂点：``WorkspaceService.write_file`` 在覆盖前调用 ``pre_write_snapshot``，
快照失败则**拒绝写入**（fail-closed）——没有退路的覆盖不许发生。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import Settings
from ..db.types import utcnow
from .actor import Actor
from .audit import AuditService
from .errors import DomainError, NotFound
from .auto_stash import auto_stash

_MANIFEST_NAME = "manifest.json"


def _snapshot_root(settings: Settings | None) -> Path:
    env = os.environ.get("FY_SNAPSHOTS_PATH")
    base = env or (getattr(settings, "snapshots_path", "") if settings else "") \
        or ".runtime/snapshots"
    return Path(base)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pre_write_snapshot(
    session: Session,
    audit: AuditService,
    actor: Actor,
    *,
    files: list[str],
    reason: str,
    task_id: str = "",
    settings: Settings | None = None,
) -> dict:
    """把 ``files``（将要被覆盖的路径）的现状快照下来。

    只 ``flush`` 不 ``commit``（与调用方同事务）。任何文件级失败都抛
    ``DomainError``——调用方（write_file）fail-closed 拒绝写入。
    """
    if not files:
        raise DomainError("snapshot_empty", "pre-write snapshot needs at least one path")
    snap_id = uuid.uuid4().hex[:16]
    root = _snapshot_root(settings) / snap_id
    files_dir = root / "files"
    try:
        files_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DomainError(
            "snapshot_failed", f"cannot create snapshot dir {root}: {exc}") from exc

    entries: list[dict] = []
    seen: set[str] = set()
    for i, raw in enumerate(files):
        abs_path = str(Path(raw).resolve())
        if abs_path in seen:
            continue
        seen.add(abs_path)
        src = Path(abs_path)
        existed = src.is_file()
        entry: dict = {"path": abs_path, "existed": existed}
        if existed:
            copy_name = f"{i:04d}_{src.name}"
            try:
                shutil.copy2(src, files_dir / copy_name)
                entry.update({
                    "snapshot_copy": copy_name,
                    "sha256": _sha256_file(src),
                    "size": src.stat().st_size,
                })
            except OSError as exc:
                raise DomainError(
                    "snapshot_failed",
                    f"cannot snapshot {abs_path}: {exc}",
                ) from exc
        entries.append(entry)

    manifest = {
        "snapshot_id": snap_id,
        "reason": reason[:200],
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "actor": getattr(actor, "owner_id", "") or getattr(actor, "service_id", ""),
        "files": entries,
    }
    try:
        (root / _MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise DomainError(
            "snapshot_failed", f"cannot write manifest for {snap_id}: {exc}") from exc

    stash = auto_stash(
        session, audit, actor,
        title=f"写前快照 · {reason}"[:200],
        content=json.dumps(manifest, ensure_ascii=False),
        content_type="application/json",
        metadata={
            "kind": "pre_write_snapshot",
            "snapshot_id": snap_id,
            "task_id": task_id,
            "files": len(entries),
        },
    )
    audit.append(
        actor, "snapshot.pre_write", snap_id,
        {"reason": reason[:120], "task_id": task_id, "files": len(entries),
         "stash_id": stash.id},
    )
    session.flush()
    return manifest


def _load_manifest(snapshot_id: str, settings: Settings | None) -> dict:
    manifest_path = _snapshot_root(settings) / snapshot_id / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise NotFound("snapshot_not_found", f"Snapshot {snapshot_id} not found", 404)
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainError(
            "snapshot_manifest_corrupt",
            f"manifest for {snapshot_id} unreadable: {exc}",
        ) from exc


def restore_preview(
    session: Session, actor: Actor, snapshot_id: str, *, settings: Settings | None = None
) -> dict:
    """回滚前的 diff 预览：逐文件对比「当前 sha vs 快照 sha」。不落任何写。"""
    manifest = _load_manifest(snapshot_id, settings)
    files = []
    for entry in manifest.get("files", []):
        current = Path(entry["path"])
        cur_exists = current.is_file()
        cur_sha = _sha256_file(current) if cur_exists else None
        files.append({
            "path": entry["path"],
            "existed_at_snapshot": entry.get("existed", False),
            "exists_now": cur_exists,
            "sha256_now": cur_sha,
            "sha256_snapshot": entry.get("sha256"),
            "changed": bool(cur_sha and entry.get("sha256") and cur_sha != entry["sha256"])
                       or (entry.get("existed") and not cur_exists),
            "restorable": bool(entry.get("snapshot_copy")),
        })
    return {"snapshot_id": snapshot_id, "reason": manifest.get("reason", ""),
            "files": files}


def restore_apply(
    session: Session,
    audit: AuditService,
    actor: Actor,
    snapshot_id: str,
    *,
    settings: Settings | None = None,
) -> dict:
    """按快照回滚：先整体校验副本完整性，再逐文件原子替换 + 回读验证。

    快照时点不存在（``existed=False``）的文件**不自动删除**——删除是比覆盖
    更危险的不可逆动作，如实标注留给用户决定（红线 5）。
    """
    manifest = _load_manifest(snapshot_id, settings)
    root = _snapshot_root(settings) / snapshot_id / "files"

    # 第一遍：全部副本先过完整性，任何一份损坏就整体拒绝（不做半吊子回滚）
    for entry in manifest.get("files", []):
        if not entry.get("snapshot_copy"):
            continue
        copy_path = root / entry["snapshot_copy"]
        if not copy_path.is_file() or _sha256_file(copy_path) != entry.get("sha256"):
            raise DomainError(
                "snapshot_integrity",
                f"snapshot copy for {entry['path']} failed sha256 verification; "
                "refusing to restore from a tampered/incomplete snapshot",
            )

    results = []
    for entry in manifest.get("files", []):
        target = Path(entry["path"])
        if not entry.get("existed"):
            results.append({
                "path": entry["path"], "restored": False,
                "action": "absent_at_snapshot",
                "note": "file did not exist at snapshot time; not auto-deleting — "
                        "decide manually",
            })
            continue
        copy_path = root / entry["snapshot_copy"]
        tmp = target.with_name(target.name + f".restore-{uuid.uuid4().hex[:8]}.tmp")
        shutil.copy2(copy_path, tmp)
        if _sha256_file(tmp) != entry.get("sha256"):
            tmp.unlink(missing_ok=True)
            results.append({"path": entry["path"], "restored": False,
                            "action": "verify_failed",
                            "note": "temp copy failed verification; target untouched"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, target)
        restored_sha = _sha256_file(target)
        results.append({
            "path": entry["path"], "restored": restored_sha == entry.get("sha256"),
            "action": "replaced", "sha256": restored_sha,
        })

    all_ok = all(r.get("restored") for r in results if r.get("action") == "replaced")
    audit.append(
        actor, "snapshot.restored", snapshot_id,
        {"ok": all_ok, "files": len(results), "reason": manifest.get("reason", "")[:120]},
    )
    session.flush()
    return {"snapshot_id": snapshot_id, "ok": all_ok, "files": results}
