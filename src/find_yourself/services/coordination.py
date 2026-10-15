"""B5/N8 首切片：组织/协调拓扑即数据（REQ-A09）。

六维正交原语做**声明式配置**，代替逐个硬编码拓扑形态
（REQ-D04 的顺序/并行/流水线只是本模型可表达的三种实例）。
本文件 = 声明层：schema + 交叉一致性校验 + describe；
通用协调运行时（把 spec 真跑起来）在后，不在本切片。

维度与 v1 封闭词表（扩展词表 = 加新枚举值 + 对应用例，不许放宽成自由串，
否则“任意拓扑可表达”退化成“任意字符串可填写”）：

* authority（权威）：centralized / decentralized / federal / hierarchical
* communication（通信）：star / mesh / bus / blackboard / pipeline / tree
* decomposition（分解）：sequential / parallel / pipeline / map / delegation / market
* coordination（协调）：orchestrated / choreographed / consensus / auction / hierarchical_task
* control（控制）：preemptive / cooperative / supervisory / autonomous
* governance（治理）：approval_gate / audit_trail / policy / none

交叉规则（fail-closed，无效组合编译期拒绝）：
* centralized + star 必须有 coordinator，且 coordinator 必须在 members 内；
* pipeline 分解必须有 ≥2 的有序 stages；
* hierarchical 权威必须有 parent_links 且构成树（无环、单根、覆盖全部非根成员）；
* communication tree 必须配 parent_links（沿层级走，没边走不了）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

AUTHORITY_MODES = ("centralized", "decentralized", "federal", "hierarchical")
COMMUNICATION_MODES = ("star", "mesh", "bus", "blackboard", "pipeline", "tree")
DECOMPOSITION_MODES = ("sequential", "parallel", "pipeline", "map", "delegation", "market")
COORDINATION_MODES = ("orchestrated", "choreographed", "consensus", "auction", "hierarchical_task")
CONTROL_MODES = ("preemptive", "cooperative", "supervisory", "autonomous")
GOVERNANCE_MODES = ("approval_gate", "audit_trail", "policy", "none")


class CoordinationValidationError(ValueError):
    """spec 非法（未知枚举 / 缺失必填 / 交叉不一致）。"""


@dataclass(frozen=True)
class AuthoritySpec:
    mode: str
    coordinator: str | None = None


@dataclass(frozen=True)
class CommunicationSpec:
    mode: str


@dataclass(frozen=True)
class DecompositionSpec:
    mode: str
    stages: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoordinationModeSpec:
    mode: str


@dataclass(frozen=True)
class ControlSpec:
    mode: str


@dataclass(frozen=True)
class GovernanceSpec:
    mode: str


@dataclass(frozen=True)
class CoordinationSpec:
    """一份组织/协调拓扑声明（不可变快照）。"""

    name: str
    authority: AuthoritySpec
    communication: CommunicationSpec
    decomposition: DecompositionSpec
    coordination: CoordinationModeSpec
    control: ControlSpec
    governance: GovernanceSpec
    members: tuple[str, ...] = ()
    parent_links: dict[str, str] | None = None


def _enum(value: Any, allowed: tuple[str, ...], field_path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise CoordinationValidationError(
            f"{field_path} 必须是 {list(allowed)} 之一，实际是 {value!r}")
    return value


def _hierarchy_roots_and_cycle(members: list[str],
                               links: dict[str, str]) -> tuple[str, bool]:
    """返回 (根, 是否有环)。parent 指向不存在的成员同样非法（调用方先查）。"""
    roots = [m for m in members if m not in links]
    # 沿 parent 链走：回到已访即环。
    for start in members:
        seen: set[str] = set()
        cur: str | None = start
        while cur is not None:
            if cur in seen:
                return roots[0] if roots else "", True
            seen.add(cur)
            cur = links.get(cur)
    return (roots[0] if len(roots) == 1 else ""), False


def validate_spec(raw: dict[str, Any]) -> CoordinationSpec:
    """校验并冻结一份拓扑声明；非法即抛 ``CoordinationValidationError``。"""
    if not isinstance(raw, dict):
        raise CoordinationValidationError("spec 必须是对象")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise CoordinationValidationError("spec.name 必须是非空字符串")

    authority_raw = raw.get("authority") or {}
    communication_raw = raw.get("communication") or {}
    decomposition_raw = raw.get("decomposition") or {}
    coordination_raw = raw.get("coordination") or {}
    control_raw = raw.get("control") or {}
    governance_raw = raw.get("governance") or {}

    authority = AuthoritySpec(
        mode=_enum(authority_raw.get("mode"), AUTHORITY_MODES, "authority.mode"),
        coordinator=authority_raw.get("coordinator"),
    )
    communication = CommunicationSpec(
        mode=_enum(communication_raw.get("mode"), COMMUNICATION_MODES,
                   "communication.mode"),
    )
    stages = tuple(decomposition_raw.get("stages") or ())
    decomposition = DecompositionSpec(
        mode=_enum(decomposition_raw.get("mode"), DECOMPOSITION_MODES,
                   "decomposition.mode"),
        stages=stages,
    )
    coordination = CoordinationModeSpec(
        mode=_enum(coordination_raw.get("mode"), COORDINATION_MODES,
                   "coordination.mode"),
    )
    control = ControlSpec(
        mode=_enum(control_raw.get("mode"), CONTROL_MODES, "control.mode"),
    )
    governance = GovernanceSpec(
        mode=_enum(governance_raw.get("mode"), GOVERNANCE_MODES, "governance.mode"),
    )

    members = raw.get("members") or []
    if not isinstance(members, list) or not all(isinstance(m, str) and m for m in members):
        raise CoordinationValidationError("spec.members 必须是非空字符串数组")
    members = list(members)

    links = raw.get("parent_links") or {}
    if not isinstance(links, dict):
        raise CoordinationValidationError("spec.parent_links 必须是对象")
    for child, parent in links.items():
        if child not in members or parent not in members:
            raise CoordinationValidationError(
                f"parent_links 引了不存在的成员：{child!r}->{parent!r}")

    # -- 交叉规则 ---------------------------------------------------------- #
    if authority.mode == "centralized" or communication.mode == "star":
        coord = authority.coordinator
        if not coord:
            raise CoordinationValidationError(
                "centralized/star 拓扑必须指定 coordinator（谁拍板/经谁中转）")
        if coord not in members:
            raise CoordinationValidationError(
                f"coordinator {coord!r} 必须在 members 内")
    if decomposition.mode == "pipeline" and len(stages) < 2:
        raise CoordinationValidationError(
            "pipeline 分解必须有 ≥2 的有序 stages（没阶段排什么序）")
    if authority.mode == "hierarchical" or communication.mode == "tree":
        if not links:
            raise CoordinationValidationError(
                "hierarchical/tree 拓扑必须给出 parent_links（边即组织）")
        root, cyclic = _hierarchy_roots_and_cycle(members, dict(links))
        if cyclic:
            raise CoordinationValidationError("parent_links 存在 cycle（层级不能成环）")
        if not root:
            raise CoordinationValidationError(
                "parent_links 必须恰好一个根（零根是森林，多根是分裂）")
        if set(links) | {root} != set(members):
            raise CoordinationValidationError(
                "parent_links 必须覆盖全部非根成员（有人没编入组织）")

    return CoordinationSpec(
        name=name, authority=authority, communication=communication,
        decomposition=decomposition, coordination=coordination,
        control=control, governance=governance,
        members=tuple(members), parent_links=dict(links) or None,
    )


def describe_spec(spec: CoordinationSpec) -> dict[str, Any]:
    """人类可读的拓扑摘要（调度器/审计引用同一口径，不编造执行承诺）。"""
    topology_kind = f"{spec.authority.mode}-{spec.communication.mode}"
    execution = spec.decomposition.mode
    if spec.decomposition.mode == "pipeline":
        execution += f"({ '->'.join(spec.decomposition.stages) })"
    return {
        "name": spec.name,
        "topology_kind": topology_kind,
        "coordinator": spec.authority.coordinator,
        "member_count": len(spec.members),
        "members": list(spec.members),
        "execution": execution,
        "coordination": spec.coordination.mode,
        "control": spec.control.mode,
        "governance": spec.governance.mode,
    }
