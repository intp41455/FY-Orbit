"""冬眠机制（A-任务可移植-05）—— 遇临界封存 + 条件成熟一键唤醒。

**本质**：正常跑；遇到**没钱 / 模型崩 / 用户想暂停 / 想换更聪明的模型**，立刻
「冬眠」——把任务状态 + 产出物一起打包封存；条件成熟后换到别的地方**一键唤醒**
接着干（跨模型 / 跨平台）。

设计要点
--------

1. **封存是可验证的包**：目录里一份 ``task.clawtask``（内含本机指针，故用
   ``portable=False`` 序列化——本机指针留在本地副本，不随可移植文件外传）
   + ``manifest.json``（逐文件 sha256 / size / 相对路径）。
   唤醒时**先整体校验**，任何一份被改动就拒绝唤醒（fail closed），绝不半截恢复。
2. **临界可判定**：:func:`hibernation_policy` 把「临界」变成可测的策略——
   预算 80% 预警、95% 强制建议冬眠（与 A-任务可移植-01 的余额熔断口径一致）。
   策略只**给建议**，是否封存仍由调用方决定（不静默替用户停机）。
3. **owner 隔离是查询谓词**：``list_hibernations`` / ``wake`` 都按 actor 过滤，
   别人的封存包对当前 actor 表现为「不存在」（404），不泄露存在性。
4. **零新表**：包落 ``FY_CLAWTASK_HIBERNATION_DIR``（默认
   ``.runtime/clawtask-hibernation``）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..actor import Actor
from ..audit import AuditService
from ..errors import DomainError, NotFound, ValidationFailed
from .format import (
    CLAWTASK_EXT,
    parse,
    serialize,
    validate_clawtask,
)

_MANIFEST_NAME = "manifest.json"
_PACKAGE_NAME = f"task{CLAWTASK_EXT}"

#: 临界策略：预警 / 强制建议（与 A-任务可移植-01 的 80%/95% 口径一致）。
WARN_PERCENT = 80.0
FORCE_PERCENT = 95.0


def hibernation_dir() -> Path:
    return Path(os.environ.get("FY_CLAWTASK_HIBERNATION_DIR", ".runtime/clawtask-hibernation"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hibernation_policy(*, budget_percent: float | None = None,
                       model_failed: bool = False, user_requested: bool = False) -> dict[str, Any]:
    """判定是否该冬眠，并**说清理由**（不猜、不静默停机）。

    返回 ``should_hibernate`` + ``triggers``（触发了哪些条件）+ ``thresholds``。
    ``budget_percent`` 是「已用百分比」。
    """
    triggers: list[str] = []
    if budget_percent is not None:
        if not isinstance(budget_percent, (int, float)) or isinstance(budget_percent, bool):
            raise ValidationFailed("budget_percent_invalid", "budget_percent 必须是数字")
        if float(budget_percent) >= FORCE_PERCENT:
            triggers.append("budget_force")
        elif float(budget_percent) >= WARN_PERCENT:
            triggers.append("budget_warn")
    if model_failed:
        triggers.append("model_failed")
    if user_requested:
        triggers.append("user_requested")
    return {
        "should_hibernate": any(t in ("budget_force", "model_failed", "user_requested")
                                for t in triggers),
        "warn_only": triggers == ["budget_warn"],
        "triggers": triggers,
        "thresholds": {"warn_percent": WARN_PERCENT, "force_percent": FORCE_PERCENT},
        "note": "策略只给建议；是否封存由调用方决定（不静默替用户停机）。",
    }


class HibernationStore:
    """本地冬眠包仓。"""

    def __init__(self, *, directory: str | os.PathLike[str] | None = None,
                 audit: AuditService | None = None) -> None:
        self._dir = Path(directory) if directory else hibernation_dir()
        self.audit = audit

    @property
    def directory(self) -> Path:
        """封存包根目录（只读暴露，供路由层把它写进 ``local_only`` 指针）。"""
        return self._dir

    # -- 封存 ---------------------------------------------------------------
    def hibernate(
        self,
        actor: Actor,
        doc: dict[str, Any],
        *,
        reason: str,
        artifacts: list[str] | None = None,
        budget_percent: float | None = None,
    ) -> dict[str, Any]:
        """把任务状态 + 产出物打包封存，返回可唤醒的 ``hibernation_id``。"""
        actor.require_authenticated()
        policy = hibernation_policy(
            budget_percent=budget_percent,
            user_requested=True,          # 显式调用就是「要封存」，理由由 reason 说明
        )
        # 至少要求「能自证是同一个任务」：核心字段齐、且文本可读。
        problems = [p for p in validate_clawtask(doc, check_integrity=False)
                    if not p.startswith("integrity_")]
        if problems:
            raise ValidationFailed("hibernate_invalid_doc",
                                  "任务文档不合格，拒绝封存：" + "；".join(problems))

        hib_id = uuid.uuid4().hex[:16]
        root = self._dir / hib_id
        files_dir = root / "artifacts"
        try:
            files_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DomainError("hibernate_failed", f"无法创建封存目录 {root}：{exc}") from exc

        # 1) 逐份复制产出物并记录 sha256（复制失败 = 整体失败，不留半截包）
        entries: list[dict[str, Any]] = []
        for i, raw in enumerate(artifacts or []):
            src = Path(raw)
            if not src.is_file():
                raise ValidationFailed("hibernate_artifact_missing", f"产出物不存在：{raw}")
            copy_name = f"{i:04d}_{src.name}"
            try:
                shutil.copy2(src, files_dir / copy_name)
            except OSError as exc:
                raise DomainError("hibernate_failed", f"无法封存 {raw}：{exc}") from exc
            entries.append({
                "name": src.name,
                "package_path": f"artifacts/{copy_name}",
                "sha256": _sha256_file(src),
                "size": src.stat().st_size,
            })

        # 2) 任务文档：portable=False（保留本机指针，好原地续跑）
        exported = serialize(doc, portable=False)
        (root / _PACKAGE_NAME).write_text(exported["text"], encoding="utf-8", newline="\n")

        owner = getattr(actor, "owner_id", "") or getattr(actor, "service_id", "")
        manifest = {
            "hibernation_id": hib_id,
            "owner_id": owner,
            "reason": reason[:200],
            "created_at": _now_iso(),
            "clawtask_version": exported["clawtask_version"],
            "task_digest": exported["digest"],
            "task_id": doc.get("id"),
            "kind": doc.get("kind"),
            "resume": doc.get("resume") or {},
            "local_only": doc.get("local_only") or {},
            "artifact_count": len(entries),
            "artifacts": entries,
            "policy": policy,
            "wake_hint": f"POST /api/clawtask/hibernations/{hib_id}/wake",
        }
        (root / _MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        if self.audit is not None:
            self.audit.append(actor, "clawtask.hibernated", hib_id, {
                "reason": reason[:120], "artifact_count": len(entries),
                "task_digest": exported["digest"], "triggers": policy["triggers"],
            })
        return {
            "hibernation_id": hib_id,
            "directory": str(root),
            "task_digest": exported["digest"],
            "artifact_count": len(entries),
            "policy": policy,
            "wake_hint": manifest["wake_hint"],
            "note": "已封存：任务状态 + 产出物完整打包，条件成熟后一键唤醒接着干。",
        }

    # -- 读面 ---------------------------------------------------------------
    def _manifests(self) -> list[dict[str, Any]]:
        if not self._dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob(f"*/{_MANIFEST_NAME}")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if isinstance(data, dict):
                out.append(data)
        return out

    def list_hibernations(self, actor: Actor) -> dict[str, Any]:
        actor.require_authenticated()
        me = getattr(actor, "owner_id", "") or getattr(actor, "service_id", "")
        rows = [m for m in self._manifests() if m.get("owner_id") == me]
        rows.sort(key=lambda m: str(m.get("created_at")), reverse=True)
        return {
            "items": [
                {
                    "hibernation_id": m.get("hibernation_id"),
                    "task_id": m.get("task_id"),
                    "reason": m.get("reason"),
                    "created_at": m.get("created_at"),
                    "artifact_count": m.get("artifact_count"),
                    "wake_hint": m.get("wake_hint"),
                }
                for m in rows
            ],
            "total": len(rows),
        }

    def _manifest_for(self, actor: Actor, hibernation_id: str) -> dict[str, Any]:
        me = getattr(actor, "owner_id", "") or getattr(actor, "service_id", "")
        for m in self._manifests():
            if m.get("hibernation_id") == hibernation_id and m.get("owner_id") == me:
                return m
        # 他人的封存包一律表现为「不存在」，不泄露存在性
        raise NotFound("hibernation_not_found", f"冬眠包不存在：{hibernation_id}")

    def inspect(self, actor: Actor, hibernation_id: str) -> dict[str, Any]:
        actor.require_authenticated()
        return self._manifest_for(actor, hibernation_id)

    # -- 唤醒 ---------------------------------------------------------------
    def wake(self, actor: Actor, hibernation_id: str) -> dict[str, Any]:
        """一键唤醒：**先校验完整性，再恢复**；任何一份被改动就拒绝。"""
        actor.require_authenticated()
        manifest = self._manifest_for(actor, hibernation_id)
        root = self._dir / hibernation_id
        task_path = root / _PACKAGE_NAME
        if not task_path.is_file():
            raise NotFound("hibernation_package_missing",
                           f"冬眠包缺少 {_PACKAGE_NAME}：{hibernation_id}")

        # 1) 产出物逐份校验（先全部过，再恢复——不做半截唤醒）
        results: list[dict[str, Any]] = []
        for entry in manifest.get("artifacts", []):
            pkg = root / str(entry.get("package_path"))
            if not pkg.is_file():
                raise DomainError("hibernation_integrity",
                                  f"产出物缺失：{entry.get('name')}；拒绝唤醒")
            actual = _sha256_file(pkg)
            if actual != entry.get("sha256"):
                raise DomainError(
                    "hibernation_integrity",
                    f"产出物 {entry.get('name')} 校验失败（包内文件被改动）；拒绝唤醒",
                )
            results.append({
                "name": entry.get("name"),
                "package_path": str(entry.get("package_path")),
                "sha256": actual,
                "size": entry.get("size"),
                "restorable": True,
            })

        # 2) 任务文档摘要校验
        doc = parse(task_path.read_text(encoding="utf-8"), verify=True)
        if manifest.get("task_digest") and manifest["task_digest"] != doc.get("integrity", {}).get("digest"):
            raise DomainError("hibernation_integrity",
                              "任务文档摘要与 manifest 不符；拒绝唤醒")

        if self.audit is not None:
            self.audit.append(actor, "clawtask.woken", hibernation_id,
                              {"artifact_count": len(results)})
        return {
            "hibernation_id": hibernation_id,
            "awakened": True,
            "doc": doc,
            "artifacts": results,
            "resume": manifest.get("resume") or {},
            "local_only": manifest.get("local_only") or {},
            "note": "已唤醒：任务文档与产出物完整性均通过，可原地或换地方接着干。",
        }

    def discard(self, actor: Actor, hibernation_id: str) -> dict[str, Any]:
        """丢弃冬眠包（用户明确不要了）。仅供 owner 本人。"""
        actor.require_authenticated()
        self._manifest_for(actor, hibernation_id)
        shutil.rmtree(self._dir / hibernation_id, ignore_errors=True)
        if self.audit is not None:
            self.audit.append(actor, "clawtask.hibernation_discarded", hibernation_id, {})
        return {"hibernation_id": hibernation_id, "discarded": True}


__all__ = [
    "HibernationStore", "hibernation_dir", "hibernation_policy",
    "WARN_PERCENT", "FORCE_PERCENT",
]
