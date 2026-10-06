"""统一能力网关 · 多路裁决门（现有门的收编层）(补齐包1 A-能力网关-01/04)。

裁决算法（与 A-Agent运行时-03「四路权限竞争裁决」**同域合并**，不是第二套权限系统）：

1. 每一路既有门被包成一个 :class:`Gate`，对请求返回 :class:`Verdict`：
   ``allowed=True``（放行）/ ``False``（否决）/ ``None``（弃权）。
2. broker 汇总所有门：**任一 False 即拒绝；无任何 True（全部弃权）也是拒绝
   ——这就是「默认最小必要（默认拒绝）」；有 True 且无 False 才放行**
   （多路冲突取最严）。
3. 收编关系（只包不改语义）：

   ============================  ==============================================
   门                            收编的既有实现
   ============================  =============================================
   :class:`PathBoundaryGate`     A-Claw安全-01 路径越界校验（``runtime/local_agents.py`` /
                                 ``runtime/sandbox.py`` 同语义，网关侧前置防线）
   :class:`ProfileGate`          授权档位（本包 profiles.py；novice 的零配置隐式放行）
   :class:`LevelGate`            五级分级开关（本包 levels.py）
   :class:`AutomationModeGate`   ``services/automation/permissions.py`` 四档权限门
   :class:`ActorGate`            ``services/actor.py`` RBAC（allowed_tools / bound_domains）
   :class:`GrantGate`            四元组授予（本包 grants.py）
   ============================  ==============================================

诚实边界：既有门的直接调用点（如某些旧 service 直接 ``actor.require_tool``）
仍保留其行为；它们此后应改经 broker 裁决（收编 ≠ 删除，见交付报告未竟事项）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..actor import Actor
from ..automation.permissions import AutomationPermissionManager
from .grants import GrantStore
from .levels import LEVEL_L1, LEVEL_L2, LEVEL_ORDER, LevelRegistry, is_escalation
from .profiles import PROFILE_NOVICE, ProfileManager
from .types import (
    DOMAIN_CROSS_AGENT,
    DOMAIN_LOCAL,
    RESOURCE_PATH,
    CapabilityRequest,
    CapabilityTypeSpec,
    PathEscape,
    Verdict,
    ensure_path_within,
)


@dataclass
class DecisionContext:
    """裁决上下文：门需要的外部输入（actor / 路径边界 / 各管理器）。"""

    actor: Actor | None = None
    #: L1 允许的根（沙箱 + 制品目录）。
    sandbox_roots: tuple[Path, ...] = ()
    #: L2 允许的根（工作区）。
    workspace_roots: tuple[Path, ...] = ()


class Gate:
    """门基类：不适用于该请求时返回弃权（None）。"""

    name = "gate"

    def applies(self, spec: CapabilityTypeSpec, req: CapabilityRequest, ctx: DecisionContext) -> bool:
        return True

    def check(self, spec: CapabilityTypeSpec, req: CapabilityRequest, ctx: DecisionContext) -> Verdict:
        raise NotImplementedError


class PathBoundaryGate(Gate):
    """A-Claw安全-01 的网关侧防线：路径类能力必须落在该级别允许的根内。

    L1 → sandbox_roots；L2 → workspace_roots（工作区根含沙箱时天然覆盖 L1，
    与「L2 比 L1 深」的分级一致）。越界拒绝码 ``path_escape``，**任何级别、
    任何档位、任何授予都不能旁路本门**。

    本门是**否决门**：通过时弃权（不投 True）——路径合法本身不构成放行理由，
    放行必须来自授权门（档位基线 / automation 档 / 授予 / service RBAC）。
    """

    name = "path_boundary"

    def applies(self, spec, req, ctx) -> bool:
        return spec.resource_kind == RESOURCE_PATH and req.resource is not None

    def check(self, spec, req, ctx) -> Verdict:
        level = req.level or spec.default_level
        if level == "L1":
            roots: tuple[Path, ...] = ctx.sandbox_roots
        else:
            # L2 及更深（越级到 L2+ 触达文件）：工作区根兜底；沙箱根永远也在内。
            roots = ctx.workspace_roots + ctx.sandbox_roots
        try:
            ensure_path_within(req.resource or "", roots)
        except PathEscape as exc:
            return Verdict(self.name, False, f"path_escape: {exc}")
        return Verdict(self.name, None, "path within allowed roots (veto-gate abstains)")


class ProfileGate(Gate):
    """授权档位门（05）。

    * **novice（小白友好档，粗粒度、零配置）**：隐式放行 **默认级别在 L1/L2
      的本机能力、非越级请求**（沙箱内与工作区的粗放行，路径边界仍生效）；
      L3+ 深级别、越级请求、跨 agent 能力一律弃权（由更严门/授予决定）。
    * **fine（细粒度档）**：**一律弃权**——没有隐式放行，一切按四元组授予；
      颗粒度换易用性。
    """

    name = "profile"

    def __init__(self, profiles: ProfileManager):
        self._profiles = profiles

    def check(self, spec, req, ctx) -> Verdict:
        if self._profiles.current() != PROFILE_NOVICE:
            return Verdict(self.name, None, "fine profile: explicit grants only")
        level = req.level or spec.default_level
        if (
            spec.domain == DOMAIN_LOCAL
            and not is_escalation(spec.default_level, req.level)
            and level in (LEVEL_L1, LEVEL_L2)
        ):
            return Verdict(self.name, True, f"novice coarse baseline ({spec.name} @ {level})")
        return Verdict(self.name, None, "novice: deep/escalated/cross-agent needs explicit grant")


class LevelGate(Gate):
    """五级开关门（02）：**纯否决门**（kill-switch）。

    目标级别未启用 → 否决；启用 → 弃权（级别开关本身永不构成放行，
    否则 fine 档下任何已启用级别都会绕过四元组授予）。
    越级的「显式授权」要求由 :class:`GrantGate` 承担。
    """

    name = "level"

    def __init__(self, levels: LevelRegistry):
        self._levels = levels

    def applies(self, spec, req, ctx) -> bool:
        # 凡带级别维度的能力（本机五级，或跨 agent 但落地在 L3 的 CLI 通道）
        # 都受级别总开关约束；无级别维度的跨 agent 能力弃权。
        return spec.default_level is not None

    def check(self, spec, req, ctx) -> Verdict:
        level = req.level or spec.default_level
        if level is None:
            return Verdict(self.name, None, "no level dimension")
        if level not in LEVEL_ORDER:
            return Verdict(self.name, False, f"unknown level: {level!r}")
        if not self._levels.enabled(level):
            spec_note = ""
            if spec.default_level and level != spec.default_level:
                spec_note = f" (default {spec.default_level}; escalation to {level} needs explicit grant)"
            return Verdict(self.name, False, f"level {level} is disabled{spec_note}")
        return Verdict(self.name, None, f"level {level} enabled (veto-gate abstains)")


class AutomationModeGate(Gate):
    """GUI 自动化四档权限门的收编（services/automation/permissions.py）。

    通过新增的非抛出 ``AutomationPermissionManager.verdict()`` 取裁决，
    语义与既有 ``require()`` 完全一致（off/readonly/safe/full 权重比较、
    full 档 TTL 过期回落 off）——原门保持不动，只是多了一个结构化出口。
    """

    name = "automation_mode"

    def __init__(self, manager: AutomationPermissionManager | None):
        self._manager = manager

    def applies(self, spec, req, ctx) -> bool:
        return spec.automation_min_mode is not None and self._manager is not None

    def check(self, spec, req, ctx) -> Verdict:
        assert spec.automation_min_mode is not None and self._manager is not None
        allowed, reason = self._manager.verdict(
            action=req.capability, min_mode=spec.automation_min_mode, tool_level=4
        )
        return Verdict(self.name, allowed, reason)


class ActorGate(Gate):
    """``services/actor.py`` RBAC 的收编：service 身份的 allowed_tools /
    bound_domains / 过期语义在此作为一路输入。

    * **owner 弃权（不是放行）**：owner 不是旁路——RBAC 对 owner 本就全通，
      若在此放行会让深级别能力（L3-L5）在 novice 档被 RBAC 单路放行，
      破坏默认拒绝。owner 的实际约束由档位/级别/授予/自动化各门承担。
    * service：工具名（能力名）必须在其 ``allowed_tools``；跨 agent 域资源
      必须命中其 ``bound_domains``；凭据过期即否决。
    * 请求 subject 与 actor 身份冲突 → 否决（防止 agent 冒名请求别的主体）。
    """

    name = "actor_rbac"

    def check(self, spec, req, ctx) -> Verdict:
        actor = ctx.actor
        if actor is None:
            return Verdict(self.name, None, "no actor context")
        if actor.is_expired():
            return Verdict(self.name, False, "actor credentials expired")
        subject = actor.capability_subject()
        if req.subject and subject and req.subject != subject:
            # 主体必须与凭据一致（agent 不能冒名请求其他主体）。
            return Verdict(self.name, False, f"subject mismatch: credential is {subject}, request claims {req.subject}")
        if actor.subject_type == "owner":
            return Verdict(self.name, None, "owner identity (bound by the other gates, not a bypass)")
        if not actor.can_use_tool(req.capability):
            return Verdict(self.name, False, f"RBAC: tool '{req.capability}' not in allowed_tools")
        if spec.domain == DOMAIN_CROSS_AGENT and req.resource and not actor.can_access_domain(req.resource):
            return Verdict(self.name, False, f"RBAC: domain '{req.resource}' not in bound_domains")
        return Verdict(self.name, True, "service identity within RBAC scopes")


class GrantGate(Gate):
    """四元组授予门（04）：deny 覆盖 allow；越级请求必须命中显式越级授予。"""

    name = "grant"

    def __init__(self, grants: GrantStore):
        self._grants = grants

    def check(self, spec, req, ctx) -> Verdict:
        deny = self._grants.find_deny(req)
        if deny is not None:
            return Verdict(self.name, False, f"explicit deny grant {deny.id} ({deny.note or 'no note'})")
        escalated = is_escalation(spec.default_level, req.level)
        allow = self._grants.find_allow(req, escalation_only=escalated)
        if allow is not None:
            return Verdict(self.name, True, f"grant {allow.id} ({allow.effect})", grant_id=allow.id)
        if escalated:
            return Verdict(self.name, None, f"escalation to {req.level} requires an explicit escalation grant")
        return Verdict(self.name, None, "no matching grant")


#: 裁决顺序：先物理边界与档位/级别（便宜且直白），再身份，最后数据授予。
DEFAULT_GATES: tuple[type[Gate], ...] = (
    PathBoundaryGate,
    ProfileGate,
    LevelGate,
    AutomationModeGate,
    ActorGate,
    GrantGate,
)
