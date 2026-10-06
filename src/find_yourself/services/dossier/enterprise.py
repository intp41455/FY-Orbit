"""企业模式（A-三重模式-03）—— 万能适配 / 转换层，类 Spring AI ``ChatClient``。

**要解决什么**：企业已经有自己的技术栈与既有 agent 框架。企业模式不是让他们改用
我们的 API，而是提供一个**声明式转换层**：把外部框架的 agent 定义映射到本仓的
**同一套单循环内核**规格（三模式同源硬约束：禁止三套引擎）。

三条设计原则
------------

1. **声明式映射，不写一次性适配代码**。每个目标框架给一张对照表
   （``from`` 外部字段路径 → ``to`` 内部字段路径 + ``via`` 转换器名）。
   加新框架 = 加一行数据，不是加一个 if 分支。
2. **不静默丢字段**。外部定义里没有被映射到的键，**全部**进
   ``spec.extensions.unmapped`` 并出现在响应的 ``unmapped`` 列表里。
   企业字段被悄悄吞掉是最难查的一类集成事故。
3. **不另起多租户/权限**。企业身份映射到既有 actor 模型（``owner`` / ``service``），
   权限判定继续走既有的 :mod:`~find_yourself.services.grant` /
   :mod:`~find_yourself.services.auth` / :mod:`~find_yourself.services.team_approval`。
   本模块只**声明**这层映射（``governance`` 段），不改写任何授权语义。

诚实边界：``adapt`` 产出的是**可运行的规格**，不是「已经跑起来的系统」——
真正的执行仍由运行时承接。``provided_example`` 里的示例可在企业接入文档里照做。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from ..actor import Actor
from ..audit import AuditService
from ..errors import NotFound, ValidationFailed

#: 内部规格版本（企业文档与消费方据此判定兼容性）。
INTERNAL_SPEC_VERSION = "1.0.0"

#: 转换器名 → 实现（封闭集合；映射表只能引用这里有的名字）。
_TRANSFORMS: dict[str, Callable[[Any, Any], Any]] = {}


def _transform(name: str):
    def deco(fn: Callable[[Any, Any], Any]):
        _TRANSFORMS[name] = fn
        return fn
    return deco


@_transform("identity")
def _t_identity(value: Any, _spec: dict) -> Any:
    return value


@_transform("tools")
def _t_tools(value: Any, _spec: dict) -> list[str]:
    """把各种工具形状归一成名字列表。"""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        picked: str | None = None
        for key in ("name", "id", "tool"):
            if isinstance(item.get(key), str) and item[key]:
                picked = item[key]
                break
        if picked is None:
            fn = item.get("function")
            # 🔴 OpenAI/Spring 形状是 {"type":"function","function":{"name":...}}：
            # 工具名是 function.name；把 type（"function"）当工具名会往白名单里塞噪声。
            if isinstance(fn, dict) and isinstance(fn.get("name"), str) and fn["name"]:
                picked = fn["name"]
        if picked is None and isinstance(item.get("type"), str):
            picked = item["type"] or None   # 最后兜底：MCP 里 type 本身就是工具名
        if picked:
            out.append(picked)
    # 去重且保序（工具白名单的顺序语义：先声明先可用）
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


@_transform("max_steps")
def _t_max_steps(value: Any, spec: dict) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return int((spec.get("loop") or {}).get("max_steps") or 8)
    return max(1, min(100, int(value)))


@_transform("model_split")
def _t_model_split(value: Any, _spec: dict) -> dict[str, str]:
    """支持 ``"openai:gpt-4o"`` 形式，拆成 provider + model。"""
    if isinstance(value, dict):
        return {"provider_id": str(value.get("provider") or value.get("provider_id") or ""),
                "model_name": str(value.get("model") or value.get("model_name") or "")}
    if isinstance(value, str) and ":" in value:
        provider, _, model = value.partition(":")
        return {"provider_id": provider, "model_name": model}
    return {"provider_id": "", "model_name": str(value or "")}


@_transform("memory")
def _t_memory(value: Any, _spec: dict) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"tier": str(value.get("type") or value.get("tier") or "short_term"),
                "window": int(value.get("window") or value.get("max_messages") or 20)}
    return {"tier": "short_term", "window": 20}


#: 企业接入文档要用的示例外部定义（generic 目标，可直接照做）。
EXAMPLE_EXTERNAL: dict[str, Any] = {
    "name": "contract-reviewer",
    "system_prompt": "你是合同审阅助手，逐条核对风险条款并给出修改建议。",
    "model": "openai:gpt-4o",
    "tools": [{"name": "knowledge.search"}, {"name": "artifact.write"}],
    "max_steps": 12,
    "memory": {"type": "short_term", "window": 30},
}

def _adapter(*, target: str, label: str, mappings: list[dict[str, str]],
             notes: list[str]) -> dict[str, Any]:
    return {"target": target, "label": label, "mappings": mappings, "notes": notes}


#: 出厂适配器目录。加新框架 = 加一条数据（不是加一个 if）。
ENTERPRISE_ADAPTERS: tuple[dict[str, Any], ...] = (
    _adapter(
        target="generic",
        label="通用（直连字段名）",
        notes=["字段名与内部规范同名时用这个，零转换成本。"],
        mappings=[
            {"from": "name", "to": "agent.id", "via": "identity"},
            {"from": "name", "to": "agent.name", "via": "identity"},
            {"from": "system_prompt", "to": "agent.system_prompt", "via": "identity"},
            {"from": "tools", "to": "agent.tool_allowlist", "via": "tools"},
            {"from": "model", "to": "model", "via": "model_split"},
            {"from": "max_steps", "to": "loop.max_steps", "via": "max_steps"},
            {"from": "memory", "to": "memory", "via": "memory"},
        ],
    ),
    _adapter(
        target="spring-ai-chatclient",
        label="Spring AI ChatClient",
        notes=[
            "ChatClient 的 advisor 链没有内部等价物，原样放进 extensions.advisors，不丢。",
            "Spring 的 ChatOptions 里未列出的键会出现在 unmapped 里，供你决定要不要显式映射。",
        ],
        mappings=[
            {"from": "client.name", "to": "agent.id", "via": "identity"},
            {"from": "client.systemPrompt", "to": "agent.system_prompt", "via": "identity"},
            {"from": "client.system_prompt", "to": "agent.system_prompt", "via": "identity"},
            {"from": "client.tools", "to": "agent.tool_allowlist", "via": "tools"},
            {"from": "client.functions", "to": "agent.tool_allowlist", "via": "tools"},
            {"from": "client.options.model", "to": "model", "via": "model_split"},
            {"from": "client.maxIterations", "to": "loop.max_steps", "via": "max_steps"},
            {"from": "client.max_iterations", "to": "loop.max_steps", "via": "max_steps"},
            {"from": "client.advisors", "to": "extensions.advisors", "via": "identity"},
            {"from": "client.memory", "to": "memory", "via": "memory"},
        ],
    ),
    _adapter(
        target="langgraph",
        label="LangGraph 图定义",
        notes=[
            "图结构（nodes/edges）超出单循环内核的表达范围：契约上要求企业侧把"
            "「哪些节点是模型调用」翻译成 agent/max_steps；整图塞进 extensions.graph 保留。",
        ],
        mappings=[
            {"from": "graph.name", "to": "agent.id", "via": "identity"},
            {"from": "graph.system_prompt", "to": "agent.system_prompt", "via": "identity"},
            {"from": "state.system_prompt", "to": "agent.system_prompt", "via": "identity"},
            {"from": "tools", "to": "agent.tool_allowlist", "via": "tools"},
            {"from": "config.model", "to": "model", "via": "model_split"},
            {"from": "config.max_iterations", "to": "loop.max_steps", "via": "max_steps"},
            {"from": "graph.nodes", "to": "extensions.graph.nodes", "via": "identity"},
            {"from": "graph.edges", "to": "extensions.graph.edges", "via": "identity"},
        ],
    ),
    _adapter(
        target="openai-assistants",
        label="OpenAI Assistants",
        notes=["``instructions`` 即系统提示词；``tools[].function.name`` 会被归一成工具名。"],
        mappings=[
            {"from": "name", "to": "agent.id", "via": "identity"},
            {"from": "instructions", "to": "agent.system_prompt", "via": "identity"},
            {"from": "tools", "to": "agent.tool_allowlist", "via": "tools"},
            {"from": "model", "to": "model", "via": "model_split"},
            {"from": "metadata", "to": "extensions.metadata", "via": "identity"},
        ],
    ),
)


def adapter_catalog() -> list[dict[str, Any]]:
    return [dict(a) for a in ENTERPRISE_ADAPTERS]


def _target(target: str) -> dict[str, Any]:
    for a in ENTERPRISE_ADAPTERS:
        if a["target"] == target:
            return a
    raise NotFound("enterprise_target_unknown",
                   f"未知企业目标：{target!r}；可选 {[a['target'] for a in ENTERPRISE_ADAPTERS]}")


def _get(source: Any, path: str) -> tuple[bool, Any]:
    """按点路径取值，支持 ``a.b[0].c``。返回 ``(found, value)``。"""
    cur = source
    for part in path.split("."):
        name, _, index = part.partition("[")
        if name:
            if not isinstance(cur, dict) or name not in cur:
                return False, None
            cur = cur[name]
        if index:
            try:
                cur = cur[int(index.rstrip("]"))]
            except (TypeError, ValueError, IndexError):
                return False, None
    return True, cur


def _set(target: dict[str, Any], path: str, value: Any) -> None:
    cur = target
    parts = path.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _collect_paths(node: Any, prefix: str = "") -> list[str]:
    """外部定义里**所有叶子路径**（用来算 unmapped）。"""
    paths: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)):
                paths.extend(_collect_paths(value, child))
                if not value:
                    paths.append(child)
            else:
                paths.append(child)
    else:
        paths.append(prefix)
    return paths


class EnterpriseAdapter:
    """企业模式适配层。``audit`` 可选；提供时才挂留痕帧。"""

    def __init__(self, *, directory: str | os.PathLike[str] | None = None,
                 audit: AuditService | None = None) -> None:
        self._dir = Path(directory) if directory else Path(
            os.environ.get("FY_DOSSIER_DIR", ".runtime/dossier"))
        self.audit = audit

    # -- 目录与治理声明 -----------------------------------------------------
    def catalog(self, actor: Actor) -> dict[str, Any]:
        actor.require_authenticated()
        return {
            "spec_version": INTERNAL_SPEC_VERSION,
            "adapters": adapter_catalog(),
            "governance": self.governance(),
            "custom_mappings": sorted(self._custom().keys()),
        }

    @staticmethod
    def governance() -> dict[str, Any]:
        """权限 / 身份的**复用声明**（不另起多租户）。"""
        return {
            "actor_model": "enterprise_identity -> owner | service（映射到既有 Actor）",
            "permission_source": "find_yourself.services.grant（既有授权数据面）",
            "identity_source": "find_yourself.services.auth（会话/服务身份）",
            "approval_source": "find_yourself.services.team_approval + proposals（既有审批）",
            "note": "企业模式不实现第二套权限/多租户；只声明映射关系。",
            "single_loop_kernel": True,
        }

    # -- 自定义映射（企业可增补对照表）-------------------------------------
    def _file(self) -> Path:
        return self._dir / "enterprise-mappings.json"

    def _custom(self) -> dict[str, Any]:
        path = self._file()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def register_mapping(self, actor: Actor, target: str, mappings: list[dict[str, str]]) -> dict[str, Any]:
        """增补映射（企业自有字段）。``from``/``to`` 必填，``via`` 必须在封闭集合里。"""
        actor.require_authenticated()
        _target(target)
        for m in mappings:
            if not isinstance(m, dict) or not m.get("from") or not m.get("to"):
                raise ValidationFailed("mapping_invalid", "每条映射必须含 from 与 to")
            via = m.get("via") or "identity"
            if via not in _TRANSFORMS:
                raise ValidationFailed(
                    "mapping_unknown_transform",
                    f"未知转换器 {via!r}；可选 {sorted(_TRANSFORMS)}",
                )
        data = self._custom()
        existing = data.setdefault(target, [])
        existing.extend({"from": m["from"], "to": m["to"], "via": m.get("via") or "identity"}
                        for m in mappings)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file().write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        if self.audit is not None:
            self.audit.append(actor, "enterprise.mapping_registered", target,
                              {"count": len(mappings)})
        return {"target": target, "registered": len(mappings),
                "total_mappings": len(existing)}

    # -- 转换 ---------------------------------------------------------------
    def adapt(self, actor: Actor, target: str, external: dict[str, Any], *,
              overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """把外部框架的 agent 定义转换成本仓的单循环内核规格。

        未被映射到的外部字段**全部**保留在 ``spec.extensions.unmapped`` 并出现在
        ``unmapped`` 列表里——不静默丢弃。
        """
        actor.require_authenticated()
        if not isinstance(external, dict):
            raise ValidationFailed("external_not_mapping", "外部定义必须是 JSON 对象")
        spec_adapter = _target(target)
        mappings = list(spec_adapter["mappings"]) + list(self._custom().get(target, []))

        spec: dict[str, Any] = {
            "spec_version": INTERNAL_SPEC_VERSION,
            "agent": {"id": "", "name": "", "system_prompt": "", "tool_allowlist": []},
            "loop": {"max_steps": 8, "strategy": "single"},
            "model": {"provider_id": "", "model_name": ""},
            "memory": {"tier": "short_term", "window": 20},
            "governance": {
                "actor_model": "owner",
                "permission_source": "grant",
                "domain": str((overrides or {}).get("domain") or "work"),
            },
            "source_target": target,
            "extensions": {},
        }
        consumed: set[str] = set()
        mapped: list[dict[str, str]] = []
        for m in mappings:
            found, value = _get(external, m["from"])
            if not found:
                continue
            via = m.get("via") or "identity"
            transformed = _TRANSFORMS[via](value, spec)
            _set(spec, m["to"], transformed)
            consumed.add(m["from"])
            mapped.append({"from": m["from"], "to": m["to"], "via": via})

        # 未映射字段：全部收进 extensions.unmapped，并逐个报告
        unmapped: list[str] = []
        for path in sorted(set(_collect_paths(external))):
            if path in consumed:
                continue
            # 父路径已消费（如 client.tools）时不重复报子路径
            if any(path == c or path.startswith(c + ".") or c.startswith(path + ".")
                   for c in consumed):
                continue
            found, value = _get(external, path)
            if found:
                _set(spec["extensions"], f"unmapped.{path}", value)
                unmapped.append(path)

        warnings: list[str] = []
        if not spec["agent"]["id"]:
            spec["agent"]["id"] = "enterprise-agent"
            warnings.append("外部定义没有 name/id，已回退为 'enterprise-agent'")
        if not str(spec["agent"]["system_prompt"]).strip():
            warnings.append("外部定义没有系统提示词（instructions/systemPrompt/system_prompt）："
                            "跑起来会缺少角色约束，建议补上")
        if not spec["agent"]["tool_allowlist"]:
            warnings.append("未解析到工具白名单：该 agent 只能做纯文本推理（默认本地优先，不联网）")
        if unmapped:
            warnings.append(f"{len(unmapped)} 个外部字段没有现成映射，已原样保留在 "
                            f"extensions.unmapped，未丢弃")

        if overrides:
            allowed = {"domain", "tool_allowlist", "max_steps"}
            unknown = sorted(set(overrides) - allowed)
            if unknown:
                raise ValidationFailed("override_unknown_key",
                                       f"overrides 含未知字段 {unknown}；允许 {sorted(allowed)}")
            if "tool_allowlist" in overrides:
                spec["agent"]["tool_allowlist"] = list(overrides["tool_allowlist"])
            if "max_steps" in overrides:
                spec["loop"]["max_steps"] = _t_max_steps(overrides["max_steps"], spec)
            if "domain" in overrides:
                spec["governance"]["domain"] = str(overrides["domain"])

        if self.audit is not None:
            self.audit.append(actor, "enterprise.adapted", target,
                              {"unmapped": len(unmapped), "tools": len(spec["agent"]["tool_allowlist"])})
        return {
            "target": target,
            "spec": spec,
            "mapped": mapped,
            "unmapped": unmapped,
            "warnings": warnings,
            "executed": False,
            "note": "产出的是可运行规格；真正执行由运行时（同一单循环内核）承接。",
        }

    # -- 企业接入文档（验收：有企业接入文档）-------------------------------
    def onboarding_doc(self, actor: Actor, target: str, *,
                       external: dict[str, Any] | None = None) -> dict[str, Any]:
        actor.require_authenticated()
        spec_adapter = _target(target)
        sample = external if isinstance(external, dict) else EXAMPLE_EXTERNAL
        adapted = self.adapt(actor, target, sample)
        lines = [
            f"# 企业接入文档 · {spec_adapter['label']}",
            "",
            f"内部规格版本：`{INTERNAL_SPEC_VERSION}`　目标：`{target}`",
            "",
            "## 一、这条路怎么走",
            "",
            "1. 把你的 agent 定义（JSON/YAML）原样交给转换层："
            "`POST /api/dossier/enterprise/{target}/adapt`。",
            "2. 看返回的 `unmapped` 列表：没有被映射的字段会**原样保留**在 "
            "`spec.extensions.unmapped`，不会丢。",
            "3. 按需补映射（企业自有字段）：`POST /api/dossier/enterprise/{target}/mappings`。",
            "4. 把 `spec` 交给运行时执行——与「小白模式 / 技术模式」**同一个单循环内核**。",
            "",
            "## 二、字段对照表",
            "",
            "| 外部字段 | 内部字段 | 转换器 |",
            "|---|---|---|",
        ]
        for m in spec_adapter["mappings"]:
            lines.append(f"| `{m['from']}` | `{m['to']}` | `{m.get('via') or 'identity'}` |")
        lines += [
            "",
            "## 三、权限与身份（复用，不是新建）",
            "",
            f"- 身份：{self.governance()['actor_model']}",
            f"- 权限：{self.governance()['permission_source']}",
            f"- 审批：{self.governance()['approval_source']}",
            "",
            "> 企业模式**不提供第二套多租户/权限**。你在既有 Grant 上授予什么，"
            "转换出来的 agent 就只能做什么。",
            "",
            "## 四、可照做的示例",
            "",
            "```json",
            json.dumps(sample, ensure_ascii=False, indent=2),
            "```",
            "",
            "转换结果（节选）：",
            "",
            "```json",
            json.dumps({"agent": adapted["spec"]["agent"],
                        "loop": adapted["spec"]["loop"],
                        "model": adapted["spec"]["model"]},
                       ensure_ascii=False, indent=2),
            "```",
            "",
            "## 五、注意事项",
            "",
            *[f"- {n}" for n in spec_adapter["notes"]],
        ]
        return {"target": target, "markdown": "\n".join(lines) + "\n",
                "mappings": spec_adapter["mappings"], "governance": self.governance(),
                "adapted_sample": adapted}


__all__ = [
    "EnterpriseAdapter", "adapter_catalog", "ENTERPRISE_ADAPTERS",
    "INTERNAL_SPEC_VERSION", "EXAMPLE_EXTERNAL",
]
