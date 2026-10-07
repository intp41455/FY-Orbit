"""统一能力网关 · 唯一裁决入口 (补齐包1 A-能力网关-01~06)。

**设计总纲：深 × 全 × 细 × 可审计，四维相乘**（比 Hermes/OpenClaw 的「更大
单一权限」更深的调控力）：

* **深**（levels.py）：本机触达分五级（沙箱内 → 沙箱外工作区 → 进程树 →
  桌面注入 → 驱动级），每级独立开关 + 能力清单 + 风险提示，越级需显式授权，
  可一键降级；
* **全**（types.py）：本机能力与跨 agent 能力（MCP/A2A/CLI/进程内插件，通道
  执行体在包3）共用同一张能力类型注册表与同一裁决管线，**新类型仅注册即接入**；
* **细**（grants.py）：能力 × 资源（路径前缀/域名白名单/进程白名单）× 时限 ×
  可撤回 的四元组授予/拒绝/撤销，默认拒绝；
* **可审计**（audit.py）：每次调用与每次变更入既有审计哈希链，可按任务/时间/
  能力检索，可逆动作可回滚，agent 不可篡改（哈希链 + 独立锚库）。

**与 A-Agent运行时-03（四路权限竞争裁决）同域合并**：本 broker 就是那个裁决
算法的落点——RBAC（actor.py）、automation 四档（permissions.py）、五级开关、
四元组授予、档位五路作为 gate 输入，任一否决即否决、全弃权即默认拒绝、
有放行且无否决才放行（多路冲突取最严）。不新建第二套权限系统：既有门全部
保留并收编为输入（见 gates.py 收编表）。

**唯一裁决入口（无旁路）**：新增能力（本机动作与跨 agent 调度）一律经
:meth:`CapabilityBroker.decide` / :meth:`CapabilityBroker.enforce`；
既有门的直接调用点仍在（收编 ≠ 删除），其迁移见交付报告未竟事项。
"""

from __future__ import annotations

from dataclasses import replace as _dataclass_replace
from pathlib import Path

from sqlalchemy.orm import Session

from ...db.types import utcnow
from ..actor import Actor
from ..audit import AuditService
from ..errors import PermissionDenied, ValidationFailed
from .audit import (
    ACTION_DEGRADE,
    ACTION_GRANT,
    ACTION_LEVEL_CHANGE,
    ACTION_PROFILE_CHANGE,
    ACTION_REVOKE,
    CapabilityAudit,
)
from .gates import (
    ActorGate,
    AutomationModeGate,
    DecisionContext,
    Gate,
    GrantGate,
    LevelGate,
    PathBoundaryGate,
    ProfileGate,
)
from .grants import EFFECT_ALLOW, GrantSpec, GrantStore
from .levels import LEVEL_ORDER, LEVEL_SPECS, LevelRegistry, is_escalation
from .profiles import PROFILE_NOVICE, ProfileManager
from .types import (
    CapabilityRequest,
    CapabilityTypeRegistry,
    CapabilityTypeSpec,
    Decision,
    Verdict,
)

__all__ = [
    "CapabilityBroker",
    "build_capability_broker",
]


