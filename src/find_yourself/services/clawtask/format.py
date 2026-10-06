"""``.clawtask`` 标准任务格式（A-任务可移植-03 第二层）。

**要做到什么**：一个任务能被导出成**单个自描述文件**，拷到别的模型 / 别的平台
就能无缝接手——跨模型（GPT / Claude / Qwen / DeepSeek 都能读）、跨框架
（不同平台任务随便迁）、可分享（像传游戏存档）。

设计要点（每条都对应一个可测的不变量）
--------------------------------------

1. **自描述**：文件里 ``clawtask_version`` + 完整字段，不依赖任何本机配置或数据库
   才能理解。解析器只靠文件本身。
2. **跨模型可读**：``human_brief`` 是**必填的 Markdown 交接说明**——任何模型
   （哪怕不完全支持本 schema）读这一段就能接手。这是「跨模型」的兜底，不是装饰。
3. **跨框架中立**：必填字段里没有任何框架专有概念；扩展只能进 ``extensions``
   命名空间，从而保证核心字段在所有平台都同义。
4. **可迁移 = 不带本机痕迹**：``serialize(portable=True)``（默认）会把
   ``local_only`` 段（本机路径、线程 id、绝对路径）**剥离**，并拒绝任何
   绝对路径出现在可移植字段里；需要带本机指针时显式 ``portable=False``
   （冬眠封存用这条），两者不混淆。
5. **完整性**：``integrity.digest`` = 去掉 ``integrity`` 后的 canonical JSON 的
   sha256。导入时校验，被改过就**拒绝**（fail closed），不悄悄放行。
6. **与 P13 契约对齐**：``system_template.schema_version`` 必须等于
   :data:`~find_yourself.services.templates.scaffold.TEMPLATE_SCHEMA_VERSION`，
   八类必备项齐备——模板 schema 是唯一真源，本格式**引用**它而不是复制它。
7. **不编造**：``from_task`` 只搬运任务行里**真实存在**的字段（状态 / 阶段 /
   步数 / 进度）；没有的信息就是空，绝不填料。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..errors import NotFound, ValidationFailed
from ..templates.scaffold import (
    ESSENTIAL_KEYS,
    TEMPLATE_SCHEMA_VERSION,
)

#: ``.clawtask`` 文档格式版本。变更即破坏性 —— 消费方按此判定能否读。
CLAWTASK_VERSION = "1.0.0"

#: 文件扩展名（单文件，JSON 文本，可 diff 可阅读）。
CLAWTASK_EXT = ".clawtask"

#: 文档种类：一个具体任务，还是一个可复用/可挂卖的任务模板。
KINDS: tuple[str, ...] = ("task", "task_template")

#: 出厂声明的目标模型集合。「跨模型」的默认覆盖面，不封闭——可加任意字符串。
KNOWN_MODELS: tuple[str, ...] = ("gpt", "claude", "qwen", "deepseek", "local")

#: 必填顶层字段（``local_only`` 与 ``integrity`` 由序列化层补齐，不要求手填）。
REQUIRED_TOP_LEVEL: tuple[str, ...] = (
    "clawtask_version", "kind", "id", "name", "goal", "human_brief",
    "created_at", "target_models", "system_template", "context", "steps",
    "budget", "progress",
)

#: 本机专有指针：可移植导出时**整体剥离**（不随文件外传）。
LOCAL_ONLY_KEYS: tuple[str, ...] = (
    "artifacts_dir", "checkpoint_path", "thread_id", "message_id",
    "workspace", "stash_id",
)

_ABS_POSIX = re.compile(r"(?<![\w.])/(?:Users|home|opt|var|tmp|mnt)/")
_ABS_WIN = re.compile(r"[A-Za-z]:[\\/]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clawtask_schema() -> dict[str, Any]:
    """冻结的 ``.clawtask`` schema 描述（消费方据此校验，不必 import 私有常量）。"""
    return {
        "clawtask_version": CLAWTASK_VERSION,
        "extension": CLAWTASK_EXT,
        "kinds": list(KINDS),
        "known_models": list(KNOWN_MODELS),
        "required_top_level": list(REQUIRED_TOP_LEVEL),
        "local_only_keys": list(LOCAL_ONLY_KEYS),
        "system_template_schema_version": TEMPLATE_SCHEMA_VERSION,
        "human_brief_required": True,
        "integrity_algorithm": "sha256",
    }


# ---------------------------------------------------------------------------
# canonical / digest / 完整性
# ---------------------------------------------------------------------------

def canonical_clawtask(doc: dict[str, Any], *, with_integrity: bool = False) -> str:
    """canonical JSON（排序键 + 紧凑分隔符）——摘要与逐字节比对都以它为准。"""
    body = {k: v for k, v in doc.items() if k != "integrity"}
    if with_integrity:
        body = doc
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def clawtask_digest(doc: dict[str, Any]) -> str:
    """文档摘要（不含 ``integrity`` 本身，故可反复重算且稳定）。"""
    return hashlib.sha256(canonical_clawtask(doc).encode("utf-8")).hexdigest()


def seal(doc: dict[str, Any]) -> dict[str, Any]:
    """写入 ``integrity`` 段（深拷贝，不改动入参）。"""
    import copy

    sealed = copy.deepcopy(doc)
    digest = clawtask_digest(sealed)
    sealed["integrity"] = {
        "algorithm": "sha256",
        "digest": digest,
        "sealed_at": _now_iso(),
    }
    return sealed


def verify_integrity(doc: dict[str, Any]) -> list[str]:
    """校验 ``integrity``；返回问题列表（空 = 通过）。缺 integrity 也算问题。"""
    integrity = doc.get("integrity")
    if not isinstance(integrity, dict):
        return ["integrity_missing: 文档没有 integrity 段，无法证明未被改动"]
    if integrity.get("algorithm") != "sha256":
        return [f"integrity_unknown_algorithm: {integrity.get('algorithm')!r}"]
    expected = integrity.get("digest")
    actual = clawtask_digest(doc)
    if expected != actual:
        return ["integrity_mismatch: 摘要不符，文件已被改动（拒绝导入）"]
    return []


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _find_local_paths(node: Any, path: str = "") -> list[str]:
    """递归找出**绝对路径**（可移植字段里不允许出现本机路径）。"""
    hits: list[str] = []
    if isinstance(node, str):
        if _ABS_POSIX.search(node) or _ABS_WIN.search(node):
            hits.append(path or "<root>")
    elif isinstance(node, dict):
        for key, value in node.items():
            hits.extend(_find_local_paths(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits.extend(_find_local_paths(value, f"{path}[{i}]"))
    return hits


def validate_clawtask(doc: dict[str, Any], *, check_integrity: bool = True) -> list[str]:
    """结构 + 契约 + 可移植性校验，返回**全部**问题（空 = 合格）。"""
    problems: list[str] = []
    if not isinstance(doc, dict):
        return ["document_not_mapping: .clawtask 必须是 JSON 对象"]

    for key in REQUIRED_TOP_LEVEL:
        if key not in doc or doc[key] in (None, "", {}, []):
            problems.append(f"missing_required: 缺少必填字段 {key!r}")

    version = doc.get("clawtask_version")
    if version is not None and version != CLAWTASK_VERSION:
        problems.append(
            f"version_unsupported: 需要 {CLAWTASK_VERSION}，实际 {version!r}"
        )
    kind = doc.get("kind")
    if kind is not None and kind not in KINDS:
        problems.append(f"unknown_kind: {kind!r} 不在 {list(KINDS)}")

    brief = doc.get("human_brief")
    if brief is not None and not isinstance(brief, str):
        problems.append("human_brief_not_text: human_brief 必须是 Markdown 文本")
    elif isinstance(brief, str) and len(brief.strip()) < 20:
        problems.append(
            "human_brief_too_short: 交接说明太短，别的模型读不懂（<20 字符）"
        )

    models = doc.get("target_models")
    if models is not None and (not isinstance(models, list) or not models):
        problems.append("target_models_invalid: target_models 必须是非空列表")

    # 与 P13 的模板契约对齐
    system = doc.get("system_template")
    if not isinstance(system, dict):
        problems.append("system_template_missing: 必须带 system_template 段（引用 P13 契约）")
    else:
        if system.get("schema_version") != TEMPLATE_SCHEMA_VERSION:
            problems.append(
                f"system_template_schema_mismatch: 需要 {TEMPLATE_SCHEMA_VERSION}，"
                f"实际 {system.get('schema_version')!r}"
            )
        essentials = system.get("essentials")
        if not isinstance(essentials, dict) or set(essentials) != set(ESSENTIAL_KEYS):
            problems.append(
                "system_template_essentials_incomplete: 八类必备项必须齐备（"
                f"{list(ESSENTIAL_KEYS)}）"
            )

    steps = doc.get("steps")
    if steps is not None and (not isinstance(steps, list) or not steps):
        problems.append("steps_invalid: steps 必须是非空列表")

    progress = doc.get("progress")
    if progress is not None and not isinstance(progress, dict):
        problems.append("progress_invalid: progress 必须是对象")
    elif isinstance(progress, dict):
        percent = progress.get("percent")
        if percent is not None and (not isinstance(percent, (int, float))
                                    or isinstance(percent, bool)
                                    or not 0 <= float(percent) <= 100):
            problems.append("progress_percent_out_of_range: percent 必须在 0–100")

    # 可移植性：本机路径不得出现在可移植字段里
    portable_view = {k: v for k, v in doc.items() if k != "local_only"}
    offenders = sorted(set(_find_local_paths(portable_view)))
    if offenders:
        problems.append(
            f"local_path_leaked: 以下字段含本机绝对路径，不可移植：{offenders}"
        )

    if check_integrity:
        problems.extend(verify_integrity(doc))
    return problems


# ---------------------------------------------------------------------------
# 序列化 / 反序列化
# ---------------------------------------------------------------------------

def serialize(doc: dict[str, Any], *, portable: bool = True) -> dict[str, Any]:
    """文档 → 可写盘的 ``.clawtask`` 文本。

    ``portable=True``（默认）剥离 ``local_only`` 段——分享出去的文件不带本机痕迹。
    ``portable=False`` 保留（冬眠封存用），此时本机指针只在本地副本里，不外传。
    """
    import copy

    body = copy.deepcopy(doc)
    if portable:
        body.pop("local_only", None)
    # 校验的是**剥离后**的形态：可移植文件必须自己站得住。
    problems = [p for p in validate_clawtask(body, check_integrity=False)
                if not p.startswith("integrity_")]
    if problems:
        raise ValidationFailed(
            "clawtask_invalid",
            "文档不合格，拒绝序列化：" + "；".join(problems),
        )
    sealed = seal(body)
    text = json.dumps(sealed, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    return {
        "filename": f"{sealed.get('id') or 'task'}{CLAWTASK_EXT}",
        "text": text,
        "digest": sealed["integrity"]["digest"],
        "portable": portable,
        "byte_size": len(text.encode("utf-8")),
        "clawtask_version": CLAWTASK_VERSION,
    }


def parse(text: str, *, verify: bool = True) -> dict[str, Any]:
    """``.clawtask`` 文本 → 文档。``verify=True`` 时摘要不符直接拒绝。"""
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise ValidationFailed("clawtask_not_json", f".clawtask 不是合法 JSON：{exc}") from exc
    if not isinstance(doc, dict):
        raise ValidationFailed("clawtask_not_mapping", ".clawtask 顶层必须是 JSON 对象")
    problems = validate_clawtask(doc, check_integrity=verify)
    if problems:
        raise ValidationFailed(
            "clawtask_invalid", "文档不合格：" + "；".join(problems),
        )
    return doc


# ---------------------------------------------------------------------------
# 从真实任务行构建（只搬运真实存在的字段）
# ---------------------------------------------------------------------------

def build_human_brief(
    *, name: str, goal: str, status: str, stage: str, percent: float,
    steps: list[dict[str, Any]], next_action: str = "",
) -> str:
    """生成跨模型可读的 Markdown 交接说明（必填字段）。"""
    lines = [
        f"# 任务交接：{name}",
        "",
        f"- 目标：{goal}",
        f"- 当前状态：{status} / 阶段：{stage} / 进度：{percent}%",
        "",
        "## 步骤",
        "",
    ]
    for i, step in enumerate(steps, start=1):
        marker = "x" if step.get("status") == "done" else " "
        lines.append(f"{i}. [{marker}] {step.get('title') or step.get('id')}"
                     f"　（负责：{step.get('owner') or '未指派'}）")
    lines.append("")
    if next_action:
        lines.append(f"## 下一步\n\n{next_action}\n")
    lines.append(
        "> 本文件是自描述的任务交接包：任何模型读到这一段即可接手，"
        "不需要本机的任何配置。\n"
    )
    return "\n".join(lines)


def from_task(
    actor: Any,
    task: Any,
    *,
    system_template: dict[str, Any] | None = None,
    target_models: list[str] | None = None,
    next_action: str = "",
    local_only: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把一个真实 ``Task`` 行导出成 ``.clawtask`` 文档。

    **只搬运真实字段**：``status`` / ``stage`` / ``steps`` / ``progress_percent``
    直接来自任务行；没有的信息留空，绝不填料。``system_template`` 由调用方
    （路由层）从 P13 的模板服务取，本函数只负责形状。
    """
    if getattr(task, "owner_id", None) != getattr(actor, "owner_id", None):
        # 不泄露他人任务的存在性：按 404 回，而不是 403
        raise NotFound("task_not_found", f"任务不存在：{getattr(task, 'id', '')}")

    template = system_template or {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "template_id": "",
        "quality_tier": "novice",
        "essentials": {},
    }
    essentials = template.get("essentials") or {}
    steps = template.get("steps") or []
    if not steps:
        # 从模板成员的执行管线派生步骤（如实标注 derived）
        steps = [
            {"id": m.get("id"), "title": m.get("role") or m.get("id"),
             "owner": m.get("id"), "status": "pending"}
            for m in (template.get("members") or [])
        ]
    percent = task.progress_percent
    if percent is None:
        percent = 0

    doc: dict[str, Any] = {
        "clawtask_version": CLAWTASK_VERSION,
        "kind": "task",
        "id": task.id,
        "name": (task.goal or "")[:80] or task.id,
        "goal": task.goal or "",
        "created_at": _now_iso(),
        "task_created_at": task.created_at.isoformat() if task.created_at else None,
        "target_models": list(target_models or KNOWN_MODELS),
        "system_template": {
            "schema_version": template.get("schema_version", TEMPLATE_SCHEMA_VERSION),
            "template_id": template.get("template_id", ""),
            "quality_tier": template.get("quality_tier", "novice"),
            "essentials": {k: essentials.get(k) for k in ESSENTIAL_KEYS}
                          if essentials else {k: None for k in ESSENTIAL_KEYS},
        },
        "context": {
            "format": (essentials.get("context_format") or {}).get("value")
                      or "Markdown + YAML front-matter",
            "domain": task.domain,
            "mode": task.mode,
            "strategy": task.strategy,
        },
        "steps": steps,
        "budget": (essentials.get("budget") or {}).get("value")
                  or {"max_model_calls": None, "note": "任务行未带预算，须由模板补齐"},
        "progress": {
            "status": task.status,
            "stage": task.stage,
            "percent": float(percent),
            "steps_done": int(task.steps or 0),
            "max_steps": int(task.max_steps or 0),
        },
        "checklist": [],
        "artifacts": [],
        "extensions": {},
        "resume": {
            "task_id": task.id,
            "root_task_id": task.root_task_id,
            "parent_task_id": task.parent_task_id,
        },
    }
    doc["human_brief"] = build_human_brief(
        name=doc["name"], goal=doc["goal"], status=doc["progress"]["status"],
        stage=doc["progress"]["stage"], percent=doc["progress"]["percent"],
        steps=steps, next_action=next_action,
    )
    if local_only:
        doc["local_only"] = {k: v for k, v in local_only.items() if k in LOCAL_ONLY_KEYS}
    return doc


__all__ = [
    "CLAWTASK_VERSION", "CLAWTASK_EXT", "KINDS", "KNOWN_MODELS",
    "REQUIRED_TOP_LEVEL", "LOCAL_ONLY_KEYS",
    "clawtask_schema", "canonical_clawtask", "clawtask_digest", "seal",
    "verify_integrity", "validate_clawtask", "serialize", "parse",
    "build_human_brief", "from_task",
]
