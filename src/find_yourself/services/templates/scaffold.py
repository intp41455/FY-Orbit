"""开箱模板实体 · schema v1.0.0（冻结）+ ``ScaffoldTemplateService``（P13）。

**这是什么**：系统级多 Agent 脚手架的**模板实体**——一套模板 = 一个总控 + 若干
成员的完整可运行拓扑（提示词 / 职责边界 / 工具白名单 / 通信协议 / 分配与回传
规则 / 验收环节）。不是单角色模板（那是 A-角色模板-01 的 10 个高频角色）。

覆盖需求：A-开箱模板-02（总控出厂预设含禁行规则）/ -03（隐性必备条件自动预置）
/ -05（模板与代码双向同源）/ -07（选中即懂 + 一键试跑）/ -08（出厂默认质量档位）
/ -09（手册质量要求）。

设计取舍（诚实记录）
--------------------

1. **模板是内容包，不是新引擎**。出厂模板落
   ``prompts_packages/scaffold/*.md``（Markdown 散文 + 一个 ```yaml 围栏块），
   人可读可改（需求 -02② 要求提示词「默认可见、可编辑」），
   解析器只认第一个围栏块，其余文字是给人看的说明。
2. **零新表**。本模块不引入 ORM 模型、不申领迁移编号：出厂模板随包发布（源文件
   即真源），用户另存的模板落 ``.runtime/templates/``（env
   ``FY_USER_TEMPLATES_DIR`` 可覆盖）。与 ``services/snapshot.py`` 落
   ``.runtime/snapshots`` 同一范式。
3. **模板 ↔ 代码同源复用既有通道**（需求 -05）。模板的**执行管线**映射成受限 DSL
   图（成员 → ``agent`` 动词的 transform 节点），导出/解析走
   ``dsl_code_export.export_dsl_code`` / ``parse_dsl_code``（既有唯一真源，
   不另写第二套转换器）。模板实体本身（提示词 / 必备项）以注释块随代码一并交付，
   因此往返**信息无损**、可校验。
4. **档位与分层是同一份数据的视图**（需求 -08、-04），不维护三份模板。
5. **八类隐性必备条件全部有出厂默认值**（需求 -03①），每项带 ``explain``
   说明（-03②）；``instantiate`` 返回 ``unresolved`` 列表——若用户覆盖成空值且
   该项确实必需，界面据此**明确指出缺什么**（-03④），而不是静默失败。
6. **绝不编造**：``estimate_usage`` 的用量是**基于成员数与示例任务步数的确定性
   估算**，返回值里明写 ``basis`` 与 ``estimated=True``，不冒充实测。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..actor import Actor
from ..audit import AuditService
from ..errors import DomainError, NotFound, ValidationFailed

# ---------------------------------------------------------------------------
# 冻结契约（跨包锁四：P12 / P16 按此消费，禁止各写各的）
# ---------------------------------------------------------------------------

#: 模板 schema 版本。**变更即破坏性**，必须同步升 P12/P16 的消费方。
TEMPLATE_SCHEMA_VERSION = "1.0.0"

#: 一套模板最少成员数（需求 A-开箱模板-01 验收①：「总控 + 成员 ≥3」）。
MIN_MEMBERS = 3

#: 总控成员的固定 id（模板内唯一；运行时留痕据此区分「总控分配」与「成员执行」）。
#:
#: **值是 ``coordinator`` 而不是 ``controller``** —— 运行时早就统一叫 coordinator
#: （``db/team_models.py`` 的 ``TeamDefinition.coordinator_role`` 默认值、
#: ``services/agent_teams.py`` 的三个预置团队都用它），模板层自称 controller
#: 会让「模板下发 → 建团队 → 派单」这条链在 id 上对不上，用户看到的总控
#: 和实际调度中枢成了两个不同角色。
#:
#: 字段名 ``controller`` 保持不变：它是已入库模板 JSON 的键，改名等于破坏性
#: schema 变更。本处只对齐**值**，即命名契约的两端在此收敛。
CONTROLLER_ID = "coordinator"

#: 历史 id 别名表 —— 读侧兼容，别名 → 规范 id。
#:
#: 为什么需要：用户手写或旧版本下发的模板可能写 ``"id": "controller"``。
#: 直接按规范 id 硬校验会把它们全判成 ``controller_id_invalid``，等于用一次
#: 改名否掉存量数据。读侧一律先过 :func:`normalize_controller_id`。
CONTROLLER_ID_ALIASES: dict[str, str] = {
    "controller": CONTROLLER_ID,
    "coordinator": CONTROLLER_ID,
}


def normalize_controller_id(raw: object) -> str:
    """把总控 id 归一到规范值；无法识别时原样返回（由调用方决定是否报错）。"""
    return CONTROLLER_ID_ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip())

#: 总控禁行规则（需求 -02①）。提示词里必须出现这条，否则 ``check_controller_prompt``
#: 给出警告（**不阻断保存**——技术用户有权自行决定，需求 -02③）。
CONTROLLER_FORBIDDEN_RULE = "不得直接执行具体任务"

#: 总控职责白名单（需求 -02：只负责分配 / 路由 / 调度跟进 / 同步 / 状态更新）。
CONTROLLER_DUTIES: tuple[str, ...] = ("任务分配", "路由划分", "调度跟进", "信息同步", "状态更新")

#: 场景轴——进阶可换层按此分类（需求 A-开箱模板-04②）。
SCENARIOS: tuple[str, ...] = ("writing", "research", "development", "data")
SCENARIO_LABELS: dict[str, str] = {
    "writing": "写作流水线",
    "research": "调研流水线",
    "development": "研发流水线",
    "data": "数据分析流水线",
}

#: 三层模板供给（需求 A-开箱模板-04①）：新手默认 / 进阶可换 / 技术可拆。
LAYERS: tuple[str, ...] = ("novice_default", "advanced_swappable", "technical_removable")
LAYER_LABELS: dict[str, str] = {
    "novice_default": "新手默认层",
    "advanced_swappable": "进阶可换层",
    "technical_removable": "技术可拆层",
}

#: 出厂质量档位（需求 A-开箱模板-08 / W7）。
QUALITY_TIERS: tuple[str, ...] = ("novice", "strict")
QUALITY_TIER_LABELS: dict[str, str] = {
    "novice": "小白档 · 最稳默认",
    "strict": "严格档 · 放开高级可调项",
}

#: 每套模板必须齐全的七项配置（需求 A-开箱模板-01 验收②）。
CONFIG_ITEMS: tuple[str, ...] = (
    "topology",
    "member_prompts",
    "responsibilities",
    "tool_allowlist",
    "communication_protocol",
    "dispatch_rules",
    "acceptance",
)
CONFIG_ITEM_LABELS: dict[str, str] = {
    "topology": "拓扑（总控 + 成员）",
    "member_prompts": "每个成员的系统提示词",
    "responsibilities": "职责边界",
    "tool_allowlist": "可用工具白名单",
    "communication_protocol": "成员间通信协议",
    "dispatch_rules": "任务分配与回传规则",
    "acceptance": "验收 / 质检环节",
}

#: 八类隐性必备条件（需求 A-开箱模板-03）。**顺序即界面展示顺序**。
ESSENTIAL_KEYS: tuple[str, ...] = (
    "member_roles",
    "task_granularity",
    "context_format",
    "retry_escalation",
    "termination",
    "artifact_naming",
    "budget",
    "permission_scope",
)
ESSENTIAL_LABELS: dict[str, str] = {
    "member_roles": "成员身份与职责描述",
    "task_granularity": "任务切分粒度",
    "context_format": "上下文传递格式",
    "retry_escalation": "失败重试与升级路径",
    "termination": "终止条件",
    "artifact_naming": "产出物存放位置与命名规则",
    "budget": "用量预算",
    "permission_scope": "权限范围",
}

#: 必备项必须是非空值的集合（``budget`` 允许 0 但不得为 None；``permission_scope``
#: 允许显式 ``local_only``——「空」与「已定义的最小值」是两回事）。
_NON_EMPTY_ESSENTIALS: tuple[str, ...] = ESSENTIAL_KEYS

_SPEC_BEGIN = "# ▼ TEMPLATE-SPEC-BEGIN"
_SPEC_END = "# ▲ TEMPLATE-SPEC-END"
_FENCE_RE = re.compile(r"^```(?:yaml|yml|json)\s*$(.*?)^```\s*$", re.MULTILINE | re.DOTALL)


def template_schema() -> dict[str, Any]:
    """返回**冻结的模板 schema 描述**（跨包契约，P12 / P16 消费）。

    这是纯数据、无副作用：消费方据此校验，不必 import 私有常量。
    """
    return {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "entity": "system_scaffold_template",
        "min_members": MIN_MEMBERS,
        "controller_id": CONTROLLER_ID,
        "controller_forbidden_rule": CONTROLLER_FORBIDDEN_RULE,
        "controller_duties": list(CONTROLLER_DUTIES),
        "scenarios": list(SCENARIOS),
        "layers": list(LAYERS),
        "quality_tiers": list(QUALITY_TIERS),
        "config_items": list(CONFIG_ITEMS),
        "essential_keys": list(ESSENTIAL_KEYS),
        "required_top_level": [
            "schema_version", "template_id", "name", "scenario", "layer",
            "quality_tier", "controller", "members", "communication_protocol",
            "dispatch_rules", "acceptance", "essentials", "example_task",
        ],
    }


# ---------------------------------------------------------------------------
# 目录定位
# ---------------------------------------------------------------------------

def _default_templates_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts_packages" / "scaffold"


def user_templates_dir() -> Path:
    """用户「另存为新模板」的落点（``FY_USER_TEMPLATES_DIR`` 可覆盖）。"""
    return Path(os.environ.get("FY_USER_TEMPLATES_DIR", ".runtime/templates"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 内容包解析与校验
# ---------------------------------------------------------------------------

def parse_template_markdown(text: str, *, source: str = "<memory>") -> dict[str, Any]:
    """从 ``.md`` 内容包里取出机器可读的模板实体。

    只认**第一个** ```yaml / ```json 围栏块；其余文字是给人看的散文。
    找不到围栏块或不是映射对象时抛 :class:`ValidationFailed`——绝不返回半个模板。
    """
    m = _FENCE_RE.search(text)
    if m is None:
        raise ValidationFailed(
            "template_block_missing",
            f"{source}: 缺少 ```yaml 围栏块（模板实体必须可机读）",
        )
    try:
        doc = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise ValidationFailed(
            "template_block_unparsable", f"{source}: 围栏块 YAML 解析失败：{exc}",
        ) from exc
    if not isinstance(doc, dict):
        raise ValidationFailed(
            "template_not_mapping", f"{source}: 围栏块必须是映射对象（key: value）",
        )
    doc.setdefault("_source", source)
    return doc


def missing_config_items(doc: dict[str, Any]) -> list[str]:
    """返回**缺失的七项配置**（需求 A-开箱模板-01②）。

    七项不是七个顶层键：提示词 / 职责 / 工具白名单都长在各成员身上，
    所以必须按语义核验（总控提示词有值 + 每个成员三项齐全），而不是查 key 是否存在。
    这样「七项齐全」才是可机检的断言（验收③「无需补充任何必填项即可跑通」的前提）。
    """
    missing: list[str] = []
    controller = doc.get("controller") if isinstance(doc.get("controller"), dict) else {}
    members = doc.get("members") if isinstance(doc.get("members"), list) else []
    real_members = [m for m in members if isinstance(m, dict)]

    if not doc.get("topology"):
        missing.append("topology")
    if not str(controller.get("system_prompt") or "").strip() or not real_members or any(
        not str(m.get("system_prompt") or "").strip() for m in real_members
    ):
        missing.append("member_prompts")
    if not real_members or any(not m.get("responsibilities") for m in real_members):
        missing.append("responsibilities")
    if not real_members or any(not isinstance(m.get("tool_allowlist"), list) for m in real_members):
        missing.append("tool_allowlist")
    if not doc.get("communication_protocol"):
        missing.append("communication_protocol")
    if not doc.get("dispatch_rules"):
        missing.append("dispatch_rules")
    if not doc.get("acceptance"):
        missing.append("acceptance")
    return missing


def validate_template(doc: dict[str, Any]) -> list[str]:
    """结构校验，返回**全部**问题（合法时为空列表）。

    「收集而不是遇错即抛」是刻意的：出厂内容包的作者需要一次看到所有缺项，
    界面也要能逐项指出「缺什么」（需求 -03④）。
    """
    problems: list[str] = []
    if not isinstance(doc, dict):
        return ["document_not_mapping: 模板实体必须是映射对象"]

    for key in template_schema()["required_top_level"]:
        if key not in doc or doc[key] in (None, "", {}, []):
            problems.append(f"missing_required: 缺少必填字段 {key!r}")

    if doc.get("schema_version") not in (None, TEMPLATE_SCHEMA_VERSION):
        problems.append(
            f"schema_version_mismatch: 期望 {TEMPLATE_SCHEMA_VERSION}，"
            f"实际 {doc.get('schema_version')!r}"
        )
    if doc.get("scenario") not in (None, *SCENARIOS):
        problems.append(f"unknown_scenario: {doc.get('scenario')!r} 不在 {list(SCENARIOS)}")
    if doc.get("layer") not in (None, *LAYERS):
        problems.append(f"unknown_layer: {doc.get('layer')!r} 不在 {list(LAYERS)}")
    if doc.get("quality_tier") not in (None, *QUALITY_TIERS):
        problems.append(
            f"unknown_quality_tier: {doc.get('quality_tier')!r} 不在 {list(QUALITY_TIERS)}"
        )

    controller = doc.get("controller")
    if not isinstance(controller, dict):
        problems.append("controller_missing: 每套模板必须带一个总控成员")
    else:
        # 兼容存量模板写 "controller"：别名归一后再比，而不是硬拒。
        if normalize_controller_id(controller.get("id")) != CONTROLLER_ID:
            problems.append(
                f"controller_id_invalid: 总控 id 必须是 {CONTROLLER_ID!r}"
                f"（兼容别名 {sorted(set(CONTROLLER_ID_ALIASES) - {CONTROLLER_ID})}）"
            )
        if not str(controller.get("system_prompt") or "").strip():
            problems.append("controller_prompt_missing: 总控系统提示词出厂必须有值")
        if not controller.get("forbidden_rules"):
            problems.append("controller_forbidden_rules_missing: 总控必须声明禁行规则")
        problems.extend(check_controller_prompt(str(controller.get("system_prompt") or "")))

    members = doc.get("members")
    if not isinstance(members, list):
        problems.append("members_missing: members 必须是成员列表")
    else:
        if len(members) < MIN_MEMBERS:
            problems.append(
                f"members_too_few: 至少 {MIN_MEMBERS} 个成员，实际 {len(members)}"
            )
        seen: set[str] = set()
        for i, m in enumerate(members):
            if not isinstance(m, dict):
                problems.append(f"member_not_mapping: members[{i}] 不是映射")
                continue
            mid = m.get("id")
            if not mid or not re.fullmatch(r"[A-Za-z0-9_-]+", str(mid)):
                problems.append(f"member_id_invalid: members[{i}].id={mid!r} 非法")
            elif mid in seen:
                problems.append(f"member_id_duplicate: 成员 id 重复 {mid!r}")
            else:
                seen.add(str(mid))
            if not str(m.get("system_prompt") or "").strip():
                problems.append(f"member_prompt_missing: 成员 {mid!r} 缺系统提示词")
            if not m.get("responsibilities"):
                problems.append(f"member_responsibilities_missing: 成员 {mid!r} 缺职责边界")
            if not isinstance(m.get("tool_allowlist"), list):
                problems.append(f"member_tool_allowlist_invalid: 成员 {mid!r} 缺工具白名单")

    essentials = doc.get("essentials")
    if not isinstance(essentials, dict):
        problems.append("essentials_missing: 缺八类隐性必备条件的默认值")
    else:
        for key in ESSENTIAL_KEYS:
            item = essentials.get(key)
            if not isinstance(item, dict):
                problems.append(f"essential_missing: 必备项 {key!r} 无默认值")
                continue
            if "value" not in item:
                problems.append(f"essential_value_missing: 必备项 {key!r} 缺 value")
            if not str(item.get("explain") or "").strip():
                problems.append(f"essential_explain_missing: 必备项 {key!r} 缺说明（explain）")

    if not isinstance(doc.get("example_task"), dict) or not str(
        (doc.get("example_task") or {}).get("goal") or ""
    ).strip():
        problems.append("example_task_missing: 缺一键试跑的示例任务（goal 必填）")

    problems.extend(f"config_item_missing: 缺配置项 {k}" for k in missing_config_items(doc))
    return problems


def check_controller_prompt(prompt: str) -> list[str]:
    """检查总控提示词是否守住「只调度、不执行」的边界（需求 -02③）。

    返回**警告**列表（不是错误）：技术用户有权改坏提示词，界面给明显警告即可。
    """
    warnings: list[str] = []
    text = prompt or ""
    if CONTROLLER_FORBIDDEN_RULE not in text:
        warnings.append(
            f"controller_forbidden_rule_missing: 提示词未写明禁行规则"
            f"「{CONTROLLER_FORBIDDEN_RULE}」——这是新手最易踩的坑"
        )
    # 反向线索：总控提示词里出现「自己执行」类措辞时提示越权风险。
    # ⚠️ 必须**否定感知**：禁行规则本身就是「不得直接执行具体任务」，朴素子串匹配
    # 会把「你不得直接执行具体任务」误判成越权（这正是本函数第一版翻过的车）。
    exec_markers = ("你自己写代码", "直接编写代码", "直接执行具体任务", "亲自完成实现")
    negations = ("不", "勿", "别", "禁", "无需", "不得")
    for marker in exec_markers:
        start = 0
        while True:
            idx = text.find(marker, start)
            if idx == -1:
                break
            start = idx + 1
            window = text[max(0, idx - 3):idx]
            if any(neg in window for neg in negations):
                continue  # 「不得…」是否定用法，不是越权
            warnings.append(
                f"controller_scope_risk: 提示词出现「{marker}」，与「总控不参与具体执行」冲突"
            )
            break
    if not any(duty in text for duty in CONTROLLER_DUTIES):
        warnings.append(
            f"controller_duties_missing: 提示词未声明总控职责"
            f"（应含 {list(CONTROLLER_DUTIES)} 之一）"
        )
    return warnings


# ---------------------------------------------------------------------------
# 只读派生：一眼可见的构成 + 确定性用量估算（需求 -07）
# ---------------------------------------------------------------------------

def _tier_view(template: dict[str, Any], tier: str) -> dict[str, Any]:
    """按质量档位裁剪视图（需求 -08：切换即改变默认值与可见复杂度）。"""
    if tier not in QUALITY_TIERS:
        raise ValidationFailed("unknown_quality_tier", f"未知质量档位 {tier!r}")
    doc = copy.deepcopy(template)
    doc["quality_tier"] = tier
    if tier == "novice":
        # 小白档：隐藏技术细节（拓扑代码 / 原始 canonical 结构），只留人话。
        doc.pop("_source", None)
        doc["_hidden_technical"] = True
    else:
        doc["_hidden_technical"] = False
    return doc


def system_overview(template: dict[str, Any]) -> dict[str, Any]:
    """「选中即懂」的构成摘要（需求 -07①）：成员数 / 各干啥 / 用什么工具 / 产出放哪 / 大致用量。"""
    members = template.get("members") or []
    essentials = template.get("essentials") or {}
    naming = (essentials.get("artifact_naming") or {}).get("value") or {}
    return {
        "template_id": template.get("template_id"),
        "name": template.get("name"),
        "scenario": template.get("scenario"),
        "scenario_label": SCENARIO_LABELS.get(str(template.get("scenario")), ""),
        "layer": template.get("layer"),
        "quality_tier": template.get("quality_tier"),
        "controller": {
            "id": CONTROLLER_ID,
            "role": (template.get("controller") or {}).get("role"),
            "duties": list((template.get("controller") or {}).get("duties") or []),
        },
        "member_count": len(members),
        "members": [
            {
                "id": m.get("id"),
                "role": m.get("role"),
                "responsibilities": list(m.get("responsibilities") or []),
                "tools": list(m.get("tool_allowlist") or []),
            }
            for m in members
        ],
        "artifact_rule": naming,
        "estimate": estimate_usage(template),
    }


def estimate_usage(template: dict[str, Any]) -> dict[str, Any]:
    """确定性用量估算（**估算，非实测**）。

    口径：每个成员一步 ≈ 1 次模型调用；示例任务的 ``steps`` 决定总步数。
    返回值显式带 ``estimated=True`` 与 ``basis``，前端据此标注「预估」，
    不得当作账单（真实账单口径见 runtime 模型网关计费）。
    """
    members = template.get("members") or []
    example = template.get("example_task") or {}
    steps = example.get("steps") if isinstance(example.get("steps"), int) else len(members) + 1
    per_step_tokens = 1800  # 保守中位估算，仅用于「大致用多少」的量级提示
    return {
        "estimated": True,
        "basis": "成员数 × 示例任务步数 × 每步保守 token 中位",
        "steps": steps,
        "member_count": len(members),
        "approx_model_calls": steps,
        "approx_tokens": steps * per_step_tokens,
        "cost_note": "实际费用以运行时账单为准（A-成本仪表盘-01）",
    }


# ---------------------------------------------------------------------------
# 模板 ↔ 代码（需求 -05，复用既有画布↔代码通道）
# ---------------------------------------------------------------------------

def template_to_dsl(template: dict[str, Any]) -> dict[str, Any]:
    """把模板的**执行管线**映射成受限 DSL 图（成员 → ``agent`` 动词节点）。

    只使用既有 ``VERB_REGISTRY`` 里的 ``agent`` 动词，因此导出必然落在受限动词集内
    （``export_dsl_code`` 的封闭性不变量原样成立）。
    """
    members = template.get("members") or []
    if not members:
        raise ValidationFailed("template_no_members", "模板没有成员，无法展开为代码")
    nodes: list[dict[str, Any]] = [{"id": "start", "type": "input", "params": {"kind": "text_lines"}}]
    for m in members:
        nodes.append({
            "id": str(m["id"]),
            "type": "transform",
            "verb": "agent",
            "params": {"agent": str(m["id"])},
        })
    nodes.append({"id": "result", "type": "output", "params": {"format": "json"}})
    ids = [n["id"] for n in nodes]
    edges = [{"from": a, "to": b} for a, b in zip(ids, ids[1:])]
    # DSL 文档版本号必须是 "1"（dsl_canvas.validate_dsl 的硬约束），不是 "1.0"。
    return {"version": "1", "nodes": nodes, "edges": edges}


def dsl_to_template(doc: dict[str, Any], *, base: dict[str, Any] | None = None,
                    name: str | None = None) -> dict[str, Any]:
    """把（代码解析回来的）DSL 图反向还原成模板实体。

    ``base`` 提供提示词 / 必备项等无法从管线图还原的字段；缺失的成员降级为
    ``imported=True`` 的占位成员——**如实标注**，不假装它有完整提示词。
    """
    from ..dsl_canvas import canonical_dsl

    canonical = canonical_dsl(doc)
    member_ids = [
        str(n.get("params", {}).get("agent"))
        for n in canonical["nodes"]
        if n.get("type") == "transform" and n.get("verb") == "agent"
    ]
    known = {str(m["id"]): m for m in ((base or {}).get("members") or [])}
    members: list[dict[str, Any]] = []
    for mid in member_ids:
        if mid in known:
            members.append(copy.deepcopy(known[mid]))
        else:
            members.append({
                "id": mid,
                "role": mid,
                "system_prompt": "",
                "responsibilities": [],
                "tool_allowlist": [],
                "imported": True,
            })
    template = copy.deepcopy(base) if base else {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "template_id": "",
        "scenario": "development",
        "layer": "technical_removable",
        "quality_tier": "strict",
        "controller": {},
        "communication_protocol": {},
        "dispatch_rules": {},
        "acceptance": {},
        "essentials": {},
        "example_task": {"goal": ""},
    }
    template["members"] = members
    template["topology"] = {
        "controller": CONTROLLER_ID,
        "members": [m["id"] for m in members],
    }
    if name:
        template["name"] = name
        template["template_id"] = _slug(name)
    return template


def _slug(text: str) -> str:
    r"""把名字归一成模板 id。

    ``\w`` 在 str 上覆盖 CJK，所以中文名会保留（如「我的写作系统」）——中文用户
    另存模板时不该被迫起英文名；只把空白/标点折成连字符。
    """
    s = re.sub(r"[^\w]+", "-", (text or "").strip(), flags=re.UNICODE)
    return s.strip("-").lower() or "template"


def render_template_export(template: dict[str, Any]) -> dict[str, Any]:
    """模板 → 单文件代码（模板实体注释块 + 受限 DSL 管线代码）。

    往返无损：``parse_template_export`` 能拿回**逐字节一致**的模板 JSON 与等价的
    管线图（管道图部分由既有通道保证 ``parse(export(doc)) == canonical(doc)``）。
    """
    from ..dsl_code_export import export_dsl_code

    dsl_doc = template_to_dsl(template)
    exported = export_dsl_code(dsl_doc)
    spec = copy.deepcopy(template)
    spec.pop("_source", None)
    spec_json = json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2)
    spec_lines = "\n".join(f"# {line}" for line in spec_json.splitlines())
    header = (
        "# -*- coding: utf-8 -*-\n"
        f"# Find Yourself 开箱模板导出（schema {TEMPLATE_SCHEMA_VERSION}）\n"
        "# 本文件 = 模板实体的 JSON 注释块 + 受限 DSL 执行管线。\n"
        "# 用 find_yourself.services.templates.scaffold.parse_template_export() 可无损读回。\n"
        f"{_SPEC_BEGIN}\n{spec_lines}\n{_SPEC_END}\n"
    )
    return {
        "filename": f"{spec.get('template_id') or 'template'}.py",
        "language": "python",
        "code": header + exported["code"],
        "template_id": spec.get("template_id"),
        "node_count": exported["node_count"],
        "edge_count": exported["edge_count"],
        "verbs": exported["verbs"],
        "runtime_dirname": exported["runtime_dirname"],
        "runtime_files": exported["runtime_files"],
    }


def parse_template_export(code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """读回 ``render_template_export`` 的产物 → ``(template, dsl_doc)``。

    刻意**不** ``eval`` 任何东西：模板实体是 JSON 注释块，管线图交给既有
    ``parse_dsl_code`` 做 ast 静态解析。
    """
    from ..dsl_code_export import parse_dsl_code

    lines = code.splitlines()
    spec_lines: list[str] = []
    pipeline: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == _SPEC_BEGIN:
            inside = True
            continue
        if line.strip() == _SPEC_END:
            inside = False
            continue
        if inside:
            spec_lines.append(line[2:] if line.startswith("# ") else line.lstrip("#").lstrip(" "))
        else:
            pipeline.append(line)
    if not spec_lines:
        raise ValidationFailed("template_export_spec_missing", "导出文件缺少模板实体注释块")
    try:
        template = json.loads("\n".join(spec_lines))
    except ValueError as exc:
        raise ValidationFailed(
            "template_export_spec_unparsable", f"模板实体注释块不是合法 JSON：{exc}",
        ) from exc
    if not isinstance(template, dict):
        raise ValidationFailed("template_export_spec_not_mapping", "模板实体必须是 JSON 对象")
    dsl_doc = parse_dsl_code("\n".join(pipeline))
    return template, dsl_doc


def _roundtrip_consistent(template: dict[str, Any]) -> bool:
    """往返一致性校验（需求 -05④）：模板→代码→模板，管线与成员序列必须一致。"""
    from ..dsl_canvas import canonical_dsl

    exported = render_template_export(template)
    back, dsl_doc = parse_template_export(exported["code"])
    if [m["id"] for m in back.get("members", [])] != [m["id"] for m in template.get("members", [])]:
        return False
    return canonical_dsl(dsl_doc) == canonical_dsl(template_to_dsl(template))


# ---------------------------------------------------------------------------
# 手册质量要求（需求 -09 / W8）
# ---------------------------------------------------------------------------

_FAULT_TABLE_HINT = ("现象",)
_FAULT_TABLE_COLS = ("现象", "原因", "恢复", "预防")
MIN_FAULT_SCENARIOS = 20


def check_manual_quality(markdown: str) -> dict[str, Any]:
    """校验手册是否达标（需求 -09①②③）：故障目录 ≥20 类、四列齐全、有示例。

    这是**交付物验收检查器**，不新增功能。返回 ``ok`` 与逐项 ``gaps``，
    让「手册不达标」变成可回归的结论，而不是印象。
    """
    text = markdown or ""
    lines = text.splitlines()

    # 1) 故障目录：找表头含「现象」且同时含其余三列的表格，统计其数据行。
    header_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip().startswith("|") and all(col in line for col in _FAULT_TABLE_COLS):
            header_idx = i
            break
    fault_rows: list[str] = []
    if header_idx is not None:
        for line in lines[header_idx + 2:]:  # 跳过表头与分隔行
            if not line.strip().startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if any(cells):
                fault_rows.append(line)

    example_blocks = text.count("```") // 2
    has_zero_basis_path = ("30 分钟" in text) or ("30分钟" in text)

    gaps: list[str] = []
    if len(fault_rows) < MIN_FAULT_SCENARIOS:
        gaps.append(
            f"fault_catalog_short: 故障目录仅 {len(fault_rows)} 类，需 ≥{MIN_FAULT_SCENARIOS} 类"
            f"（每类含 {'/'.join(_FAULT_TABLE_COLS)}）"
        )
    if example_blocks < 1:
        gaps.append("no_runnable_example: 未发现可照做的示例代码块")
    if not has_zero_basis_path:
        gaps.append("zero_basis_path_missing: 未声明「零基础 30 分钟跑通」路径")
    return {
        "ok": not gaps,
        "fault_scenario_count": len(fault_rows),
        "min_required": MIN_FAULT_SCENARIOS,
        "example_block_count": example_blocks,
        "zero_basis_path": has_zero_basis_path,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

class ScaffoldTemplateService:
    """开箱模板的读 / 实例化 / 档位 / 代码同源 / 手册验收面。

    只依赖 ``templates_dir``（内容包目录）；``audit`` 可选——提供时才挂留痕帧
    （需求 A-开箱模板-02⑥：提示词修改与实例化进留痕）。
    """

    def __init__(
        self,
        *,
        templates_dir: str | os.PathLike[str] | None = None,
        user_dir: str | os.PathLike[str] | None = None,
        audit: AuditService | None = None,
        session: Any | None = None,
    ) -> None:
        self._dir = Path(templates_dir) if templates_dir else _default_templates_dir()
        self._user_dir = Path(user_dir) if user_dir else user_templates_dir()
        self.audit = audit
        self.session = session
        self._cache: dict[str, dict[str, Any]] | None = None

    # -- 加载 ---------------------------------------------------------------
    def _load_dir(self, directory: Path) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        if not directory.is_dir():
            return out
        for path in sorted(directory.glob("*.md")):
            try:
                doc = parse_template_markdown(path.read_text(encoding="utf-8"),
                                              source=str(path))
            except ValidationFailed:
                continue  # 非模板文件/坏文件不污染整站列表
            tid = doc.get("template_id")
            if isinstance(tid, str) and tid:
                doc["_source"] = str(path)
                out[tid] = doc
        return out

    def _templates(self) -> dict[str, dict[str, Any]]:
        if self._cache is None:
            merged = self._load_dir(self._dir)
            merged.update(self._load_dir(self._user_dir))  # 用户模板覆盖同名出厂模板
            self._cache = merged
        return self._cache

    def invalidate(self) -> None:
        self._cache = None

    def _require(self, template_id: str) -> dict[str, Any]:
        doc = self._templates().get(template_id)
        if doc is None:
            raise NotFound("template_not_found", f"模板不存在：{template_id}")
        return doc

    # -- 读面 ---------------------------------------------------------------
    def layers(self) -> dict[str, Any]:
        """三层模板供给的同源说明（需求 -04）。同一份数据，三种视图。"""
        return {
            "schema_version": TEMPLATE_SCHEMA_VERSION,
            "layers": [
                {"id": k, "label": LAYER_LABELS[k],
                 "hides_technical": k == "novice_default"} for k in LAYERS
            ],
            "single_source": True,
            "note": "三层共用同一份模板数据（需求 -04⑥），不维护三份；技术入口在一级可见。",
        }

    def quality_tiers(self) -> dict[str, Any]:
        """出厂质量档位（需求 -08）。"""
        return {
            "tiers": [{"id": k, "label": QUALITY_TIER_LABELS[k]} for k in QUALITY_TIERS],
            "default": "novice",
            "paired_with_layers": True,
            "note": "切换档位不丢失已做工作（联动保存点），任一档位下一键试跑均可用。",
        }

    def list_templates(
        self, actor: Actor, *, scenario: str | None = None,
        tier: str = "novice", layer: str | None = None,
    ) -> dict[str, Any]:
        actor.require_authenticated()
        if scenario is not None and scenario not in SCENARIOS:
            raise ValidationFailed("unknown_scenario", f"未知场景 {scenario!r}")
        if tier not in QUALITY_TIERS:
            raise ValidationFailed("unknown_quality_tier", f"未知质量档位 {tier!r}")
        items = []
        for doc in self._templates().values():
            if scenario is not None and doc.get("scenario") != scenario:
                continue
            if layer is not None and doc.get("layer") != layer:
                continue
            view = _tier_view(doc, tier)
            items.append({
                "template_id": doc.get("template_id"),
                "name": doc.get("name"),
                "scenario": doc.get("scenario"),
                "scenario_label": SCENARIO_LABELS.get(str(doc.get("scenario")), ""),
                "layer": doc.get("layer"),
                "quality_tier": tier,
                "summary": doc.get("summary", ""),
                "member_count": len(doc.get("members") or []),
                "overview": system_overview(view),
            })
        items.sort(key=lambda it: (str(it["scenario"]), str(it["template_id"])))
        return {"items": items, "total": len(items), "scenario": scenario,
                "quality_tier": tier, "schema_version": TEMPLATE_SCHEMA_VERSION}

    def get_template(self, actor: Actor, template_id: str, *, tier: str = "novice",
                     layer: str | None = None) -> dict[str, Any]:
        actor.require_authenticated()
        doc = _tier_view(self._require(template_id), tier)
        if layer is not None:
            if layer not in LAYERS:
                raise ValidationFailed("unknown_layer", f"未知层级 {layer!r}")
            doc["layer"] = layer
        # 缺项必须能被界面指出来（需求 -03④）：get 也带上 problems。
        doc["problems"] = validate_template(doc)
        doc["controller_warnings"] = check_controller_prompt(
            str((doc.get("controller") or {}).get("system_prompt") or "")
        )
        doc["overview"] = system_overview(doc)
        doc["essentials_view"] = [
            {
                "key": k,
                "label": ESSENTIAL_LABELS[k],
                "value": (doc.get("essentials", {}).get(k) or {}).get("value"),
                "explain": (doc.get("essentials", {}).get(k) or {}).get("explain"),
                "overridable": True,
                "factory_value": (self._require(template_id).get("essentials", {}).get(k) or {}).get("value"),
            }
            for k in ESSENTIAL_KEYS
        ]
        return doc

    # -- 实例化（需求 -02 / -03）-------------------------------------------
    def instantiate(
        self, actor: Actor, template_id: str, *, tier: str = "novice",
        overrides: dict[str, Any] | None = None, name: str | None = None,
    ) -> dict[str, Any]:
        """按模板创建**可运行系统规格**：无空必填项，缺项明确列出。

        ``overrides`` 只允许覆盖八类必备项与总控/成员提示词；未知键直接 422
        （不能让调用方偷偷塞别的字段进来）。
        """
        actor.require_authenticated()
        base = _tier_view(self._require(template_id), tier)
        overrides = dict(overrides or {})
        allowed = set(ESSENTIAL_KEYS) | {"controller_prompt", "member_prompts"}
        unknown = sorted(set(overrides) - allowed)
        if unknown:
            raise ValidationFailed(
                "override_unknown_key",
                f"overrides 含未知字段 {unknown}；允许：{sorted(allowed)}",
            )

        system = copy.deepcopy(base)
        # 1) 八类必备项覆盖（逐项）
        essentials = system.setdefault("essentials", {})
        for key in ESSENTIAL_KEYS:
            if key in overrides:
                essentials[key] = {**essentials.get(key, {}), "value": overrides[key]}
        # 2) 提示词覆盖
        if "controller_prompt" in overrides:
            system["controller"]["system_prompt"] = str(overrides["controller_prompt"])
        member_prompts = overrides.get("member_prompts") or {}
        if member_prompts and not isinstance(member_prompts, dict):
            raise ValidationFailed("member_prompts_invalid", "member_prompts 必须是 {成员id: 提示词}")
        for m in system.get("members", []):
            if m.get("id") in member_prompts:
                m["system_prompt"] = str(member_prompts[m["id"]])

        system["name"] = name or f"{base.get('name')}（我的系统）"
        system["template_id"] = f"{template_id}@{tier}"

        unresolved = [
            key for key in _NON_EMPTY_ESSENTIALS
            if (essentials.get(key) or {}).get("value") in (None, "", [], {})
        ]
        warnings = check_controller_prompt(str(system["controller"].get("system_prompt") or ""))
        # 成员提示词被清空也要明确指出来（-03④）
        unresolved.extend(
            f"member_prompt:{m['id']}" for m in system.get("members", [])
            if not str(m.get("system_prompt") or "").strip()
        )

        result = {
            "template_id": template_id,
            "created_from": "factory",
            "quality_tier": tier,
            "system": system,
            "unresolved": unresolved,
            "warnings": warnings,
            "runnable": not unresolved,
            "message": (
                "可直接跑通一次完整任务" if not unresolved
                else f"以下必填项缺失，系统跑不通：{unresolved}"
            ),
        }
        self._audit(actor, "template.instantiated", template_id, {
            "quality_tier": tier, "unresolved": unresolved,
            "overridden": sorted(set(overrides) & set(ESSENTIAL_KEYS)),
        })
        return result

    # -- 提示词体检与恢复出厂（需求 -02③④）--------------------------------
    def check_controller_prompt_draft(self, actor: Actor, template_id: str,
                                      draft_prompt: str) -> dict[str, Any]:
        actor.require_authenticated()
        self._require(template_id)
        warnings = check_controller_prompt(draft_prompt)
        return {
            "template_id": template_id,
            "warnings": warnings,
            "blocking": False,
            "note": "技术用户有权改坏提示词：给出明显警告但不阻断保存（需求 -02③）。",
        }

    def restore_factory(self, actor: Actor, template_id: str, *, tier: str = "novice") -> dict[str, Any]:
        """一键恢复出厂总控提示词 / 必备项默认值（需求 -02④、-03③）。"""
        actor.require_authenticated()
        base = _tier_view(self._require(template_id), tier)
        self._audit(actor, "template.factory_restored", template_id, {"quality_tier": tier})
        return {
            "template_id": template_id,
            "quality_tier": tier,
            "controller_prompt": (base.get("controller") or {}).get("system_prompt"),
            "essentials": base.get("essentials"),
            "restored_from": "factory",
        }

    # -- 模板 ↔ 代码同源（需求 -05）----------------------------------------
    def expand_to_code(self, actor: Actor, template_id: str, *, tier: str = "novice") -> dict[str, Any]:
        """一键展开为代码并进入技术模式（复用既有通道，不另造转换实现）。"""
        actor.require_authenticated()
        doc = _tier_view(self._require(template_id), tier)
        exported = render_template_export(doc)
        self._audit(actor, "template.expanded_to_code", template_id,
                    {"quality_tier": tier, "node_count": exported["node_count"]})
        exported["roundtrip_consistent"] = _roundtrip_consistent(doc)
        exported["note"] = ("管线走既有受限 DSL 通道（dsl_ir 单一真源）；"
                           "展开后仍受基座实时保存与留痕保护。")
        return exported

    def import_code(self, actor: Actor, code: str, *, base_template_id: str | None = None,
                    name: str | None = None) -> dict[str, Any]:
        """代码编辑后另存为新模板（需求 -05②）：读回代码 → 新模板实体 → 落用户目录。"""
        actor.require_authenticated()
        base = self._require(base_template_id) if base_template_id else None
        template, _dsl = parse_template_export(code)
        if base is not None and not template.get("members"):
            template = dsl_to_template(template_to_dsl(base), base=base, name=name)
        saved = self.save_as_template(actor, template, name=name)
        return {"template": saved[0], "written_to": saved[1], "source_template": base_template_id}

    def save_as_template(self, actor: Actor, template: dict[str, Any],
                         *, name: str | None = None) -> tuple[dict[str, Any], str]:
        """另存为新模板（**不改动出厂原件**，需求 -01⑤）。"""
        actor.require_authenticated()
        doc = copy.deepcopy(template)
        if name:
            doc["name"] = name
            doc["template_id"] = _slug(name)
        if not doc.get("template_id"):
            raise ValidationFailed("template_id_missing", "另存模板必须有 template_id 或 name")
        doc.setdefault("schema_version", TEMPLATE_SCHEMA_VERSION)
        problems = validate_template(doc)
        # 另存允许不完整，但必须如实报告；落盘后仍可被 list 读到。
        self._user_dir.mkdir(parents=True, exist_ok=True)
        path = self._user_dir / f"{doc['template_id']}.md"
        body = yaml.safe_dump({k: v for k, v in doc.items() if not k.startswith("_")},
                              allow_unicode=True, sort_keys=True)
        path.write_text(
            f"# {doc.get('name')}（用户另存模板）\n\n```yaml\n{body}```\n",
            encoding="utf-8", newline="\n",
        )
        self.invalidate()
        self._audit(actor, "template.saved_as", str(doc["template_id"]),
                    {"path": str(path), "problems": problems})
        return doc, str(path)

    # -- 手册质量（需求 -09）-----------------------------------------------
    def manual_quality_report(self, actor: Actor, markdown: str | None = None,
                              *, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        actor.require_authenticated()
        if markdown is None:
            candidate = Path(path) if path else (
                Path(os.environ["FY_MANUAL_PATH"]) if os.environ.get("FY_MANUAL_PATH") else None
            )
            if candidate is None or not candidate.is_file():
                raise NotFound(
                    "manual_not_found",
                    "未提供手册内容，且未找到手册文件（可用 path 或 FY_MANUAL_PATH 指定）",
                )
            markdown = candidate.read_text(encoding="utf-8")
        report = check_manual_quality(markdown)
        report["checked_at"] = _now_iso()
        return report

    # -- 内部 ---------------------------------------------------------------
    def _audit(self, actor: Actor, action: str, target: str, details: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit.append(actor, action, target, details)


def template_digest(template: dict[str, Any]) -> str:
    """模板实体的确定性摘要（sha256，JSON canonical）——市场 / 导入导出据此校验。"""
    clean = {k: v for k, v in template.items() if not k.startswith("_")}
    basis = json.dumps(clean, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(basis).hexdigest()


__all__ = [
    "TEMPLATE_SCHEMA_VERSION", "MIN_MEMBERS", "CONTROLLER_ID",
    "CONTROLLER_FORBIDDEN_RULE", "CONTROLLER_DUTIES", "SCENARIOS", "SCENARIO_LABELS",
    "LAYERS", "LAYER_LABELS", "QUALITY_TIERS", "QUALITY_TIER_LABELS",
    "CONFIG_ITEMS", "CONFIG_ITEM_LABELS", "ESSENTIAL_KEYS", "ESSENTIAL_LABELS",
    "MIN_FAULT_SCENARIOS",
    "template_schema", "parse_template_markdown", "validate_template",
    "missing_config_items",
    "check_controller_prompt", "check_manual_quality", "system_overview",
    "estimate_usage", "template_to_dsl", "dsl_to_template", "render_template_export",
    "parse_template_export", "template_digest", "ScaffoldTemplateService",
]