class CapabilityBroker:
    """统一能力网关：表达（注册）、裁决（唯一入口）、授权（四元组）、审计（哈希链）。"""

    def __init__(
        self,
        *,
        session: Session,
        audit: AuditService,
        levels: LevelRegistry | None = None,
        grants: GrantStore | None = None,
        profiles: ProfileManager | None = None,
        automation_permissions=None,  # AutomationPermissionManager | None
        sandbox_roots: list[Path | str] | None = None,
        workspace_roots: list[Path | str] | None = None,
        gates: list[Gate] | None = None,
    ) -> None:
        self.s = session
        self._audit_svc = audit
        self.types = CapabilityTypeRegistry.with_builtins()
        self.levels = levels or LevelRegistry()
        self.grants = grants or GrantStore(session)
        self.profiles = profiles or ProfileManager()
        self.capability_audit = CapabilityAudit(audit)
        self._sandbox_roots = [Path(p) for p in (sandbox_roots or [".runtime/sandbox", ".runtime/artifacts"])]
        self._workspace_roots = [Path(p) for p in (workspace_roots or [Path.cwd()])]
        self._gates: list[Gate] = gates or [
            PathBoundaryGate(),
            ProfileGate(self.profiles),
            LevelGate(self.levels),
            AutomationModeGate(automation_permissions),
            ActorGate(),
            GrantGate(self.grants),
        ]

    # ------------------------------------------------------------------
    # 唯一裁决入口（01）
    # ------------------------------------------------------------------

    def decide(self, request: CapabilityRequest, *, actor: Actor | None = None) -> Decision:
        """裁决一次能力调用。放行与拒绝都会写入审计哈希链。"""
        # 主体归一：请求未带 subject 时从 actor 凭据推导；显式 subject 与凭据
        # 不一致由 ActorGate 否决（防冒名）。四元组授予按归一后的主体匹配。
        subject = request.subject or (actor.capability_subject() if actor is not None else "")
        if subject != request.subject:
            request = _dataclass_replace(request, subject=subject)
        spec = self.types.get(request.capability)
        if spec is None:
            decision = Decision(
                request=request, allowed=False, code="unknown_capability",
                reasons=(f"capability type not registered: {request.capability}",),
                profile=self.profiles.current(),
            )
            self.capability_audit.record_decision(actor or _system_actor(), decision)
            return decision

        level = request.level or spec.default_level
        escalated = is_escalation(spec.default_level, request.level)
        ctx = DecisionContext(
            actor=actor,
            sandbox_roots=tuple(self._sandbox_roots),
            workspace_roots=tuple(self._workspace_roots),
        )
        verdicts: list[Verdict] = []
        for gate in self._gates:
            if not gate.applies(spec, request, ctx):
                continue
            verdicts.append(gate.check(spec, request, ctx))

        # ---- 多路取最严：任一否决即否决；全弃权即默认拒绝；有放行且无否决才放行 ----
        denied = [v for v in verdicts if v.allowed is False]
        allowed = [v for v in verdicts if v.allowed is True]
        grant_id = next((v.grant_id for v in allowed if v.grant_id), None)
        if denied:
            decision = Decision(
                request=request, allowed=False, code="denied",
                reasons=tuple(v.reason for v in denied if v.reason),
                verdicts=tuple(verdicts), profile=self.profiles.current(),
                level=level, escalated=escalated,
            )
        elif not allowed:
            decision = Decision(
                request=request, allowed=False, code="default_deny",
                reasons=("no gate allowed this request (default-deny: least privilege)",),
                verdicts=tuple(verdicts), profile=self.profiles.current(),
                level=level, escalated=escalated,
            )
        else:
            decision = Decision(
                request=request, allowed=True, code="allowed",
                verdicts=tuple(verdicts), profile=self.profiles.current(),
                level=level, escalated=escalated, grant_id=grant_id,
            )
        self.capability_audit.record_decision(actor or _system_actor(), decision)
        return decision

    def enforce(self, request: CapabilityRequest, *, actor: Actor | None = None) -> Decision:
        """裁决并强制：拒绝即抛 :class:`PermissionDenied`（带全部否决原因）。"""
        decision = self.decide(request, actor=actor)
        if not decision.allowed:
            raise PermissionDenied(
                decision.code,
                f"capability '{request.capability}' denied: "
                + ("; ".join(decision.reasons) or decision.code),
            )
        return decision

    # ------------------------------------------------------------------
    # 注册 API（01 新类型仅注册即接入；03 包3 通道经此注册能力类型）
    # ------------------------------------------------------------------

    def register_capability_type(self, spec: CapabilityTypeSpec, *, replace: bool = False) -> None:
        """注册新能力类型。此后该能力即可被四元组授予并经同一管线裁决。"""
        self.types.register(spec, replace=replace)

    # ------------------------------------------------------------------
    # 授权管理（04；owner-only，agent/服务身份不可改授权 —— 不可篡改审计的一半）
    # ------------------------------------------------------------------

    @staticmethod
    def _require_owner(actor: Actor | None) -> Actor:
        if actor is None or actor.subject_type != "owner":
            raise PermissionDenied(
                "owner_only",
                "capability administration (grants/profile/levels) requires the authenticated owner session",
            )
        return actor

    def grant(
        self,
        spec: GrantSpec,
        *,
        actor: Actor,
    ):
        """授予/拒绝一条四元组。深级别能力强制短时效（默认最小必要的时限维）。"""
        owner = self._require_owner(actor)
        type_spec = self.types.get(spec.capability)
        if type_spec is None:
            raise ValidationFailed("unknown_capability", f"capability type not registered: {spec.capability}")
        self._enforce_grant_ttl_policy(spec, type_spec)
        row = self.grants.grant(spec, created_by=owner.owner_id or "owner", type_spec=type_spec)
        self.capability_audit.record_admin(
            owner, ACTION_GRANT, row.id,
            {
                "capability": row.capability, "subject": row.subject,
                "effect": row.effect, "resource_kind": row.resource_kind,
                "resource_pattern": row.resource_pattern, "level": row.level,
                "escalation": row.escalation, "task_id": row.task_id,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "revocable": row.revocable, "note": row.note,
            },
        )
        return row

    @staticmethod
    def _enforce_grant_ttl_policy(spec: GrantSpec, type_spec: CapabilityTypeSpec) -> None:
        """深级别**授予**必须短时效、可撤回；显式拒绝不受 TTL 约束（拒绝永远安全）。

        与 automation full 档「必须带 TTL」同一思路：越深的能力，授权越要
        短时效、越要可撤回。上限 24h（L4 8h，L5 4h）。
        """
        if spec.effect != EFFECT_ALLOW:
            return
        deep = spec.escalation or (
            spec.level is not None and LEVEL_ORDER.get(spec.level, 0) >= LEVEL_ORDER["L3"]
        ) or (type_spec.default_level is not None and LEVEL_ORDER.get(type_spec.default_level, 0) >= LEVEL_ORDER["L3"])
        max_ttl = {LEVEL_ORDER["L3"]: 24 * 3600, LEVEL_ORDER["L4"]: 8 * 3600, LEVEL_ORDER["L5"]: 4 * 3600}
        if deep:
            cap = max_ttl.get(LEVEL_ORDER.get(type_spec.default_level or "", 0), 24 * 3600)
            if spec.escalation and spec.level:
                cap = max_ttl.get(LEVEL_ORDER.get(spec.level, 0), cap)
            if spec.ttl_seconds is None and spec.expires_at is None:
                raise ValidationFailed(
                    "deep_grant_requires_ttl",
                    f"deep-level capability '{type_spec.name}' grants must be short-lived: pass ttl_seconds (<= {cap}s)",
                )
            ttl = spec.ttl_seconds
            if ttl is not None and ttl > cap:
                raise ValidationFailed(
                    "deep_grant_ttl_too_long",
                    f"deep-level grant TTL {ttl}s exceeds the {cap}s cap for '{type_spec.name}'",
                )
            if spec.expires_at is not None and spec.ttl_seconds is None:
                remaining = (spec.expires_at - utcnow()).total_seconds()
                if remaining > cap:
                    raise ValidationFailed(
                        "deep_grant_ttl_too_long",
                        f"deep-level grant expires in {int(remaining)}s, exceeding the {cap}s cap",
                    )
        if not spec.revocable and (
            type_spec.default_level is None or LEVEL_ORDER.get(type_spec.default_level, 0) >= LEVEL_ORDER["L3"]
        ):
            raise ValidationFailed(
                "deep_grant_must_be_revocable",
                f"deep-level capability '{type_spec.name}' grants must stay revocable",
            )

    def revoke(self, grant_id: str, *, actor: Actor):
        """撤销授予（即时生效并审计）。"""
        owner = self._require_owner(actor)
        row = self.grants.revoke(grant_id, by=owner.owner_id or "owner")
        self.capability_audit.record_admin(
            owner, ACTION_REVOKE, grant_id,
            {"capability": row.capability, "subject": row.subject, "revoked_at": row.revoked_at.isoformat()},
        )
        return row

    def list_grants(self, *, subject: str | None = None, capability: str | None = None):
        """当前有效授予（未撤销、未过期）。"""
        return self.grants.active(subject=subject, capability=capability)

    # ------------------------------------------------------------------
    # 档位切换（05）
    # ------------------------------------------------------------------

    def switch_profile(self, profile: str, *, actor: Actor) -> dict:
        """切档（owner-only），返回带提示与回退信息的通知并入审计。"""
        owner = self._require_owner(actor)
        notice = self.profiles.switch(profile)
        self.capability_audit.record_admin(
            owner, ACTION_PROFILE_CHANGE, profile,
            {"from": notice["from"], "to": notice["to"], "changed": notice["changed"]},
        )
        return notice

    def revert_profile(self, *, actor: Actor) -> dict:
        """一键回退到上一个档位（owner-only）。"""
        owner = self._require_owner(actor)
        notice = self.profiles.revert()
        self.capability_audit.record_admin(
            owner, ACTION_PROFILE_CHANGE, notice["to"],
            {"from": notice["from"], "to": notice["to"], "reverted": True},
        )
        return notice

    def current_profile(self) -> str:
        return self.profiles.current()

    # ------------------------------------------------------------------
    # 五级开关与降级（02）
    # ------------------------------------------------------------------

    def set_level(self, level: str, enabled: bool, *, actor: Actor) -> dict:
        """独立开关某一级别（owner-only）。开启深级别时给出风险提示。"""
        owner = self._require_owner(actor)
        spec = LEVEL_SPECS.get(level)
        if spec is None:
            raise ValidationFailed("unknown_level", f"unknown level: {level!r}")
        self.levels.set_enabled(level, enabled)
        self.capability_audit.record_admin(
            owner, ACTION_LEVEL_CHANGE, level,
            {"enabled": enabled, "risk_notes": spec.risk_notes},
        )
        return {"level": level, "enabled": enabled, "risk_notes": spec.risk_notes,
                "capabilities": list(spec.capabilities)}

    def degrade_to(self, level: str, *, actor: Actor) -> dict:
        """一键降级到目标级别：关闭其上所有级别（owner-only），并回收
        对应深级别的有效越级/普通授予（可撤回的才回收，留审计）。"""
        owner = self._require_owner(actor)
        closed_levels = self.levels.degrade_to(level)
        revoked: list[str] = []
        rank = LEVEL_ORDER[level]
        for row in self.grants.active():
            row_rank = LEVEL_ORDER.get(row.level or "", 0)
            default_rank = LEVEL_ORDER.get((self.types.get(row.capability).default_level or "") if self.types.get(row.capability) else "", 0)
            involved = max(row_rank, default_rank)
            if involved > rank and row.revocable:
                try:
                    self.grants.revoke(row.id, by=owner.owner_id or "owner")
                    revoked.append(row.id)
                except PermissionDenied:
                    continue
        self.capability_audit.record_admin(
            owner, ACTION_DEGRADE, level,
            {"closed_levels": closed_levels, "revoked_grants": revoked},
        )
        return {"degraded_to": level, "closed_levels": closed_levels, "revoked_grants": revoked}

    def describe_levels(self) -> list[dict]:
        """每级能力清单 + 风险提示 + 当前开关（02 验收面）。"""
        return self.levels.describe()

    # ------------------------------------------------------------------
    # 审计检索与回滚（06）
    # ------------------------------------------------------------------

    def search_audit(self, actor: Actor, **filters):
        """按 任务/时间/能力（可组合）检索能力审计帧。"""
        return self.capability_audit.search(actor, **filters)

    def register_reversal(self, undo, *, description: str) -> str:
        """为可逆动作登记撤销回调（06 可回滚）。"""
        return self.capability_audit.register_reversal(undo, description=description)

    def rollback(self, reversal_id: str, *, actor: Actor) -> dict:
        """执行回滚并入审计。"""
        self._require_owner(actor)
        return self.capability_audit.rollback(actor, reversal_id)

    def verify_audit_chain(self):
        """转发既有审计链校验（能力帧与其他帧同链同验）。"""
        return self._audit_svc.verify()


