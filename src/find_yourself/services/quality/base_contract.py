"""基座接入强制校验（A-基座质保-11 / W1，上市门禁）。

需求验收逐条落地：

| 验收 | 落点 |
|---|---|
| ① 提供统一接入声明或钩子（``useBase()`` / ``BaseBound``），未接入的**可编辑界面**编译 / CI 失败 | :data:`DECLARATION_MARKERS` + :meth:`BaseContractService.audit_tree`（返回 ``ok=False`` + ``ci.exit_code=1``） |
| ② 覆盖工作台 / 个人空间 / 所有标签导航页与组件 | 扫描面 = ``web/src/pages`` + ``web/src/components``（只排除测试 / 快照 / 基座自身原语） |
| ③ 豁免清单与审批流程（仅限明确非编辑态的纯展示页），豁免需记录理由 | :data:`EXEMPTION_SCOPES` + :meth:`request_exemption` / :meth:`approve_exemption` / :meth:`revoke_exemption`（**未审批的豁免不生效**） |
| ④ 校验失败信息明确指出「缺哪个基座能力 + 如何接入」 | 每条违规带 ``missing_capabilities`` + ``how_to_fix``（来自 :data:`BASE_CAPABILITIES` 的 ``how``） |

三条设计纪律
------------

1. **门禁必须能真的失败**。``audit_tree`` 对**未接入**的文件返回 ``ok=False``，并给出
   ``ci.exit_code=1``；本仓现状（21 个页面尚未接线）因此会**如实报红**——这不是本模块
   的缺陷，而是「全界面接线」尚未完成的真实状态，报告里逐页列出即接线清单。
   任何「让门禁先绿」的写法（默认放过、只告警不失败）都会让基座形同虚设。
2. **豁免不是「不想改就绕过」**。豁免请求必须写明理由（>= 10 字），且必须**由 owner
   审批**后才生效；未审批（pending）/ 已撤回（revoked）的豁免**不豁免任何违规**。
   另有一条硬规则：**可编辑文件不得豁免**（需求原文：豁免仅限明确非编辑态的纯展示页）。
3. **零新表**。豁免清单落 ``.runtime/quality/base_exemptions.json``（与
   ``services/snapshot.py`` 落 ``.runtime/snapshots`` 同范式），审批动作进既有审计哈希链。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..actor import Actor
from ..audit import AuditService
from ..errors import NotFound, ValidationFailed
from .logs import quality_dir

#: 基座接入契约版本（前端 ``useBase`` / ``BaseBound`` 按此实现）。
BASE_CONTRACT_VERSION = "1.0.0"

#: 四项基座能力 = 需求原文的四个括号（实时保存 + 留痕 + 本地优先 + 统一错误回执）。
#: ``how`` 就是验收 ④ 要求的「如何接入」，即失败信息里直接给用户看的那句话。
BASE_CAPABILITIES: tuple[dict[str, str], ...] = (
    {
        "id": "realtime_save",
        "label": "全界面实时保存",
        "hook": "useAutosave({ key, value, save })",
        "how": "在页面里调用 useAutosave({ key, value: draft, save }) 把草稿交出去；"
               "或用 <BaseBound surface=\"…\"> 包住页面主体。",
    },
    {
        "id": "audit_trail",
        "label": "留痕",
        "hook": "useBase().audit",
        "how": "在 useBase 声明里保留 audit_trail；每次改动后调用 base.audit.record()，"
               "写入既有审计哈希链（不是第二套日志）。",
    },
    {
        "id": "local_first",
        "label": "本地优先",
        "hook": "useBase().storage",
        "how": "在 useBase 声明里保留 local_first；存储路径走 base.storage，"
               "默认本地落盘，云端不可达时不得阻塞编辑。",
    },
    {
        "id": "error_receipt",
        "label": "统一错误回执",
        "hook": "useBase().errors",
        "how": "在 useBase 声明里保留 error_receipt；把 IO / 网络失败交给 base.errors，"
               "由它产出结构化回执（含 code/message/hint），不要各写一套提示。",
    },
)

BASE_CAPABILITY_IDS: tuple[str, ...] = tuple(c["id"] for c in BASE_CAPABILITIES)

#: 声明标记：任一出现即视为「已接入基座」（验收 ① 的声明或钩子）。
DECLARATION_MARKERS: tuple[str, ...] = ("useBase(", "<BaseBound")

#: 判定「可编辑」的源码标记。用于验收 ③ 的硬规则：可编辑文件不得豁免。
EDITABLE_MARKERS: tuple[str, ...] = (
    "<input", "<textarea", "<select", "contentEditable", "contenteditable",
    "onChange=", "onInput=", "useAutosave(",
)

#: 豁免范围白名单（需求原文只有一档：非编辑态的纯展示页）。
EXEMPTION_SCOPES: tuple[str, ...] = ("pure_display",)

#: 豁免理由的最短长度（「豁免需记录理由」——理由不能是一两个字糊弄过去）。
MIN_EXEMPTION_REASON = 10

#: 扫描面：页面与组件（验收 ②）。相对仓库根。
DEFAULT_SCAN_DIRS: tuple[str, ...] = ("web/src/pages", "web/src/components")

#: 不纳入扫描的路径片段（测试 / 快照 / 夹具 / 基座自身原语）。
SKIP_PATH_PARTS: tuple[str, ...] = (
    "node_modules", "dist", "__fixtures__", "__snapshots__",
    "components/ui",            # 基座自身的原语就在这里，要求它们接入自己是循环
)
SKIP_SUFFIXES: tuple[str, ...] = (".test.tsx", ".test.ts", ".spec.tsx", ".spec.ts", ".d.ts")

#: 从源码里解析 ``capabilities: [...]`` 声明列表。
_CAP_LIST_RE = re.compile(r"capabilities\s*:\s*\[([^\]]*)\]")
#: 从源码里解析 ``surface: 'xxx'`` / ``surface="xxx"``。
_SURFACE_RE = re.compile(r"""surface\s*[:=]\s*['"]([^'"]+)['"]""")
#: 字符串字面量（用于把 capabilities 列表里的 'a', "b" 拆出来）。
_STRING_RE = re.compile(r"""['"]([^'"]+)['"]""")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    """仓库根：``src/find_yourself/services/quality/base_contract.py`` 上溯 4 层。"""
    return Path(__file__).resolve().parents[4]


def scan_root() -> Path:
    """扫描根目录（默认仓库根；测试与 CI 可用 ``FY_BASE_SCAN_ROOT`` 覆盖）。"""
    override = os.environ.get("FY_BASE_SCAN_ROOT")
    return Path(override) if override else repo_root()


def exemption_file() -> Path:
    override = os.environ.get("FY_BASE_EXEMPTIONS")
    return Path(override) if override else quality_dir() / "base_exemptions.json"


def base_contract() -> dict[str, Any]:
    """契约自描述（前端与 CI 共用一份，不是文档里另抄一份）。"""
    return {
        "contract_version": BASE_CONTRACT_VERSION,
        "capabilities": [dict(c) for c in BASE_CAPABILITIES],
        "declaration": {
            "markers": list(DECLARATION_MARKERS),
            "hook": "useBase({ surface, capabilities })",
            "component": "<BaseBound surface=\"…\"> 包住页面主体",
            "shorthand": "capabilities 省略 = 声明全部四项",
        },
        "scan": {
            "dirs": list(DEFAULT_SCAN_DIRS),
            "skip_path_parts": list(SKIP_PATH_PARTS),
            "skip_suffixes": list(SKIP_SUFFIXES),
            "editable_markers": list(EDITABLE_MARKERS),
        },
        "exemptions": {
            "scopes": list(EXEMPTION_SCOPES),
            "min_reason_chars": MIN_EXEMPTION_REASON,
            "approval": "必须由 owner 审批（require_owner）；pending / revoked 不生效",
            "rule": "可编辑文件不得豁免（仅限明确非编辑态的纯展示页）",
            "file": str(exemption_file()),
        },
    }


# --------------------------------------------------------------------------- #
# 源码判定（纯函数，可单独测）
# --------------------------------------------------------------------------- #
def is_editable(source: str) -> bool:
    """按源码标记判定「这是可编辑界面」（启发式，用于豁免硬规则与统计）。"""
    return any(marker in source for marker in EDITABLE_MARKERS)


def is_declared(source: str) -> bool:
    return any(marker in source for marker in DECLARATION_MARKERS)


def declared_capabilities(source: str) -> tuple[list[str], list[str]]:
    """解析声明里的能力列表，返回 ``(已声明, 未知)``。

    未写 ``capabilities`` 列表 = 简写，视为声明全部四项（契约里的 ``shorthand``）。
    """
    match = _CAP_LIST_RE.search(source)
    if match is None:
        return list(BASE_CAPABILITY_IDS), []
    ids = _STRING_RE.findall(match.group(1))
    known = [i for i in ids if i in BASE_CAPABILITY_IDS]
    unknown = [i for i in ids if i not in BASE_CAPABILITY_IDS]
    return known, unknown


def declared_surface(source: str) -> str | None:
    match = _SURFACE_RE.search(source)
    return match.group(1) if match else None


def _is_scanned(path: Path, *, root: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    if any(part in rel for part in SKIP_PATH_PARTS):
        return False
    if path.name.endswith(SKIP_SUFFIXES):
        return False
    return path.suffix in {".tsx", ".ts"}


def _iter_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*") if p.is_file())


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #
class BaseContractService:
    """接入校验 + 豁免清单（清单落文件，审批进审计链；零新表）。"""

    def __init__(self, session: Session, *, audit: AuditService | None = None,
                 exemptions_path: str | os.PathLike[str] | None = None,
                 root: str | os.PathLike[str] | None = None) -> None:
        self.s = session
        self.audit = audit
        self._exemptions_path = Path(exemptions_path) if exemptions_path else exemption_file()
        # 扫描根：默认仓库根（或 FY_BASE_SCAN_ROOT）；测试直接注入临时目录。
        self._root = Path(root) if root else scan_root()

    # -- 豁免清单读写 ------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        path = self._exemptions_path
        if not path.is_file():
            return {"version": BASE_CONTRACT_VERSION, "records": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValidationFailed("exemptions_invalid",
                                   f"豁免清单不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ValidationFailed("exemptions_invalid", "豁免清单必须含 records 列表")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        path = self._exemptions_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )

    def _latest(self) -> dict[str, dict[str, Any]]:
        """每个 path 取**最后一条**记录（撤回后再申请以新记录生效）。"""
        out: dict[str, dict[str, Any]] = {}
        for record in self._load()["records"]:
            if isinstance(record, dict) and isinstance(record.get("path"), str):
                out[record["path"]] = record
        return out

    @staticmethod
    def _norm(rel_path: str) -> str:
        return rel_path.replace("\\", "/").strip().lstrip("./")

    def exemptions(self, actor: Actor) -> dict[str, Any]:
        actor.require_authenticated()
        latest = self._latest()
        items = [dict(v) for v in latest.values()]
        items.sort(key=lambda r: r.get("path", ""))
        return {
            "items": items,
            "total": len(items),
            "approved": [r["path"] for r in items if r.get("status") == "approved"],
            "pending": [r["path"] for r in items if r.get("status") == "pending"],
            "scopes": list(EXEMPTION_SCOPES),
            "rule": "可编辑文件不得豁免；pending / revoked 的豁免不生效",
            "file": str(self._exemptions_path),
        }

    def request_exemption(self, actor: Actor, *, path: str, reason: str,
                          scope: str = "pure_display") -> dict[str, Any]:
        actor.require_owner()
        rel = self._norm(path)
        if not rel:
            raise ValidationFailed("exemption_path_missing", "必须给出要豁免的文件路径")
        if scope not in EXEMPTION_SCOPES:
            raise ValidationFailed("exemption_scope_invalid",
                                   f"豁免范围只能是 {list(EXEMPTION_SCOPES)}")
        text = (reason or "").strip()
        if len(text) < MIN_EXEMPTION_REASON:
            raise ValidationFailed(
                "exemption_reason_missing",
                f"豁免必须记录理由（至少 {MIN_EXEMPTION_REASON} 个字）",
            )
        source = self._read_source(rel)
        if source is not None and is_editable(source):
            raise ValidationFailed(
                "exemption_not_applicable",
                f"{rel} 含可编辑控件，属可编辑界面，不得豁免——请接入 useBase / BaseBound",
            )
        record = {
            "path": rel, "scope": scope, "reason": text, "status": "pending",
            "requested_by": actor.owner_id or actor.service_id,
            "requested_at": _now_iso(),
            "approved_by": None, "approved_at": None, "note": None,
        }
        data = self._load()
        data["records"].append(record)
        self._save(data)
        if self.audit is not None:
            self.audit.append(actor, "quality.exemption_requested", rel,
                              {"scope": scope, "reason": text})
        return {"exemption": record,
                "note": "已登记为待审批；审批通过前该文件仍会判为违规（门禁不放行）。"}

    def approve_exemption(self, actor: Actor, *, path: str,
                          note: str | None = None) -> dict[str, Any]:
        actor.require_owner()
        rel = self._norm(path)
        latest = self._latest()
        if rel not in latest:
            raise NotFound("exemption_not_found", f"没有待审批的豁免：{rel}")
        record = {
            **latest[rel],
            "status": "approved",
            "approved_by": actor.owner_id or actor.service_id,
            "approved_at": _now_iso(),
            "note": note,
        }
        data = self._load()
        data["records"].append(record)
        self._save(data)
        if self.audit is not None:
            self.audit.append(actor, "quality.exemption_approved", rel,
                              {"reason": record.get("reason"), "note": note})
        return {"exemption": record}

    def revoke_exemption(self, actor: Actor, *, path: str,
                         note: str | None = None) -> dict[str, Any]:
        actor.require_owner()
        rel = self._norm(path)
        latest = self._latest()
        if rel not in latest:
            raise NotFound("exemption_not_found", f"没有可撤回的豁免：{rel}")
        record = {
            **latest[rel],
            "status": "revoked",
            "revoked_by": actor.owner_id or actor.service_id,
            "revoked_at": _now_iso(),
            "note": note,
        }
        data = self._load()
        data["records"].append(record)
        self._save(data)
        if self.audit is not None:
            self.audit.append(actor, "quality.exemption_revoked", rel, {"note": note})
        return {"exemption": record}

    # -- 扫描（CI 门禁）----------------------------------------------------
    @property
    def root(self) -> Path:
        return self._root

    def _read_source(self, rel_path: str) -> str | None:
        candidate = self._root / rel_path
        if not candidate.is_file():
            return None
        try:
            return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def audit_tree(self, actor: Actor, *, root: str | os.PathLike[str] | None = None,
                   dirs: list[str] | None = None) -> dict[str, Any]:
        """扫描工作台 / 个人空间 / 各页面与组件，返回**可作 CI 判定**的结果。"""
        actor.require_authenticated()
        base = Path(root) if root else self._root
        targets = dirs if dirs is not None else list(DEFAULT_SCAN_DIRS)

        files: list[Path] = []
        for rel in targets:
            d = base / rel
            if d.is_file():
                files.append(d)
                continue
            for path in _iter_files(d):
                if _is_scanned(path, root=base):
                    files.append(path)

        approved = {p for p, r in self._latest().items() if r.get("status") == "approved"}
        by_status = {p: r.get("status") for p, r in self._latest().items()}

        violations: list[dict[str, Any]] = []
        advisories: list[dict[str, Any]] = []
        declared_count = 0
        editable_count = 0
        exempted_count = 0
        display_only = 0

        for path in sorted(set(files)):
            rel = path.relative_to(base).as_posix()
            is_page = "/pages/" in f"/{rel}"
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # 读不了就当违规，不静默跳过
                violations.append({
                    "path": rel, "kind": "unreadable",
                    "message": f"无法读取源文件：{exc}",
                    "missing_capabilities": [], "how_to_fix": [],
                })
                continue

            editable = is_editable(source)
            if editable:
                editable_count += 1

            # 阻塞口径：**页面**（App.tsx 挂载单位）一律阻塞；**组件**里只有
            # 「可编辑」的那些阻塞——纯展示组件随所属页面继承基座，仍逐条列为
            # 接线清单（advisory），但不拦合并，避免门禁被上百个展示件淹没。
            sink = violations if (is_page or editable) else advisories

            if is_declared(source):
                known, unknown = declared_capabilities(source)
                if unknown:
                    sink.append({
                        **self._violation(rel, "capability_unknown",
                                          "声明里出现未知能力 id，无法确认基座能力齐全。"),
                        "unknown_capabilities": unknown,
                    })
                missing = [c for c in BASE_CAPABILITY_IDS if c not in known]
                if missing:
                    sink.append({
                        **self._violation(rel, "capability_missing",
                                          "已接入基座，但声明的能力不齐（不要只写一半）。"),
                        "missing_capabilities": missing,
                    })
                    continue
                if not unknown:
                    declared_count += 1
                continue

            if rel in approved:
                exempted_count += 1
                continue
            if by_status.get(rel) == "pending":
                sink.append(self._violation(
                    rel, "exemption_pending",
                    "该文件已登记豁免但尚未审批；未审批的豁免不生效。"))
                continue
            if not editable:
                display_only += 1
                sink.append(self._violation(
                    rel, "base_not_declared",
                    "未接入基座：纯展示页 / 展示组件也必须登记豁免并写明理由，"
                    "才能进入白名单（证明「确认过、确实不编辑」）。"))
                continue
            sink.append(self._violation(
                rel, "base_not_declared",
                "可编辑界面未接入基座：既没有 useBase(...) / <BaseBound>，也没有已审批的豁免。"))

        ok = not violations
        return {
            "ok": ok,
            "blocking": not ok,
            "root": str(base),
            "scanned_dirs": targets,
            "scanned": len(set(files)),
            "editable": editable_count,
            "declared": declared_count,
            "exempted": exempted_count,
            "display_only": display_only,
            "violations": violations,
            "violation_count": len(violations),
            "advisories": advisories,
            "advisory_count": len(advisories),
            "capabilities": [dict(c) for c in BASE_CAPABILITIES],
            "contract_version": BASE_CONTRACT_VERSION,
            "ci": {
                "exit_code": 0 if ok else 1,
                "reason": ("全部页面与可编辑组件已接入基座" if ok
                           else f"{len(violations)} 个文件未满足基座接入校验（编译 / CI 应拒绝合并）"),
            },
            "checked_at": _now_iso(),
        }

    @staticmethod
    def _violation(rel: str, kind: str, message: str) -> dict[str, Any]:
        """违规条目：**必带**缺失的能力与「如何接入」（验收 ④）。"""
        return {
            "path": rel,
            "kind": kind,
            "message": message,
            "missing_capabilities": list(BASE_CAPABILITY_IDS),
            "how_to_fix": [
                {"capability": c["id"], "label": c["label"], "how": c["how"]}
                for c in BASE_CAPABILITIES
            ],
        }


__all__ = [
    "BASE_CONTRACT_VERSION", "BASE_CAPABILITIES", "BASE_CAPABILITY_IDS",
    "DECLARATION_MARKERS", "EDITABLE_MARKERS", "EXEMPTION_SCOPES",
    "MIN_EXEMPTION_REASON", "DEFAULT_SCAN_DIRS", "SKIP_PATH_PARTS", "SKIP_SUFFIXES",
    "repo_root", "scan_root", "exemption_file", "base_contract",
    "is_editable", "is_declared", "declared_capabilities", "declared_surface",
    "BaseContractService",
]
