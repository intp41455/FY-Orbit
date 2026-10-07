"""日志分级与可导出（A-基座质保-10）。

需求原文五条验收，逐条落地：

| 验收 | 落点 |
|---|---|
| ① 五级分级齐全、级别可配置 | :data:`LEVELS` + :func:`level_rules`（可被 JSON 覆盖） |
| ② 按界面 / 项目 / 时间 / 级别 / actor 五维筛选 | :meth:`LogService.query` |
| ③ 一键导出排查包（日志 + 留痕 + 保存点清单），通用可读格式 | :meth:`LogService.export_package` |
| ④ 导出前自动脱敏凭据与密钥，导出内容可预览 | :func:`redact` + ``preview`` |
| ⑤ 导出行为本身进留痕 | 导出后挂 ``quality.export_created`` 审计帧 |
| ⑥ 与既有审计链同源，不新建第二套日志 | 唯一数据源 = ``audit_events`` |

**时间维的诚实说明**：``audit_events`` **没有时间戳列**——哈希链用 ``seq`` 定序
（这是既有设计，不是本模块造的）。所以时间维是**序号窗口**（``since_seq`` /
``until_seq``），响应里用 ``time_axis="seq"`` 明说，并逐条给出 ``at: null``，
不编造墙钟时间。真正需要墙钟排查时，**保存点清单**（``work_stashes.created_at``）
带真实时间戳，导出包里一并给出。若要给审计帧加 ``created_at``，属迁移事项
（该列不在哈希 body 内，不会改变既有摘要），需向主控申领编号。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..actor import Actor
from ..audit import AuditService
from ..errors import NotFound, ValidationFailed
from ...db.models import AuditEvent
from ...db.staging_models import WorkStash

#: 五级分级（需求 ①）。顺序即严重度降序。
LEVELS: tuple[str, ...] = ("error", "warning", "info", "debug", "change")

#: 出厂分级规则：动作名（小写）含 token 即判该级。**可被 JSON 覆盖**（需求 ①）。
DEFAULT_LEVEL_RULES: dict[str, tuple[str, ...]] = {
    "error": ("failed", "error", "denied", "rejected", "integrity", "tampered"),
    "warning": ("warn", "retry", "degraded", "exceeded", "expired", "blocked"),
    "debug": ("debug", "probe", "diagnostic"),
    "info": ("info", "listed", "viewed", "read_only"),
    # change = 兜底：审计帧本身就是「变更留痕」。
    "change": (),
}

#: 脱敏：键名命中即整值抹掉（大小写不敏感、子串匹配）。
SENSITIVE_KEY_PATTERNS: tuple[str, ...] = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "authorization",
    "cookie", "private_key", "credential", "csrf", "session_id", "connection_string",
)

#: 脱敏：字符串值里命中即替换（连接串 / 私钥头 / bearer）。
SENSITIVE_VALUE_PATTERNS: tuple[str, ...] = (
    "postgres://", "mysql://", "mongodb://", "redis://", "amqp://",
    "-----BEGIN", "bearer ", "sk-", "ghp_", "AKIA",
)

REDACTED = "[已脱敏]"

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 500
MAX_EXPORT_ROWS = 20000


def quality_dir() -> Path:
    return Path(os.environ.get("FY_QUALITY_DIR", ".runtime/quality"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def level_rules(path: str | os.PathLike[str] | None = None) -> dict[str, tuple[str, ...]]:
    """取分级规则：默认出厂规则，可被 ``FY_QUALITY_LEVEL_RULES`` 指向的 JSON 覆盖。"""
    raw_path = path or os.environ.get("FY_QUALITY_LEVEL_RULES")
    if not raw_path:
        return {k: tuple(v) for k, v in DEFAULT_LEVEL_RULES.items()}
    p = Path(raw_path)
    if not p.is_file():
        raise ValidationFailed("level_rules_missing",
                              f"分级规则文件不存在：{p}（设置了 FY_QUALITY_LEVEL_RULES）")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationFailed("level_rules_invalid", "分级规则必须是 {级别: [token...]}")
    rules = {k: tuple(v) for k, v in DEFAULT_LEVEL_RULES.items()}
    for key, tokens in data.items():
        if key not in LEVELS:
            raise ValidationFailed("level_unknown", f"未知级别 {key!r}；可选 {list(LEVELS)}")
        if not isinstance(tokens, list) or not all(isinstance(t, str) for t in tokens):
            raise ValidationFailed("level_tokens_invalid", f"级别 {key} 的 token 必须是字符串列表")
        rules[key] = tuple(tokens)
    return rules


def classify(action: str, rules: dict[str, tuple[str, ...]] | None = None) -> str:
    """把审计动作归到五级之一（顺序 = error → warning → debug → info → change）。"""
    text = (action or "").lower()
    table = rules or level_rules()
    for level in ("error", "warning", "debug", "info"):
        if any(token and token in text for token in table.get(level, ())):
            return level
    return "change"


def redact(value: Any, *, _key: str = "") -> tuple[Any, int]:
    """递归脱敏，返回 ``(脱敏后的值, 命中次数)``。

    键名命中 :data:`SENSITIVE_KEY_PATTERNS` → **整值**替换；字符串值命中
    :data:`SENSITIVE_VALUE_PATTERNS` → 整串替换。**先脱敏再落盘**，
    所以导出包里不可能出现凭据（fail safe 的方向是「宁可多抹」）。
    """
    hits = 0
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if any(p in str(key).lower() for p in SENSITIVE_KEY_PATTERNS):
                out[key] = REDACTED
                hits += 1
                continue
            cleaned, n = redact(item, _key=str(key))
            out[key] = cleaned
            hits += n
        return out, hits
    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            cleaned, n = redact(item, _key=_key)
            cleaned_list.append(cleaned)
            hits += n
        return cleaned_list, hits
    if isinstance(value, str):
        lowered = value.lower()
        if any(p.lower() in lowered for p in SENSITIVE_VALUE_PATTERNS):
            return REDACTED, hooks(value)
        return value, 0
    return value, 0


def hooks(value: str) -> int:
    """命中的敏感片段个数（用于报告「抹了多少处」，不泄露内容）。"""
    lowered = value.lower()
    return sum(1 for p in SENSITIVE_VALUE_PATTERNS if p.lower() in lowered) or 1


def _surface_of(details: dict[str, Any]) -> str | None:
    for key in ("surface", "ui", "page", "screen"):
        val = details.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _project_of(details: dict[str, Any], target: str | None) -> str | None:
    for key in ("project", "project_id", "workspace"):
        val = details.get(key)
        if isinstance(val, str) and val:
            return val
    if isinstance(target, str) and target:
        return target.split(":")[0]
    val = details.get("task_id")
    return val if isinstance(val, str) and val else None


class LogService:
    """审计链的**分级视图** + 排查包导出。不写第二套日志。"""

    def __init__(self, session: Session, *, audit: AuditService | None = None,
                 directory: str | os.PathLike[str] | None = None) -> None:
        self.s = session
        self.audit = audit
        self._dir = Path(directory) if directory else quality_dir()

    # -- 分级目录 -----------------------------------------------------------
    def levels(self) -> dict[str, Any]:
        rules = level_rules()
        return {
            "levels": [
                {"id": level, "tokens": list(rules.get(level, ()))}
                for level in LEVELS
            ],
            "default_level": "change",
            "configurable": True,
            "config_env": "FY_QUALITY_LEVEL_RULES",
            "source": "audit_events（唯一真源，不新建第二套日志）",
            "time_axis": "seq",
            "time_note": ("审计帧没有时间戳列，时间维是序号窗口；"
                          "保存点清单带真实时间戳，见导出包 savepoints.json"),
        }

    # -- 查询（五维筛选）----------------------------------------------------
    def _rows(self, actor: Actor) -> list[AuditEvent]:
        identity = AuditService.identity_of(actor)
        if identity is None:
            return []
        return list(self.s.execute(
            select(AuditEvent).where(AuditEvent.actor == identity)
            .order_by(AuditEvent.seq.asc())
        ).scalars())

    def query(self, actor: Actor, *, level: str | None = None, actor_filter: str | None = None,
              surface: str | None = None, project: str | None = None,
              since_seq: int | None = None, until_seq: int | None = None,
              limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0,
              redact_output: bool = True) -> dict[str, Any]:
        """五维筛选：级别 / actor / 界面 / 项目 / 时间（序号轴）。"""
        actor.require_authenticated()
        if level is not None and level not in LEVELS:
            raise ValidationFailed("level_unknown", f"未知级别 {level!r}；可选 {list(LEVELS)}")
        for name, val in (("limit", limit), ("offset", offset)):
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                raise ValidationFailed(f"{name}_invalid", f"{name} 必须是 >= 0 的整数")
        if limit < 1:
            raise ValidationFailed("page_limit_invalid", "limit 必须是 >= 1 的整数")
        # 两层上界：HTTP 分页上界是 MAX_PAGE_LIMIT（由路由的 Query(le=...) 把住），
        # 服务层的**绝对**上界是 MAX_EXPORT_ROWS——导出排查包要用满它，
        # 所以这里不能把导出需要的行数也按分页上界拒掉。
        if limit > MAX_EXPORT_ROWS:
            raise ValidationFailed(
                "page_limit_exceeded",
                f"limit 超过绝对上界 {MAX_EXPORT_ROWS}（HTTP 分页上界为 {MAX_PAGE_LIMIT}）",
            )

        rules = level_rules()
        items: list[dict[str, Any]] = []
        redactions = 0
        for row in self._rows(actor):
            if since_seq is not None and row.seq < since_seq:
                continue
            if until_seq is not None and row.seq > until_seq:
                continue
            if actor_filter is not None and row.actor != actor_filter:
                continue
            row_level = classify(row.action, rules)
            if level is not None and row_level != level:
                continue
            details = row.details or {}
            row_surface = _surface_of(details)
            row_project = _project_of(details, row.target)
            if surface is not None and row_surface != surface:
                continue
            if project is not None and row_project != project:
                continue
            payload: dict[str, Any] = {
                "seq": row.seq,
                "at": None,               # 审计帧无时间戳：不编造
                "level": row_level,
                "actor": row.actor,
                "action": row.action,
                "target": row.target,
                "surface": row_surface,
                "project": row_project,
                "details": details,
            }
            if redact_output:
                cleaned, n = redact(payload)
                payload = cleaned
                redactions += n
            items.append(payload)

        total = len(items)
        return {
            "items": items[offset:offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": {"level": level, "actor": actor_filter, "surface": surface,
                        "project": project, "since_seq": since_seq, "until_seq": until_seq},
            "time_axis": "seq",
            "redacted_count": redactions,
            "levels": list(LEVELS),
        }

    # -- 保存点清单（有真实时间戳）-----------------------------------------
    def savepoints(self, actor: Actor, *, limit: int = 500) -> list[dict[str, Any]]:
        """保存点清单：来自 ``work_stashes`` 里的写前快照记录（需求 ③）。"""
        if not actor.owner_id:
            return []
        rows = list(self.s.execute(
            select(WorkStash).where(WorkStash.owner_id == actor.owner_id)
            .order_by(WorkStash.created_at.desc()).limit(limit)
        ).scalars())
        out: list[dict[str, Any]] = []
        for row in rows:
            meta = row.stash_metadata or {}
            if meta.get("kind") != "pre_write_snapshot":
                continue
            out.append({
                "stash_id": row.id,
                "title": row.title,
                "snapshot_id": meta.get("snapshot_id"),
                "task_id": meta.get("task_id"),
                "file_count": meta.get("files"),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })
        return out

    # -- 一键导出排查包 -----------------------------------------------------
    def export_package(self, actor: Actor, *, level: str | None = None,
                       surface: str | None = None, project: str | None = None,
                       since_seq: int | None = None, until_seq: int | None = None,
                       preview: bool = True) -> dict[str, Any]:
        """导出可交付的排查包：日志 + 留痕 + 保存点清单（先脱敏，再落盘）。"""
        queried = self.query(actor, level=level, surface=surface, project=project,
                             since_seq=since_seq, until_seq=until_seq,
                             limit=MAX_EXPORT_ROWS, offset=0, redact_output=True)
        # 二次脱敏是刻意的：导出是本模块唯一会「把数据带出本机」的动作，
        # 摆在最后一道，确保任何新增字段也过一遍。
        rows, hits = redact(queried["items"])
        logs = {
            "generated_at": _now_iso(),
            "time_axis": "seq",
            "filters": queried["filters"],
            "count": len(rows),
            "truncated": queried["total"] > MAX_EXPORT_ROWS,
            "entries": rows,
        }
        # 保存点清单同样先脱敏再落盘：导出包是唯一会「把数据带出本机」的动作。
        savepoints, sp_hits = redact(self.savepoints(actor))

        export_id = uuid.uuid4().hex[:16]
        root = self._dir / "exports" / export_id
        root.mkdir(parents=True, exist_ok=True)
        (root / "logs.json").write_text(
            json.dumps(logs, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n")
        (root / "savepoints.json").write_text(
            json.dumps({"count": len(savepoints), "items": savepoints},
                       ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n")
        manifest = {
            "export_id": export_id,
            "owner_id": actor.owner_id,
            "generated_at": _now_iso(),
            "format": "json",
            "files": ["logs.json", "savepoints.json", "manifest.json"],
            "log_count": len(rows),
            "savepoint_count": len(savepoints),
            "redactions": hits + sp_hits,
            "redaction_policy": {
                "key_patterns": list(SENSITIVE_KEY_PATTERNS),
                "value_patterns": list(SENSITIVE_VALUE_PATTERNS),
                "replacement": REDACTED,
            },
            "source": "audit_events + work_stashes",
            "contains_credentials": False,
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n")

        # 需求 ⑤：导出行为本身进留痕
        if self.audit is not None:
            self.audit.append(actor, "quality.export_created", export_id, {
                "log_count": len(rows), "savepoint_count": len(savepoints),
                "redactions": manifest["redactions"], "filters": queried["filters"],
            })

        result: dict[str, Any] = {
            "export_id": export_id,
            "directory": str(root),
            "files": manifest["files"],
            "log_count": len(rows),
            "savepoint_count": len(savepoints),
            "redactions": manifest["redactions"],
            "contains_credentials": False,
            "note": "导出前已自动脱敏凭据与密钥；导出行为已进留痕。",
        }
        if preview:
            result["preview"] = {
                "manifest": manifest,
                "sample": rows[:5],
            }
        return result

    def list_exports(self, actor: Actor) -> dict[str, Any]:
        """本 owner 导出过的排查包。"""
        actor.require_authenticated()
        base = self._dir / "exports"
        items: list[dict[str, Any]] = []
        if base.is_dir():
            for child in sorted(base.iterdir(), reverse=True):
                path = child / "manifest.json"
                if not path.is_file():
                    continue
                try:
                    meta = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                if meta.get("owner_id") != actor.owner_id:
                    continue
                items.append(meta)
        return {"items": items, "total": len(items)}

    def export_detail(self, actor: Actor, export_id: str) -> dict[str, Any]:
        """读回一个排查包（owner 隔离：他人导出表现为不存在）。"""
        actor.require_authenticated()
        path = self._dir / "exports" / export_id
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            raise NotFound("export_not_found", f"排查包不存在：{export_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("owner_id") != actor.owner_id:
            raise NotFound("export_not_found", f"排查包不存在：{export_id}")
        logs = json.loads((path / "logs.json").read_text(encoding="utf-8"))
        savepoints = json.loads((path / "savepoints.json").read_text(encoding="utf-8"))
        return {"manifest": manifest, "logs": logs, "savepoints": savepoints}


__all__ = [
    "LogService", "LEVELS", "DEFAULT_LEVEL_RULES", "REDACTED", "quality_dir",
    "level_rules", "classify", "redact",
    "DEFAULT_PAGE_LIMIT", "MAX_PAGE_LIMIT", "MAX_EXPORT_ROWS",
]