# ----------------------------------------------------------------------
# 工厂
# ----------------------------------------------------------------------


def _system_actor() -> Actor:
    """无 actor 上下文（内部运行时调用）时的审计归属。"""
    return Actor.service("capability-broker", "tool_gateway")


def build_capability_broker(session: Session, audit: AuditService, settings) -> CapabilityBroker:
    """从 :class:`find_yourself.config.Settings` 组装网关（api/deps 用）。

    配置段（补齐包1）::

        FY_CAPABILITY_PROFILE   novice | fine（默认 novice，零配置下载即用）
        FY_CAPABILITY_LEVELS    {"L3": true, ...} 级别开关覆盖
        FY_A2A_UPSTREAM_URL     补齐包3 使用（A2A client 上游地址）
    """
    # 惰性导入：automation registry 在模块顶层会牵出 skills.harness 链。
    from ..automation.registry import get_permissions

    artifacts = Path(getattr(settings, "artifacts_path", ".runtime/artifacts"))
    sandbox_roots = [".runtime/sandbox", artifacts]
    workspace_roots = [Path.cwd()]
    return CapabilityBroker(
        session=session,
        audit=audit,
        levels=LevelRegistry(overrides=dict(getattr(settings, "capability_levels", {}) or {})),
        grants=GrantStore(session),
        profiles=ProfileManager(default=getattr(settings, "capability_profile", PROFILE_NOVICE)),
        automation_permissions=get_permissions(),
        sandbox_roots=sandbox_roots,
        workspace_roots=workspace_roots,
    )
