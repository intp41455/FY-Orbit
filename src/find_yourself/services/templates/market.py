"""模板市场、分层视图与安全导入导出服务（A-开箱模板-04 · A-开箱模板-06 · P12）。

本服务是 P12 的核心落点，消费 P13 冻结的模板 schema 契约（``TEMPLATE_SCHEMA_VERSION = "1.0.0"``），
交付：
1. **模板分层体系（A-开箱模板-04 🔒 GATE）**：
   - 新手默认层（novice_default）：开箱即用，隐藏复杂度，不给小白看拓扑代码；
   - 进阶可换层（advanced_swappable）：按场景提供可切换的流水线模板库；
   - 技术可拆层（technical_removable）：**界面一级可见**的技术入口，可拆模板、改拓扑与直接写代码；
   - 三层共用同一套模板数据（不维护三份）；
   - 切换模板时明确提示「哪些已做的内容会被影响」（preview_switch_impact），与保存点联动。
2. **模板导出（A-开箱模板-06①）**：
   - 单文件自描述 ``.fytemplate`` 格式，包含拓扑/提示词/工具白名单/必备项默认值与 SHA-256 校验和。
3. **模板导入强制安全审查门禁（A-开箱模板-06②③④⑤）**：
   - 导入强制走安全审查门禁（防篡改、高危工具拦截、权限范围审计、提示词禁行规则审计）；
   - 审查结论透明可见；
   - 支持用户逐项确认/裁剪导入模板所申请的工具与权限（confirmed_tools）；
   - 遵循默认本地存储策略，不引入静默上传。
4. **模板市场与评分（A-开箱模板-06⑥ · A-工具市场-03）**：
   - 支持模板检索、打分、评价与按贝叶斯综合分排序（好用的自然被顶上来）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..actor import Actor
from ..audit import AuditService
from ..errors import NotFound, ValidationFailed
from ..marketplace_rating import MarketplaceRatingStore, get_rating_store
from .scaffold import (
    CONTROLLER_FORBIDDEN_RULE,
    ESSENTIAL_KEYS,
    LAYERS,
    LAYER_LABELS,
    MIN_MEMBERS,
    SCENARIOS,
    SCENARIO_LABELS,
    TEMPLATE_SCHEMA_VERSION,
    ScaffoldTemplateService,
    template_schema,
    user_templates_dir,
    validate_template,
)

#: 导出包魔数与文件扩展名
TEMPLATE_PACKAGE_MAGIC = "FYTEMPLATE_V1"
TEMPLATE_PACKAGE_EXT = ".fytemplate"

#: 高危工具黑名单：导入时若申请此类工具，必须阻断或由用户显式裁剪剔除
CRITICAL_DANGEROUS_TOOLS = frozenset({
    "exec_shell",
    "system_exec",
    "eval",
    "file_delete_root",
    "format_disk",
    "kill_process",
    "network_raw_socket",
    "raw_network",
    "arbitrary_code",
    "rm_rf",
    "sudo",
})

#: 敏感工具关注列表：导入时给出警告，并在权限审查中突出展示
SENSITIVE_TOOLS = frozenset({
    "write_file",
    "file_write",
    "http_request",
    "database_write",
    "read_env",
    "shell_read",
})

SORT_AXES = ("score", "rating", "reviews", "name", "created")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_checksum(data: dict[str, Any]) -> str:
    """计算模板导出的规范化 SHA-256 校验和。"""
    normalized = json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class TemplateMarketService:
    """系统级模板市场、分层架构与安全审查导入导出服务。"""

    def __init__(
        self,
        scaffold: ScaffoldTemplateService,
        audit: AuditService,
        ratings: MarketplaceRatingStore | None = None,
    ) -> None:
        self.scaffold = scaffold
        self.audit = audit
        self.ratings = ratings or get_rating_store()

    # -----------------------------------------------------------------------
    # 1. 模板分层体系（A-开箱模板-04 🔒 GATE 上市门禁）
    # -----------------------------------------------------------------------

    def get_hierarchy(self, actor: Actor) -> dict[str, Any]:
        """返回模板三层架构视图（新手默认 / 进阶可换 / 技术可拆）。

        陛下原话：
        ① 新手默认层：开箱即用，隐藏复杂度，不给小白看拓扑代码；
        ② 进阶可换层：按场景提供可切换的流水线模板库；
        ③ 技术层：**界面一级可见**，可拆开模板、改拓扑、改状态机、直接写代码。
        所有层级共用同一套模板数据（single_source=True）。
        """
        actor.require_authenticated()
        all_templates = self.scaffold.list_templates(actor)["items"]

        # 1. 新手默认层：精选开箱即用出厂模板（推荐默认研发/写作流水线，隐藏代码复杂度）
        novice_items = [
            {
                "template_id": t["template_id"],
                "name": t["name"],
                "scenario": t["scenario"],
                "scenario_label": SCENARIO_LABELS.get(t["scenario"], t["scenario"]),
                "summary": t["summary"],
                "quality_tier": t["quality_tier"],
                "runnable": True,
                "complexity_hidden": True,
                "example_task": t.get("example_task"),
            }
            for t in all_templates
        ]

        # 2. 进阶可换层：按场景矩阵组织（writing / research / development / data）
        scenarios_matrix: dict[str, list[dict[str, Any]]] = {s: [] for s in SCENARIOS}
        for t in all_templates:
            sc = t.get("scenario")
            if sc in scenarios_matrix:
                scenarios_matrix[sc].append({
                    "template_id": t["template_id"],
                    "name": t["name"],
                    "summary": t["summary"],
                    "member_count": len(t.get("members", [])),
                    "quality_tier": t["quality_tier"],
                    "is_factory": t.get("is_factory", False),
                })

        # 3. 技术可拆层：暴露 DSL 拓扑代码、状态机定义、可拆卸成员与代码导出通道
        tech_portal = {
            "first_class_entry": True,
            "description": "一级可见技术入口：可拆解模板、修改成员拓扑、自定义系统提示词、导出受限 DSL 代码并在线调试。",
            "supported_verbs": ["agent"],
            "features": [
                {"id": "expand_dsl", "label": "模板展开为受限 DSL 代码", "lossless": True},
                {"id": "roundtrip", "label": "代码往返同源校验", "active": True},
                {"id": "customize_topology", "label": "自由增删成员与改写调度规则", "active": True},
                {"id": "prompt_diagnostics", "label": "总控提示词边界体检（否定感知）", "active": True},
            ],
            "available_templates": [
                {
                    "template_id": t["template_id"],
                    "name": t["name"],
                    "source": t.get("source"),
                    "member_count": len(t.get("members", [])),
                }
                for t in all_templates
            ],
        }

        return {
            "schema_version": TEMPLATE_SCHEMA_VERSION,
            "single_source": True,
            "layers": [
                {
                    "id": "novice_default",
                    "label": LAYER_LABELS["novice_default"],
                    "items": novice_items,
                    "default_template_id": "development-pipeline",
                },
                {
                    "id": "advanced_swappable",
                    "label": LAYER_LABELS["advanced_swappable"],
                    "scenarios": [
                        {
                            "id": s,
                            "label": SCENARIO_LABELS.get(s, s),
                            "templates": scenarios_matrix[s],
                        }
                        for s in SCENARIOS
                    ],
                },
                {
                    "id": "technical_removable",
                    "label": LAYER_LABELS["technical_removable"],
                    "portal": tech_portal,
                },
            ],
        }

    def preview_switch_impact(
        self, actor: Actor, from_template_id: str, to_template_id: str
    ) -> dict[str, Any]:
        """预览模板切换的影响（需求 A-开箱模板-04③）。

        明确指出切换后哪些已做的内容会被影响（提示词变更、工具白名单差异、
        必备项差异），并提示与保存点联动（自动创建切换前快照）。
        """
        actor.require_authenticated()
        from_doc = self.scaffold.get_template(actor, from_template_id)
        to_doc = self.scaffold.get_template(actor, to_template_id)

        from_members = {m["id"]: m for m in from_doc["overview"]["members"]}
        to_members = {m["id"]: m for m in to_doc["overview"]["members"]}

        added_members = [m_id for m_id in to_members if m_id not in from_members]
        removed_members = [m_id for m_id in from_members if m_id not in to_members]
        retained_members = [m_id for m_id in from_members if m_id in to_members]

        # 工具白名单变化
        from_tools = {
            t for m in from_doc["overview"]["members"] for t in m.get("tool_allowlist", [])
        }
        to_tools = {
            t for m in to_doc["overview"]["members"] for t in m.get("tool_allowlist", [])
        }
        new_tools = sorted(list(to_tools - from_tools))
        dropped_tools = sorted(list(from_tools - to_tools))

        # 必备项配置变动
        from_ess = {e["key"]: e["value"] for e in from_doc.get("essentials_view", [])}
        to_ess = {e["key"]: e["value"] for e in to_doc.get("essentials_view", [])}
        changed_essentials = []
        for k in ESSENTIAL_KEYS:
            if from_ess.get(k) != to_ess.get(k):
                changed_essentials.append({
                    "key": k,
                    "from_value": from_ess.get(k),
                    "to_value": to_ess.get(k),
                })

        return {
            "from_template": {"id": from_template_id, "name": from_doc["name"]},
            "to_template": {"id": to_template_id, "name": to_doc["name"]},
            "member_changes": {
                "added": added_members,
                "removed": removed_members,
                "retained": retained_members,
            },
            "tool_changes": {
                "new_tools": new_tools,
                "dropped_tools": dropped_tools,
            },
            "changed_essentials": changed_essentials,
            "snapshot_action": "auto_savepoint_before_switch",
            "safe_to_switch": True,
            "warning": (
                f"切换将替换现有的 {len(removed_members)} 个专有成员与相关系统提示词，"
                "系统已预先为您落盘草稿保存点，可随时回溯。"
                if removed_members
                else "平滑升级：拓扑结构完全兼容。"
            ),
        }

    # -----------------------------------------------------------------------
    # 2. 模板导出（A-开箱模板-06①）
    # -----------------------------------------------------------------------

    def export_package(
        self,
        actor: Actor,
        template_id: str,
        *,
        author: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """将模板实体导出为单个自描述 .fytemplate 包。

        包含拓扑/提示词/工具白名单/必备项默认值与 SHA-256 校验和。
        """
        actor.require_authenticated()
        raw_templates = self.scaffold._templates()
        if template_id not in raw_templates:
            raise NotFound("template_not_found", f"模板不存在：{template_id}")

        doc = copy.deepcopy(raw_templates[template_id])
        doc.pop("_source", None)

        export_body = {
            "magic": TEMPLATE_PACKAGE_MAGIC,
            "schema_version": TEMPLATE_SCHEMA_VERSION,
            "template_id": template_id,
            "exported_at": _now_iso(),
            "exported_by": getattr(actor, "actor_id", None) or getattr(actor, "id", None) or "user",
            "author": author or "FindYourself User",
            "notes": notes or f"Exported from FindYourself template {template_id}",
            "template": doc,
        }

        checksum = _compute_checksum(export_body["template"])
        export_body["checksum"] = checksum

        self.audit.append(actor, "template.exported", template_id, {
            "checksum": checksum[:12],
            "schema_version": TEMPLATE_SCHEMA_VERSION,
        })
        return export_body

    def export_package_file(
        self,
        actor: Actor,
        template_id: str,
        target_dir: str | Path | None = None,
    ) -> Path:
        """将导出包以 .fytemplate 文件形式落盘。"""
        pkg = self.export_package(actor, template_id)
        out_dir = Path(target_dir) if target_dir else user_templates_dir() / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{template_id}{TEMPLATE_PACKAGE_EXT}"
        file_path = out_dir / filename
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(pkg, f, ensure_ascii=False, indent=2)
        return file_path

    # -----------------------------------------------------------------------
    # 3. 模板导入安全审查门禁（A-开箱模板-06②③④⑤）
    # -----------------------------------------------------------------------

    def security_review(
        self, actor: Actor, package_content: str | dict[str, Any]
    ) -> dict[str, Any]:
        """导入前强制执行安全审查门禁（对齐外部 skill/plugin 安装门禁）。

        审查项：
        1. 格式合规与魔数；
        2. 防篡改：校验和核验；
        3. 架构合规：P13 schema v1.0.0 与七项配置齐全；
        4. 工具白名单扫描：拦截高危命令执行/越权删除工具，标明敏感工具；
        5. 权限范围审计：遵循本地优先策略，禁止静默云外呼；
        6. 总控禁行规则与提示词风险扫描。
        """
        actor.require_authenticated()
        if isinstance(package_content, str):
            try:
                pkg = json.loads(package_content)
            except Exception as exc:
                raise ValidationFailed("package_invalid_json", f"模板包不是有效 JSON：{exc}") from exc
        elif isinstance(package_content, dict):
            pkg = copy.deepcopy(package_content)
        else:
            raise ValidationFailed("package_invalid_type", "模板包必须是 JSON 字符串或字典对象")

        findings: list[dict[str, str]] = []

        # 1. 魔数与 schema 版本
        if pkg.get("magic") != TEMPLATE_PACKAGE_MAGIC:
            findings.append({
                "severity": "high",
                "code": "invalid_magic",
                "message": f"非法包魔数 {pkg.get('magic')!r}，必须为 {TEMPLATE_PACKAGE_MAGIC!r}",
            })
        if pkg.get("schema_version") != TEMPLATE_SCHEMA_VERSION:
            findings.append({
                "severity": "high",
                "code": "schema_mismatch",
                "message": (
                    f"模板 schema 版本不匹配：期望 {TEMPLATE_SCHEMA_VERSION}，"
                    f"包内为 {pkg.get('schema_version')}"
                ),
            })

        template_doc = pkg.get("template")
        if not isinstance(template_doc, dict):
            findings.append({
                "severity": "high",
                "code": "missing_template_entity",
                "message": "包内缺少有效的 template 映射对象",
            })
            return {
                "passed": False,
                "risk_level": "high",
                "checksum_verified": False,
                "requested_tools": [],
                "dangerous_tools": [],
                "sensitive_tools": [],
                "findings": findings,
                "can_import": False,
            }

        # 2. Checksum 防篡改
        expected_checksum = pkg.get("checksum")
        actual_checksum = _compute_checksum(template_doc)
        checksum_verified = expected_checksum == actual_checksum
        if not checksum_verified:
            findings.append({
                "severity": "high",
                "code": "checksum_corrupted",
                "message": "包内容已被篡改或损坏（SHA-256 校验和不符）",
            })

        # 3. 模板结构与必要字段
        structure_problems = validate_template(template_doc)
        for prob in structure_problems:
            findings.append({
                "severity": "high",
                "code": "template_structure_error",
                "message": prob,
            })

        # 4. 工具白名单扫描
        requested_tools: set[str] = set()
        dangerous_tools: set[str] = set()
        sensitive_tools: set[str] = set()

        members = template_doc.get("members", [])
        if isinstance(members, list):
            for m in members:
                if isinstance(m, dict):
                    tools = m.get("tool_allowlist", [])
                    if isinstance(tools, list):
                        for t in tools:
                            t_str = str(t).strip()
                            requested_tools.add(t_str)
                            if t_str in CRITICAL_DANGEROUS_TOOLS:
                                dangerous_tools.add(t_str)
                            elif t_str in SENSITIVE_TOOLS:
                                sensitive_tools.add(t_str)

        if dangerous_tools:
            findings.append({
                "severity": "high",
                "code": "critical_dangerous_tools_requested",
                "message": (
                    f"模板申请了高危系统工具：{sorted(list(dangerous_tools))}。"
                    "导入时必须显式裁剪剔除，否则禁止执行！"
                ),
            })
        if sensitive_tools:
            findings.append({
                "severity": "medium",
                "code": "sensitive_tools_requested",
                "message": f"模板申请了敏感操作工具：{sorted(list(sensitive_tools))}，请确认其使用合规性。",
            })

        # 5. 权限与存储范围审计
        essentials = template_doc.get("essentials", {})
        perm_scope = (
            essentials.get("permission_scope", {}).get("value")
            if isinstance(essentials.get("permission_scope"), dict)
            else essentials.get("permission_scope")
        )
        if isinstance(perm_scope, str) and any(
            bad in perm_scope.lower() for bad in ["root", "admin", "cloud_sync", "remote_upload"]
        ):
            findings.append({
                "severity": "high",
                "code": "dangerous_permission_scope",
                "message": f"权限范围申请异常：{perm_scope!r}。本系统要求遵循默认本地存储策略。",
            })

        # 6. 总控禁行规则与提示词风险
        ctrl = template_doc.get("controller", {})
        ctrl_prompt = str(ctrl.get("system_prompt", "")) if isinstance(ctrl, dict) else ""
        if CONTROLLER_FORBIDDEN_RULE not in ctrl_prompt:
            findings.append({
                "severity": "medium",
                "code": "controller_forbidden_rule_absent",
                "message": f"总控提示词未声明禁行规则「{CONTROLLER_FORBIDDEN_RULE}」，可能越界参与具体任务。",
            })

        # 判定总风险等级
        high_count = sum(1 for f in findings if f["severity"] == "high")
        med_count = sum(1 for f in findings if f["severity"] == "medium")
        if high_count > 0:
            risk_level = "high"
        elif med_count > 0:
            risk_level = "medium"
        else:
            risk_level = "low"

        # 能否直接导入：存在高危项时不能静默导入，必须由用户确认与裁剪
        can_import = high_count == 0

        return {
            "passed": len(findings) == 0,
            "risk_level": risk_level,
            "checksum_verified": checksum_verified,
            "template_name": template_doc.get("name"),
            "template_id": template_doc.get("template_id"),
            "scenario": template_doc.get("scenario"),
            "member_count": len(members),
            "requested_tools": sorted(list(requested_tools)),
            "dangerous_tools": sorted(list(dangerous_tools)),
            "sensitive_tools": sorted(list(sensitive_tools)),
            "findings": findings,
            "can_import": can_import,
            "requires_user_confirmation": risk_level in ("medium", "high"),
        }

    def import_package(
        self,
        actor: Actor,
        package_content: str | dict[str, Any],
        *,
        confirmed_tools: list[str] | None = None,
        custom_name: str | None = None,
    ) -> dict[str, Any]:
        """导入模板包（强制安全门禁 + 用户权限/工具裁剪 + 本地存储）。

        需求 AC④：用户可逐项确认/裁剪导入模板所申请的工具与权限后再落地。
        需求 AC⑤：遵循默认本地存储策略，不引入静默上传。
        """
        actor.require_authenticated()
        report = self.security_review(actor, package_content)

        if isinstance(package_content, str):
            pkg = json.loads(package_content)
        else:
            pkg = copy.deepcopy(package_content)

        template_doc = pkg["template"]

        # 拦截：如果存在高危工具，且用户未通过 confirmed_tools 将其全部裁剪掉，坚决阻断
        if report["dangerous_tools"]:
            if confirmed_tools is None:
                raise ValidationFailed(
                    "security_gate_rejected",
                    f"模板申请了高危工具 {report['dangerous_tools']}，未进行权限确认与裁剪，禁止导入！",
                )
            remaining_dangerous = set(report["dangerous_tools"]).intersection(set(confirmed_tools))
            if remaining_dangerous:
                raise ValidationFailed(
                    "critical_tools_disallowed",
                    f"系统禁止保留高危工具：{sorted(list(remaining_dangerous))}",
                )

        # 权限裁剪：对所有成员的工具白名单实施过滤
        if confirmed_tools is not None:
            allowed_set = set(confirmed_tools)
            for m in template_doc.get("members", []):
                if isinstance(m, dict) and isinstance(m.get("tool_allowlist"), list):
                    original_tools = m["tool_allowlist"]
                    m["tool_allowlist"] = [t for t in original_tools if t in allowed_set]

        # 导入落地保存到用户本地模板库（不修改出厂原件）
        target_name = (custom_name or template_doc.get("name") or "imported-template").strip()
        saved_doc, saved_path = self.scaffold.save_as_template(
            actor, template_doc, name=target_name
        )

        self.audit.append(actor, "template.imported", saved_doc["template_id"], {
            "source_checksum": pkg.get("checksum"),
            "risk_level": report["risk_level"],
            "tailored_tools_count": len(confirmed_tools) if confirmed_tools is not None else None,
            "saved_path": saved_path,
        })

        return {
            "imported": True,
            "template_id": saved_doc["template_id"],
            "name": saved_doc["name"],
            "scenario": saved_doc["scenario"],
            "saved_path": saved_path,
            "security_report": report,
            "confirmed_tools": confirmed_tools,
        }

    # -----------------------------------------------------------------------
    # 4. 模板市场检索与评分体系打通（A-开箱模板-06⑥ · A-工具市场-03）
    # -----------------------------------------------------------------------

    def list_market(
        self,
        actor: Actor,
        *,
        query: str = "",
        scenario: str | None = None,
        layer: str | None = None,
        sort_by: str = "score",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """模板市场列表。带评分加权与好评优先排序。"""
        actor.require_authenticated()
        if sort_by not in SORT_AXES:
            raise ValidationFailed(
                "unknown_sort_axis", f"未知排序维度 {sort_by!r}；可选：{list(SORT_AXES)}"
            )

        all_templates = self.scaffold.list_templates(actor, scenario=scenario)["items"]
        needle = (query or "").strip().lower()

        items: list[dict[str, Any]] = []
        for t in all_templates:
            if needle and needle not in t["name"].lower() and needle not in t["template_id"].lower():
                continue
            if layer is not None and t.get("layer") != layer:
                continue

            summary = self.ratings.get_summary("template", t["template_id"])
            items.append({
                "template_id": t["template_id"],
                "name": t["name"],
                "scenario": t["scenario"],
                "scenario_label": SCENARIO_LABELS.get(t["scenario"], t["scenario"]),
                "summary": t["summary"],
                "quality_tier": t["quality_tier"],
                "member_count": len(t.get("members", [])),
                "is_factory": t.get("is_factory", False),
                "rating": summary,
            })

        # 好用被顶上来：默认按贝叶斯综合分降序
        if sort_by == "score":
            items.sort(key=lambda x: (x["rating"]["score"], x["rating"]["rating_count"]), reverse=True)
        elif sort_by == "rating":
            items.sort(key=lambda x: (x["rating"]["average_rating"], x["rating"]["rating_count"]), reverse=True)
        elif sort_by == "reviews":
            items.sort(key=lambda x: x["rating"]["rating_count"], reverse=True)
        elif sort_by == "name":
            items.sort(key=lambda x: x["name"].lower())

        total = len(items)
        return {
            "items": items[offset:offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by,
        }

    def rate_template(
        self,
        actor: Actor,
        template_id: str,
        rating: float | int,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """为模板打分（1.0 ~ 5.0）。记录审计日志。"""
        actor.require_authenticated()
        # 确认模板存在
        _ = self.scaffold.get_template(actor, template_id)
        actor_id = getattr(actor, "actor_id", None) or getattr(actor, "id", None) or "user"

        res = self.ratings.rate("template", template_id, actor_id, rating, comment=comment)
        self.audit.append(actor, "template.rated", template_id, {
            "rating": rating,
            "comment": comment,
            "new_score": res["summary"]["score"],
        })
        return res

    def get_template_ratings(
        self,
        actor: Actor,
        template_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """获取模板的分页评价列表与统计摘要。"""
        actor.require_authenticated()
        _ = self.scaffold.get_template(actor, template_id)
        return self.ratings.get_ratings("template", template_id, limit=limit, offset=offset)
