"""AgentTeamService: 19 单Agent内部团队与逐节点模型配置.

Implements 19 号规格 §1–§8 on top of the *existing* task contract, model
gateway, canvas, budget, authorization and event machinery. It adds no second
scheduler: each member is a real :class:`Task` row whose ``root_task_id`` points
at the team root, so the shared root budget, the audit chain and the existing
event replay all apply unchanged.

What this service deliberately does **not** do:

* it never invents a "available" model — the list comes from
  :class:`~find_yourself.services.model_catalog.ModelCatalog`;
* it never returns a credential value — only ``credential_ref`` plus a boolean;
* it never claims an exact effective model when the provider hides routing —
  ``effective_confidence`` becomes ``unknown`` / ``auto``;
* it never silently swaps a model — an incompatible node blocks with a reason;
* it never lets an old run batch overwrite a newer one.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adapters.native_subagents import HostCapabilities, NativeSubAgentAdapter
from ..config import Settings
from ..db.models import BudgetReservation, Task
from ..db.team_models import (
    AgentControlCapabilities,
    AgentInstance,
    ControlRequest,
    ModelBinding,
    TeamDefinition,
    TeamEvent,
)
from ..db.types import utcnow
from ..runtime.gateway import ModelGateway
from .actor import Actor
from .budget import BudgetService
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from .model_catalog import ModelCatalog

#: Roles the built-in team templates use. Not a closed set: custom roles are
#: allowed, these only seed the defaults.
BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "engineering": {
        "name": "工程交付 · 规划 / 编码 / 审查",
        "mode": "system_managed",
        "members": [
            {"role": "coordinator", "title": "规划负责人", "agent_host": "find_yourself", "depends_on": []},
            {"role": "implementer", "title": "编码专家", "agent_host": "find_yourself", "depends_on": ["coordinator"]},
            {"role": "reviewer", "title": "审查专家", "agent_host": "find_yourself", "depends_on": ["implementer"]},
        ],
        "default_binding": {"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
    },
    "research": {
        "name": "研究交付 · 规划 / 检索 / 汇总",
        "mode": "system_managed",
        "members": [
            {"role": "coordinator", "title": "研究规划", "agent_host": "find_yourself", "depends_on": []},
            {"role": "retriever", "title": "资料检索", "agent_host": "find_yourself", "depends_on": ["coordinator"]},
            {"role": "synthesizer", "title": "汇总撰写", "agent_host": "find_yourself", "depends_on": ["retriever"]},
        ],
        "default_binding": {"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
    },
    "custom": {
        "name": "自定义分工",
        "mode": "system_managed",
        "members": [
            {"role": "coordinator", "title": "主协调", "agent_host": "find_yourself", "depends_on": []},
        ],
        "default_binding": {"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
    },
}

#: Every member shares the root budget; a member may never hold more than this
#: fraction of it in reservation at once without an explicit override.
DEFAULT_MEMBER_RESERVE_CAP_USD = 0.05


class AgentTeamService:
    def __init__(
        self,
        session: Session,
        audit: Any | None = None,
        *,
        budget: BudgetService | None = None,
        gateway: ModelGateway | None = None,
        catalog: ModelCatalog | None = None,
        native: NativeSubAgentAdapter | None = None,
        settings: Settings | None = None,
    ):
        self.session = session
        self.audit = audit
        self.budget = budget
        self.gateway = gateway if gateway is not None else ModelGateway(settings=settings)
        if budget is not None and self.gateway.budget is None:
            # The gateway must charge against the same budget service, otherwise
            # a member execution would make an unbudgeted provider call.
            self.gateway.budget = budget
        self.catalog = catalog or ModelCatalog(settings=settings, gateway=self.gateway)
        self.native = native or NativeSubAgentAdapter()
        self.settings = settings

    # ------------------------------------------------------------------
    # Read-only catalogs
    # ------------------------------------------------------------------
    def list_templates(self) -> list[dict[str, Any]]:
        return [
            {"id": tid, "name": t["name"], "mode": t["mode"],
             "members": [m["role"] for m in t["members"]]}
            for tid, t in BUILTIN_TEMPLATES.items()
        ]

    def probe_hosts(self, actor: Actor) -> list[dict[str, Any]]:
        actor.require_authenticated()
        return self.native.hosts()

    def list_models(self, actor: Actor) -> dict[str, Any]:
        actor.require_authenticated()
        return {
            "catalog_version": self.catalog.version,
            "providers": self.catalog.providers(),
            "models": self.catalog.list_models(),
            "real_model_configured": bool(self.gateway.configured),
        }

    # ------------------------------------------------------------------
    # Team draft lifecycle
    # ------------------------------------------------------------------
    def create_team(
        self,
        actor: Actor,
        *,
        name: str,
        mode: str = "system_managed",
        template_id: str | None = None,
        members: list[dict[str, Any]] | None = None,
        default_binding: dict[str, Any] | None = None,
        role_bindings: dict[str, Any] | None = None,
        budget_ref: dict[str, Any] | None = None,
        permission_ref: dict[str, Any] | None = None,
        canvas_instance_id: str | None = None,
        root_task_id: str | None = None,
        reason: str = "initial draft",
    ) -> TeamDefinition:
        actor.require_owner()
        if not name.strip():
            raise ValidationFailed("name_required", "Team name is required")
        if mode not in ("system_managed", "product_native"):
            raise ValidationFailed("bad_mode", f"Unsupported team mode: {mode}")

        if template_id and not members:
            tmpl = BUILTIN_TEMPLATES.get(template_id)
            if tmpl is None:
                raise ValidationFailed("unknown_template", f"Unknown team template: {template_id}")
            members = [dict(m) for m in tmpl["members"]]
            if default_binding is None:
                default_binding = dict(tmpl["default_binding"])

        member_dicts = [self._normalize_member(m) for m in (members or [])]
        if not member_dicts:
            raise ValidationFailed("members_required", "A team needs at least one member")
        roles = [m["role"] for m in member_dicts]
        if len(set(roles)) != len(roles):
            raise ValidationFailed("duplicate_role", f"Duplicate member roles: {roles}")
        self._assert_no_cycle(member_dicts, "member dependencies")

        team = TeamDefinition(
            id=f"team-{uuid4().hex[:12]}",
            owner_id=actor.owner_id,
            name=name.strip()[:200],
            mode=mode,
            state="draft",
            canvas_instance_id=canvas_instance_id,
            root_task_id=root_task_id,
            members=member_dicts,
            role_bindings=dict(role_bindings or {}),
            default_binding=dict(default_binding or {}),
            budget_ref=dict(budget_ref or {}),
            permission_ref=dict(permission_ref or {}),
            last_change_reason=reason,
            last_changed_by=actor.owner_id,
        )
        self.session.add(team)
        self.session.flush()
        self._emit_event(
            actor, team, "team.created",
            details={"mode": mode, "members": roles, "template_id": template_id},
        )
        return team

    def get_team(self, actor: Actor, team_id: str) -> TeamDefinition:
        team = self.session.get(TeamDefinition, team_id)
        if team is None or team.owner_id != actor.owner_id:
            raise NotFound(f"Team not found: {team_id}")
        return team

    def list_teams(self, actor: Actor) -> list[TeamDefinition]:
        rows = self.session.execute(
            select(TeamDefinition).where(TeamDefinition.owner_id == actor.owner_id)
            .order_by(TeamDefinition.created_at.desc())
        ).scalars().all()
        return list(rows)

    def update_team(
        self,
        actor: Actor,
        team_id: str,
        *,
        expected_version: int,
        patch: dict[str, Any],
        reason: str,
    ) -> TeamDefinition:
        """Mutate a draft with optimistic concurrency (19 §2 version trail)."""
        actor.require_owner()
        team = self.get_team(actor, team_id)
        if team.version != expected_version:
            raise Conflict(
                f"Team {team_id} is at version {team.version}; expected {expected_version}"
            )

        goal_changed = False
        if "name" in patch and patch["name"]:
            team.name = str(patch["name"])[:200]
        if "members" in patch and patch["members"] is not None:
            member_dicts = [self._normalize_member(m) for m in patch["members"]]
            roles = [m["role"] for m in member_dicts]
            if len(set(roles)) != len(roles):
                raise ValidationFailed("duplicate_role", f"Duplicate member roles: {roles}")
            self._assert_no_cycle(member_dicts, "member dependencies")
            team.members = member_dicts
            goal_changed = True
        if "default_binding" in patch and patch["default_binding"] is not None:
            team.default_binding = dict(patch["default_binding"])
        if "role_bindings" in patch and patch["role_bindings"] is not None:
            team.role_bindings = dict(patch["role_bindings"])
        if "budget_ref" in patch and patch["budget_ref"] is not None:
            team.budget_ref = dict(patch["budget_ref"])
        if "permission_ref" in patch and patch["permission_ref"] is not None:
            team.permission_ref = dict(patch["permission_ref"])

        if team.state in ("running", "paused"):
            # Goal/responsibility changes propagate to live members as a new
            # plan version; in-flight batches keep their frozen model.
            if goal_changed:
                team.plan_version += 1
                self._propagate_plan_version(actor, team)

        team.version += 1
        team.last_change_reason = reason
        team.last_changed_by = actor.owner_id
        self.session.flush()
        self._emit_event(
            actor, team, "team.updated",
            details={"version": team.version, "plan_version": team.plan_version,
                     "changed": sorted(patch.keys()), "reason": reason},
        )
        return team

    def rename_member(
        self,
        actor: Actor,
        team_id: str,
        *,
        role: str,
        title: str,
        expected_version: int,
        reason: str,
    ) -> TeamDefinition:
        """T2（UI 组实测缺口）：成员重命名——只改某成员的 title。

        复用 ``update_team`` 的 members 替换语义：乐观并发、version 递增、
        running 态 plan_version 传播、审计事件全部继承。绑定/依赖/模型配置
        原样保留（调用方拿到的成员 dict 已含这些键，原值回填）。role 不存在
        显式 404，绝不静默 no-op。
        """
        actor.require_owner()
        team = self.get_team(actor, team_id)
        if team.version != expected_version:
            raise Conflict(
                f"Team {team_id} is at version {team.version}; expected {expected_version}"
            )
        members = [dict(m) for m in (team.members or [])]
        hit = False
        for m in members:
            if m.get("role") == role:
                m["title"] = str(title)[:200]
                hit = True
        if not hit:
            raise NotFound(
                "member_role_not_found",
                f"Member role {role!r} not found in team {team_id}",
                404,
            )
        return self.update_team(
            actor, team_id, expected_version=expected_version,
            patch={"members": members}, reason=reason,
        )

    # ------------------------------------------------------------------
    # Model binding inheritance (19 §3): node > role > team > global
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_member(raw: dict[str, Any]) -> dict[str, Any]:
        role = str(raw.get("role") or "").strip()
        if not role:
            raise ValidationFailed("role_required", "Every member needs a role")
        deps = raw.get("depends_on") or []
        if not isinstance(deps, list):
            raise ValidationFailed("bad_dependencies", "depends_on must be a list of roles")
        return {
            "role": role[:64],
            "title": str(raw.get("title") or role)[:200],
            "agent_host": str(raw.get("agent_host") or "find_yourself")[:64],
            "provider_id": str(raw.get("provider_id") or "")[:64],
            "depends_on": [str(d)[:64] for d in deps],
            "goal": str(raw.get("goal") or "")[:1000],
        }

    @staticmethod
    def _assert_no_cycle(members: list[dict[str, Any]], label: str) -> None:
        graph = {m["role"]: list(m.get("depends_on") or []) for m in members}
        for deps in graph.values():
            for d in deps:
                if d not in graph:
                    raise ValidationFailed(
                        "unknown_dependency",
                        f"{label}: '{d}' is not a member of this team",
                    )
        WHITE, GREY, BLACK = 0, 1, 2
        color = {r: WHITE for r in graph}

        def visit(node: str, path: list[str]) -> None:
            color[node] = GREY
            for dep in graph.get(node, []):
                if color.get(dep) == GREY:
                    # `path` is the chain that led here, so the offending edge
                    # can be named exactly (19 §2: 定位具体边).
                    start = path.index(dep) if dep in path else 0
                    cycle = path[start:] + [dep]
                    raise Conflict(
                        "dependency_cycle",
                        f"Cyclic {label} detected: {' -> '.join(cycle)}. "
                        f"The team cannot start until the edge is removed.",
                    )
                if color.get(dep) == WHITE:
                    visit(dep, path + [dep])
            color[node] = BLACK

        for r in list(graph):
            if color[r] == WHITE:
                visit(r, [r])

    def resolve_binding(self, actor: Actor, team_id: str, role: str) -> dict[str, Any]:
        """Resolve the effective request for a node and label where it came from."""
        team = self.get_team(actor, team_id)
        node = (team.members and next((m for m in team.members if m["role"] == role), None)) or {}
        chain: list[tuple[str, dict[str, Any]]] = []
        if node.get("provider_id") or node.get("model_id"):
            chain.append(("node", {"provider_id": node.get("provider_id", ""),
                                   "model_id": node.get("model_id", "")}))
        role_b = (team.role_bindings or {}).get(role)
        if role_b:
            chain.append(("role", dict(role_b)))
        if team.default_binding:
            chain.append(("team", dict(team.default_binding)))
        chain.append(("global", {}))

        scope, chosen = "global", {}
        for name, value in chain:
            if value.get("model_id"):
                scope, chosen = name, value
                break

        return {
            "role": role,
            "requested_model": chosen.get("model_id", ""),
            "requested_provider": chosen.get("provider_id", ""),
            "inherited_from": scope if chosen.get("model_id") else "unresolved",
            "chain": [{"scope": n, "model_id": v.get("model_id", "")} for n, v in chain],
            "params": dict(chosen.get("params") or {}),
        }

    def set_member_binding(
        self,
        actor: Actor,
        team_id: str,
        role: str,
        *,
        provider_id: str,
        model_id: str,
        params: dict[str, Any] | None = None,
        expected_version: int | None = None,
        reason: str = "node model override",
    ) -> dict[str, Any]:
        """Set or clear a node-level override ('恢复继承' clears it)."""
        actor.require_owner()
        team = self.get_team(actor, team_id)
        if expected_version is not None and team.version != expected_version:
            raise Conflict(
                f"Team {team_id} is at version {team.version}; expected {expected_version}"
            )
        member = next((m for m in team.members if m["role"] == role), None)
        if member is None:
            raise NotFound(f"Member role not found: {role}")

        host_caps = self.native.get(member.get("agent_host", "find_yourself"))
        if not model_id:
            member.pop("provider_id", None)
            member.pop("model_id", None)
            member.pop("params", None)
        else:
            if not host_caps.supports_per_member_model:
                raise ValidationFailed(
                    "capability_unsupported",
                    f"Host '{host_caps.agent_host}' does not support per-member model "
                    f"override ({host_caps.reason}); the control stays disabled.",
                )
            opt = self.catalog.get_model(provider_id, model_id)
            if opt is None:
                raise ValidationFailed(
                    "model_not_in_catalog",
                    f"Model '{model_id}' is not in the configured capability catalog; "
                    "the UI may not offer it as available.",
                )
            if not opt.credential_configured:
                raise ValidationFailed(
                    "credential_not_configured",
                    f"Provider '{opt.provider_id}' has no configured credential "
                    f"(reference {opt.credential_ref}).",
                )
            if opt.pricing_status != "known":
                raise ValidationFailed(
                    "price_unknown",
                    f"Model '{model_id}' has no known unit price; refusing to start "
                    "and refusing to charge it as zero.",
                )
            member["provider_id"] = opt.provider_id
            member["model_id"] = opt.model_id
            member["params"] = dict(params or {})

        team.version += 1
        team.last_change_reason = reason
        team.last_changed_by = actor.owner_id
        self.session.flush()
        self._emit_event(
            actor, team, "team.member_binding_set",
            details={"role": role, "model_id": model_id or None,
                     "provider_id": provider_id if model_id else None,
                     "inherited": not model_id, "reason": reason},
        )
        return self.resolve_binding(actor, team_id, role)

    def set_role_binding(
        self, actor: Actor, team_id: str, role: str, model_id: str,
        provider_id: str = "", expected_version: int | None = None,
        reason: str = "role default model",
    ) -> dict[str, Any]:
        actor.require_owner()
        team = self.get_team(actor, team_id)
        if expected_version is not None and team.version != expected_version:
            raise Conflict(
                f"Team {team_id} is at version {team.version}; expected {expected_version}"
            )
        if role not in {m["role"] for m in team.members}:
            raise NotFound(f"Member role not found: {role}")
        bindings = dict(team.role_bindings or {})
        if model_id:
            opt = self.catalog.get_model(provider_id, model_id)
            if opt is None or not opt.credential_configured:
                raise ValidationFailed(
                    "model_unavailable",
                    f"Model '{model_id}' is not an available configured model for role '{role}'",
                )
            bindings[role] = {"provider_id": opt.provider_id, "model_id": opt.model_id}
        else:
            bindings.pop(role, None)
        team.role_bindings = bindings
        team.version += 1
        team.last_change_reason = reason
        team.last_changed_by = actor.owner_id
        self.session.flush()
        return self.resolve_binding(actor, team_id, role)

    # ------------------------------------------------------------------
    # Pre-flight validation (19 §3)
    # ------------------------------------------------------------------
    def validate_start(self, actor: Actor, team_id: str) -> dict[str, Any]:
        """Everything that must hold before a team may run. No side effects."""
        actor.require_owner()
        team = self.get_team(actor, team_id)
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        member_dicts = [self._normalize_member(m) for m in team.members]
        try:
            self._assert_no_cycle(member_dicts, "member dependencies")
        except Conflict as exc:
            blockers.append({"code": "dependency_cycle", "message": str(exc)})

        for m in member_dicts:
            role = m["role"]
            try:
                host_caps = self.native.get(m["agent_host"])
            except ValidationFailed as exc:
                blockers.append({"code": "unknown_agent_host", "role": role, "message": str(exc)})
                continue

            resolved = self.resolve_binding(actor, team_id, role)
            if not resolved["requested_model"]:
                blockers.append({
                    "code": "model_unresolved", "role": role,
                    "message": f"Member '{role}' has no model at any level "
                               "(node / role / team / global).",
                })
                continue
            opt = self.catalog.get_model(resolved["requested_provider"], resolved["requested_model"])
            if opt is None:
                blockers.append({
                    "code": "model_not_in_catalog", "role": role,
                    "message": f"Model '{resolved['requested_model']}' is not in the "
                               "configured capability catalog.",
                })
                continue
            if not opt.credential_configured:
                blockers.append({
                    "code": "credential_not_configured", "role": role,
                    "message": f"Credential reference {opt.credential_ref} is not configured.",
                })
            if opt.pricing_status != "known":
                blockers.append({
                    "code": "price_unknown", "role": role,
                    "message": f"Model '{opt.model_id}' has no known unit price; "
                               "unknown prices reserve conservatively or block.",
                })
            if resolved["params"] and not opt.supports_tools:
                warnings.append({
                    "code": "params_unsupported", "role": role,
                    "message": "Provider does not advertise support for the requested "
                               "parameters; they will not be sent.",
                })

            if team.mode == "product_native":
                if host_caps.usage_metering != "verified":
                    warnings.append({
                        "code": "usage_not_meterable", "role": role,
                        "message": f"Host '{host_caps.agent_host}' cannot precisely meter "
                                   "internal usage; tasks requiring a hard budget "
                                   "guarantee are blocked.",
                    })

        root_budget = float((team.budget_ref or {}).get("root_budget_usd") or 0.0)
        if root_budget <= 0:
            warnings.append({
                "code": "no_root_budget", "message":
                    "No root budget configured; members share the task default instead.",
            })

        return {
            "team_id": team.id,
            "version": team.version,
            "state": team.state,
            "can_start": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "real_model_configured": bool(self.gateway.configured),
        }

    # ------------------------------------------------------------------
    # Start: freeze resolution, create independent sessions
    # ------------------------------------------------------------------
    def start_team(
        self,
        actor: Actor,
        team_id: str,
        *,
        expected_version: int,
        root_task_id: str | None = None,
    ) -> dict[str, Any]:
        actor.require_owner()
        team = self.get_team(actor, team_id)
        if team.version != expected_version:
            raise Conflict(
                f"Team {team_id} is at version {team.version}; expected {expected_version}"
            )
        if team.state not in ("draft", "ready", "paused", "failed"):
            raise Conflict(f"Team {team_id} is {team.state}; cannot start")

        report = self.validate_start(actor, team_id)
        if not report["can_start"]:
            raise ValidationFailed(
                "start_blocked",
                "Team cannot start: " + "; ".join(b["message"] for b in report["blockers"]),
            )

        root_id = root_task_id or team.root_task_id
        if not root_id:
            root_id = f"root-{team.id}"
            self.session.add(Task(
                id=root_id, owner_id=actor.owner_id, root_task_id=root_id,
                goal=f"Agent team: {team.name}", domain="work", status="running",
                deadline=utcnow() + timedelta(days=7),
                idempotency_key=f"team-root-{team.id}",
            ))
            self.session.flush()
        team.root_task_id = root_id

        team.state = "running"
        team.started_at = team.started_at or utcnow()
        self.session.flush()

        views: list[dict[str, Any]] = []
        for spec in [self._normalize_member(m) for m in team.members]:
            views.append(self._ensure_instance(actor, team, spec, root_id))

        self.session.flush()
        self._emit_event(
            actor, team, "team.started",
            details={
                "root_task_id": root_id,
                "members": [v["role"] for v in views],
                "plan_version": team.plan_version,
                "real_model": bool(self.gateway.configured),
            },
        )
        return self.get_snapshot(actor, team_id)

    def _ensure_instance(
        self, actor: Actor, team: TeamDefinition, spec: dict[str, Any], root_id: str
    ) -> dict[str, Any]:
        role = spec["role"]
        resolved = self.resolve_binding(actor, team.id, role)
        opt = self.catalog.get_model(resolved["requested_provider"], resolved["requested_model"])
        assert opt is not None  # validated in validate_start

        inst = self.session.execute(
            select(AgentInstance).where(
                AgentInstance.team_id == team.id, AgentInstance.role == role
            )
        ).scalar_one_or_none()

        if inst is None:
            inst = AgentInstance(
                id=f"agt-{uuid4().hex[:12]}",
                team_id=team.id,
                role=role,
                title=spec.get("title") or role,
                agent_host=spec.get("agent_host") or "find_yourself",
                provider_id=opt.provider_id,
                # One independent session per member. Two roles on the same model
                # must not share it (19 §8 scenario 1).
                session_id=f"team-{team.id}-{role}-{uuid4().hex[:8]}",
                root_task_id=root_id,
                depends_on=list(spec.get("depends_on") or []),
                depth=0,
                max_steps=int((team.budget_ref or {}).get("max_steps") or 8),
                state="draft",
                requested_model=opt.model_id,
                effective_model=None,
                effective_confidence="not_executed",
                current_goal=spec.get("goal") or "",
                plan_version=team.plan_version,
            )
            self.session.add(inst)
            self.session.flush()

            subtask_id = f"sub-{uuid4().hex[:12]}"
            self.session.add(Task(
                id=subtask_id, owner_id=actor.owner_id,
                parent_task_id=root_id, root_task_id=root_id,
                goal=inst.current_goal or f"[{role}] {team.name}",
                domain="work", status="running", depth=1, max_steps=inst.max_steps,
                deadline=utcnow() + timedelta(days=7),
                idempotency_key=f"team-{team.id}-{role}",
            ))
            self.session.flush()
            inst.subtask_id = subtask_id

        binding = ModelBinding(
            id=f"bind-{uuid4().hex[:12]}",
            team_id=team.id,
            agent_instance_id=inst.id,
            scope=resolved["inherited_from"],
            scope_key=role,
            provider_id=opt.provider_id,
            requested_model=opt.model_id,
            effective_model=None,
            effective_confidence="not_executed",
            credential_ref=opt.credential_ref,
            credential_configured=opt.credential_configured,
            endpoint_ref=opt.endpoint_ref,
            params=dict(resolved["params"]),
            capability_snapshot=self.catalog.capability_snapshot(opt.provider_id, opt.model_id),
            pricing_version=self.catalog.version,
            # Frozen at start: later default edits never alter an in-flight batch.
            frozen=True,
        )
        self.session.add(binding)
        self.session.flush()
        inst.model_binding_id = binding.id
        inst.requested_model = opt.model_id
        # Restarting a member keeps its run-batch monotonic, so a late result from
        # an older batch can never overwrite the new one.
        inst.run_batch += 1
        inst.state = "running"
        inst.blocked_reason = ""
        inst.version += 1
        self.session.flush()

        self._emit_event(
            actor, team, "member.started",
            details={
                "role": role, "agent_instance_id": inst.id, "session_id": inst.session_id,
                "run_batch": inst.run_batch, "requested_model": opt.model_id,
                "inherited_from": resolved["inherited_from"],
                "effective_model": "未执行", "subtask_id": inst.subtask_id,
            },
            agent_instance_id=inst.id, run_batch=inst.run_batch,
        )
        return self._agent_view(actor, inst, binding)

    # ------------------------------------------------------------------
    # Plan propagation
    # ------------------------------------------------------------------
    def update_goal(
        self, actor: Actor, team_id: str, goal: str, *, reason: str = "goal changed"
    ) -> dict[str, Any]:
        actor.require_owner()
        team = self.get_team(actor, team_id)
        if not goal.strip():
            raise ValidationFailed("goal_required", "Goal must not be empty")
        members = [dict(m) for m in team.members]
        for m in members:
            if m["role"] == team.coordinator_role:
                m["goal"] = goal.strip()
        team.members = members
        team.plan_version += 1
        team.version += 1
        team.last_change_reason = reason
        team.last_changed_by = actor.owner_id
        affected = self._propagate_plan_version(actor, team)
        self.session.flush()
        self._emit_event(
            actor, team, "team.goal_updated",
            details={"plan_version": team.plan_version, "affected": affected, "reason": reason},
        )
        return {"plan_version": team.plan_version, "affected_members": affected}

    def _propagate_plan_version(self, actor: Actor, team: TeamDefinition) -> list[str]:
        instances = self._members(team.id)
        affected: list[str] = []
        for inst in instances:
            inst.plan_version = team.plan_version
            inst.version += 1
            affected.append(inst.role)
            task = self.session.get(Task, inst.subtask_id) if inst.subtask_id else None
            if task is not None:
                task.version += 1
                task.result = {
                    "plan_version": team.plan_version,
                    "note": "目标已更新；模型绑定在本批次内保持冻结。",
                }
            self._emit_event(
                actor, team, "member.plan_updated",
                details={"plan_version": team.plan_version, "role": inst.role,
                         "model_frozen": True},
                agent_instance_id=inst.id, run_batch=inst.run_batch,
            )
        return affected

    # ------------------------------------------------------------------
    # Snapshot & events
    # ------------------------------------------------------------------
    def _members(self, team_id: str) -> list[AgentInstance]:
        rows = self.session.execute(
            select(AgentInstance).where(AgentInstance.team_id == team_id)
            .order_by(AgentInstance.created_at.asc())
        ).scalars().all()
        return list(rows)

    def _binding_for(self, inst: AgentInstance) -> ModelBinding | None:
        if inst.model_binding_id:
            return self.session.get(ModelBinding, inst.model_binding_id)
        return None

    def _agent_view(
        self, actor: Actor, inst: AgentInstance, binding: ModelBinding | None = None
    ) -> dict[str, Any]:
        binding = binding or self._binding_for(inst)
        host_caps = self.native.get(inst.agent_host)
        cap_map = self._capability_row(inst.agent_host)
        effective = inst.effective_model
        return {
            "agent_instance_id": inst.id,
            "role": inst.role,
            "title": inst.title,
            "agent_host": inst.agent_host,
            "provider_id": inst.provider_id,
            "session_id": inst.session_id,
            "independent_session": True,
            "state": inst.state,
            "blocked_reason": inst.blocked_reason,
            "requested_model": inst.requested_model,
            "effective_model": effective if effective else "未执行",
            "effective_confidence": inst.effective_confidence,
            "inherited_from": binding.scope if binding else "unresolved",
            "inherit_chain": binding.scope if binding else "unresolved",
            "credential_ref": binding.credential_ref if binding else "",
            "credential_configured": bool(binding.credential_configured) if binding else False,
            "depends_on": list(inst.depends_on or []),
            "run_batch": inst.run_batch,
            "current_goal": inst.current_goal,
            "plan_version": inst.plan_version,
            "subtask_id": inst.subtask_id,
            "budget_reserved_usd": float(inst.budget_reserved_usd or 0),
            "budget_spent_usd": float(inst.budget_spent_usd or 0),
            "version": inst.version,
            "control": {
                "disabled_operations": [
                    op for op, state in host_caps.capabilities.items() if state != "verified"
                ],
                "supports_per_member_model": host_caps.supports_per_member_model,
            },
            "capability_recorded": bool(cap_map),
            "updated_at": inst.updated_at.isoformat() if inst.updated_at else None,
        }

    def _capability_row(self, agent_host: str) -> AgentControlCapabilities | None:
        return self.session.execute(
            select(AgentControlCapabilities).where(
                AgentControlCapabilities.agent_host == agent_host
            )
        ).scalar_one_or_none()

    def _record_capabilities(self, actor: Actor, caps: HostCapabilities) -> None:
        row = self._capability_row(caps.agent_host)
        if row is None:
            row = AgentControlCapabilities(
                id=f"cap-{uuid4().hex[:12]}", agent_host=caps.agent_host,
            )
            self.session.add(row)
        row.provider_id = caps.provider_id
        row.capabilities = dict(caps.capabilities)
        row.supports_per_member_model = caps.supports_per_member_model
        row.usage_metering = caps.usage_metering
        row.probe_source = caps.probe_source
        row.reason = caps.reason
        row.probed_at = utcnow()
        self.session.flush()

    def get_snapshot(self, actor: Actor, team_id: str) -> dict[str, Any]:
        team = self.get_team(actor, team_id)
        instances = self._members(team_id)
        for host in {i.agent_host for i in instances}:
            self._record_capabilities(actor, self.native.get(host))
        events = self.get_events(actor, team_id, cursor=0, limit=200)
        return {
            "team": {
                "id": team.id,
                "name": team.name,
                "mode": team.mode,
                "state": team.state,
                "version": team.version,
                "plan_version": team.plan_version,
                "root_task_id": team.root_task_id,
                "canvas_instance_id": team.canvas_instance_id,
                "coordinator_role": team.coordinator_role,
                "default_binding": self._public_binding(team.default_binding),
                "budget_ref": team.budget_ref or {},
                "permission_ref": team.permission_ref or {},
                "members": [self._normalize_member(m) for m in team.members],
                "last_change_reason": team.last_change_reason,
                "last_changed_by": team.last_changed_by,
                "real_model_configured": bool(self.gateway.configured),
                "started_at": team.started_at.isoformat() if team.started_at else None,
            },
            "members": [self._agent_view(actor, i) for i in instances],
            "events": events,
            "validation": self.validate_start(actor, team_id),
        }

    @staticmethod
    def _public_binding(binding: dict[str, Any] | None) -> dict[str, Any]:
        """Never echo a secret — only the reference and whether it is configured."""
        b = dict(binding or {})
        b.pop("api_key", None)
        b.pop("secret", None)
        b.pop("token", None)
        return b

    def get_events(
        self, actor: Actor, team_id: str, *, cursor: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        team = self.get_team(actor, team_id)
        rows = self.session.execute(
            select(TeamEvent).where(TeamEvent.team_id == team.id, TeamEvent.seq > cursor)
            .order_by(TeamEvent.seq.asc()).limit(max(1, min(limit, 1000)))
        ).scalars().all()
        return [
            {
                "seq": e.seq, "event_type": e.event_type, "task_id": e.task_id,
                "agent_instance_id": e.agent_instance_id, "run_batch": e.run_batch,
                "source": e.source, "details": e.details or {},
                "evidence_refs": e.evidence_refs or [],
                "created_at": e.created_at.isoformat(),
            }
            for e in rows
        ]

    def _emit_event(
        self,
        actor: Actor,
        team: TeamDefinition,
        event_type: str,
        *,
        details: dict[str, Any] | None = None,
        agent_instance_id: str | None = None,
        run_batch: int | None = None,
        task_id: str | None = None,
        source: str = "service",
        evidence_refs: list[str] | None = None,
    ) -> TeamEvent:
        max_seq = self.session.execute(
            select(func.max(TeamEvent.seq)).where(TeamEvent.team_id == team.id)
        ).scalar_one_or_none()
        seq = int(max_seq or 0) + 1
        row = TeamEvent(
            id=f"tev-{uuid4().hex[:16]}",
            team_id=team.id,
            seq=seq,
            event_type=event_type,
            task_id=task_id,
            agent_instance_id=agent_instance_id,
            run_batch=run_batch,
            source=source,
            details=details or {},
            evidence_refs=list(evidence_refs or []),
        )
        self.session.add(row)
        if agent_instance_id:
            inst = self.session.get(AgentInstance, agent_instance_id)
            if inst is not None:
                inst.last_event_seq = seq
        self.session.flush()
        return row

    # ------------------------------------------------------------------
    # Budget (19 §5): one root budget, retries and delegation included
    # ------------------------------------------------------------------
    def reserve_member_budget(
        self, actor: Actor, team_id: str, role: str, amount_usd: float
    ) -> dict[str, Any]:
        """Reserve against the *root* task so a member cannot bypass the cap."""
        actor.require_authenticated()
        team = self.get_team(actor, team_id)
        inst = next((i for i in self._members(team_id) if i.role == role), None)
        if inst is None:
            raise NotFound(f"Member role not found: {role}")
        if amount_usd <= 0:
            raise ValidationFailed("bad_amount", "Reservation amount must be positive")
        if self.budget is None:
            raise ValidationFailed(
                "budget_unavailable", "No budget service is wired for this deployment"
            )
        root_id = inst.root_task_id or team.root_task_id
        if not root_id:
            raise Conflict("team_not_started", "Team has no root task yet")
        member_cap = float(
            (team.budget_ref or {}).get("member_reserve_cap_usd", DEFAULT_MEMBER_RESERVE_CAP_USD)
        )
        if amount_usd > member_cap:
            raise ValidationFailed(
                "member_budget_exceeded",
                f"Member reservation ${amount_usd:.4f} exceeds the per-member cap "
                f"${member_cap:.4f}; the root budget is shared and cannot be bypassed.",
            )

        # The root budget covers every member plus their retries (19 §5). The
        # per-member cap alone is not enough: three members each under the cap
        # would still overrun a small root budget.
        root_cap = float((team.budget_ref or {}).get("root_budget_usd") or 0.0)
        if root_cap > 0:
            held = self.session.execute(
                select(func.coalesce(func.sum(BudgetReservation.amount), 0)).where(
                    BudgetReservation.task_id == root_id,
                    BudgetReservation.state.in_(["reserved", "unknown"]),
                )
            ).scalar_one()
            if float(held or 0) + amount_usd > root_cap + 1e-9:
                raise ValidationFailed(
                    "root_budget_exceeded",
                    f"Reserving ${amount_usd:.4f} would exceed the shared root budget "
                    f"${root_cap:.4f} (${float(held or 0):.4f} already held by members "
                    "and their retries).",
                )

        res = self.budget.reserve(
            actor, task_id=root_id, amount=amount_usd,
            idempotency_key=f"team:{team_id}:{role}:{inst.run_batch}:{uuid4().hex[:8]}",
            scope="team_member",
        )
        inst.budget_reserved_usd = float(inst.budget_reserved_usd or 0) + amount_usd
        self.session.flush()
        self._emit_event(
            actor, team, "member.budget_reserved",
            details={"role": role, "amount_usd": amount_usd, "reservation_id": res.id,
                     "root_task_id": root_id},
            agent_instance_id=inst.id, run_batch=inst.run_batch,
        )
        return {"reservation_id": res.id, "amount_usd": amount_usd, "root_task_id": root_id}

    # ------------------------------------------------------------------
    # Runtime control (19 §4)
    # ------------------------------------------------------------------
    def control(
        self,
        actor: Actor,
        team_id: str,
        *,
        operation: str,
        agent_instance_id: str | None = None,
        role: str | None = None,
        idempotency_key: str | None = None,
        expected_version: int | None = None,
        scope: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        actor.require_owner()
        team = self.get_team(actor, team_id)
        if operation not in (
            "pause", "resume", "reassign", "rework", "cancel", "switch_model", "spawn"
        ):
            raise ValidationFailed("bad_operation", f"Unsupported control operation: {operation}")

        key = idempotency_key or f"{team_id}:{operation}:{agent_instance_id or ''}:{uuid4().hex[:8]}"
        existing = self.session.execute(
            select(ControlRequest).where(ControlRequest.idempotency_key == key)
        ).scalar_one_or_none()
        if existing is not None:
            # Idempotent replay: the same key never performs the action twice.
            return {
                "id": existing.id, "operation": existing.operation, "state": existing.state,
                "result": existing.result or {}, "idempotent_replay": True,
            }

        inst: AgentInstance | None = None
        if agent_instance_id:
            inst = self.session.get(AgentInstance, agent_instance_id)
            if inst is None or inst.team_id != team.id:
                raise NotFound(f"Agent instance not found in team: {agent_instance_id}")
        elif role:
            inst = next((i for i in self._members(team_id) if i.role == role), None)
            if inst is None:
                # A member that exists only in the draft still has a host, so the
                # capability gate applies before any instance exists (§8.3).
                spec = next((m for m in team.members if m["role"] == role), None)
                if spec is None:
                    raise NotFound(f"Member role not found: {role}")
                self.native.get(spec.get("agent_host") or "find_yourself").require(operation)
            target_version = int(inst.version if inst else team.version)
        else:
            target_version = int(team.version)

        # Optimistic concurrency on the target (19 §2: every change keeps a
        # version, operator and reason). A stale control request is rejected
        # rather than applied to a member that has already moved on.
        if expected_version is not None and int(expected_version) != target_version:
            raise Conflict(
                "stale_control_version",
                f"{'Member' if inst else 'Team'} is at version {target_version}; the control "
                f"request presented {expected_version}.",
            )

        req = ControlRequest(
            id=f"ctl-{uuid4().hex[:12]}",
            team_id=team.id,
            agent_instance_id=inst.id if inst else None,
            operation=operation,
            idempotency_key=key,
            expected_version=target_version,
            operator=actor.owner_id,
            scope=dict(scope or {}),
            reason=reason,
            state="pending",
        )
        self.session.add(req)
        self.session.flush()

        try:
            result = self._apply_control(actor, team, inst, operation, scope or {}, reason, role)
            req.state = "applied"
            req.result = result
            req.target_version = inst.version if inst else team.version
            req.applied_at = utcnow()
        except (Conflict, ValidationFailed, PermissionDenied) as exc:
            req.state = "rejected"
            req.result = {
                "code": getattr(exc, "code", "error"),
                "message": str(exc),
            }
            self.session.flush()
            raise
        self.session.flush()
        return {
            "id": req.id, "operation": req.operation, "state": req.state,
            "result": req.result, "idempotent_replay": False,
        }

    def _apply_control(
        self,
        actor: Actor,
        team: TeamDefinition,
        inst: AgentInstance | None,
        operation: str,
        scope: dict[str, Any],
        reason: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        if operation in ("pause", "resume", "reassign", "rework", "cancel", "switch_model"):
            if inst is None:
                # A draft-only member: the capability gate above already ran.
                if operation != "switch_model":
                    raise ValidationFailed(
                        "team_not_started",
                        f"Operation '{operation}' needs a started member instance; "
                        "start the team first.",
                    )
                host_caps = self.native.get(
                    next(m for m in team.members if m["role"] == role).get("agent_host")
                    or "find_yourself"
                )
                host_caps.require(operation)
                raise ValidationFailed(
                    "team_not_started",
                    "switch_model needs a started member instance; start the team first.",
                )
            host_caps = self.native.get(inst.agent_host)
            host_caps.require(operation)

            if operation == "pause":
                inst.state = "paused"
            elif operation == "resume":
                inst.state = "running"
            elif operation == "reassign":
                target_role = str(scope.get("role") or "")
                if target_role and target_role != inst.role:
                    if target_role in {m["role"] for m in team.members}:
                        raise Conflict(
                            "role_taken",
                            f"Role '{target_role}' already exists in this team; "
                            "rename or remove it before reassigning.",
                        )
                    members = [dict(m) for m in team.members]
                    for m in members:
                        if m["role"] == inst.role:
                            m["role"] = target_role
                    team.members = members
                    inst.role = target_role
            elif operation == "rework":
                inst.state = "waiting_rework"
                inst.steps = 0
            elif operation == "cancel":
                inst.state = "cancelled"
                task = self.session.get(Task, inst.subtask_id) if inst.subtask_id else None
                if task is not None and task.status not in ("completed", "cancelled"):
                    task.status = "cancelled"
            elif operation == "switch_model":
                return self._switch_model(actor, team, inst, scope, reason)

            inst.version += 1
            self.session.flush()
            self._emit_event(
                actor, team, f"member.{operation}",
                details={"role": inst.role, "reason": reason,
                         "state": inst.state, "run_batch": inst.run_batch,
                         "scope": scope},
                agent_instance_id=inst.id, run_batch=inst.run_batch,
            )
            return {
                "role": inst.role, "state": inst.state, "run_batch": inst.run_batch,
                "version": inst.version,
            }

        # spawn
        role = str(scope.get("role") or "")
        if not role:
            raise ValidationFailed("role_required", "spawn needs a role")
        if role in {m["role"] for m in team.members}:
            raise Conflict("role_taken", f"Role '{role}' already exists in this team")
        host_caps = self.native.get(str(scope.get("agent_host") or "find_yourself"))
        host_caps.require("spawn")
        spec = self._normalize_member({
            "role": role,
            "title": scope.get("title") or role,
            "agent_host": scope.get("agent_host") or "find_yourself",
            "depends_on": scope.get("depends_on") or [],
            "goal": scope.get("goal") or "",
        })
        members = [dict(m) for m in team.members] + [spec]
        self._assert_no_cycle(members, "member dependencies")
        team.members = members
        team.version += 1
        self.session.flush()
        root_id = team.root_task_id
        view: dict[str, Any] = {}
        if root_id:
            view = self._ensure_instance(actor, team, spec, root_id)
        self._emit_event(
            actor, team, "member.spawned",
            details={"role": role, "reason": reason},
            agent_instance_id=view.get("agent_instance_id"),
        )
        return view or {"role": role, "state": "draft", "team_version": team.version}

    def _switch_model(
        self,
        actor: Actor,
        team: TeamDefinition,
        inst: AgentInstance,
        scope: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """Switch only takes effect for batches after the current safe boundary.

        The previous batch is sealed (never overwritten later), a handoff packet
        is written, and a NEW run batch is created. Nothing about the old batch
        is erased.
        """
        model_id = str(scope.get("model_id") or "")
        # An omitted provider_id means "the provider that actually serves this
        # model", not "keep the current one" — otherwise a cross-provider switch
        # could never be expressed, and the authorization gate would be skipped.
        provider_id = str(scope.get("provider_id") or "")
        if not model_id:
            raise ValidationFailed("model_required", "switch_model needs a target model_id")
        opt = self.catalog.get_model(provider_id, model_id)
        if opt is None:
            raise ValidationFailed(
                "model_not_in_catalog",
                f"Model '{model_id}' is not in the configured capability catalog.",
            )
        if not opt.credential_configured:
            raise ValidationFailed(
                "credential_not_configured",
                f"Provider '{opt.provider_id}' credential reference {opt.credential_ref} "
                "is not configured; refusing to switch silently.",
            )
        if opt.pricing_status != "known":
            raise ValidationFailed(
                "price_unknown", f"Model '{opt.model_id}' price is unknown; refusing to switch."
            )
        cross_provider = opt.provider_id != inst.provider_id
        if cross_provider and not bool((team.permission_ref or {}).get("allow_cross_provider")):
            raise PermissionDenied(
                "cross_provider_not_authorized",
                f"Switching from provider '{inst.provider_id}' to '{opt.provider_id}' "
                "changes the data destination and requires explicit authorization.",
                403,
            )

        previous_binding = self._binding_for(inst)
        handoff = self._handoff_packet(actor, team, inst, previous_binding)
        previous_batch = inst.run_batch

        new_binding = ModelBinding(
            id=f"bind-{uuid4().hex[:12]}",
            team_id=team.id,
            agent_instance_id=inst.id,
            scope="node",
            scope_key=inst.role,
            provider_id=opt.provider_id,
            requested_model=opt.model_id,
            effective_model=None,
            effective_confidence="not_executed",
            credential_ref=opt.credential_ref,
            credential_configured=opt.credential_configured,
            endpoint_ref=opt.endpoint_ref,
            params=dict(scope.get("params") or {}),
            capability_snapshot=self.catalog.capability_snapshot(opt.provider_id, opt.model_id),
            pricing_version=self.catalog.version,
            frozen=True,
        )
        self.session.add(new_binding)
        self.session.flush()

        if previous_binding is not None:
            previous_binding.frozen = True

        inst.provider_id = opt.provider_id
        inst.requested_model = opt.model_id
        inst.effective_model = None
        inst.effective_confidence = "not_executed"
        inst.model_binding_id = new_binding.id
        # A new batch: the previous batch's late result can no longer land.
        inst.run_batch += 1
        inst.state = "running"
        inst.version += 1
        self.session.flush()

        self._emit_event(
            actor, team, "member.model_switched",
            details={
                "role": inst.role,
                "from_model": previous_binding.requested_model if previous_binding else None,
                "to_model": opt.model_id,
                "cross_provider": cross_provider,
                "previous_run_batch": previous_batch,
                "new_run_batch": inst.run_batch,
                "handoff": handoff,
                "reason": reason,
                "side_effects": "unknown_needs_reconciliation"
                if (scope.get("side_effects_unknown") or False) else "none_pending",
            },
            agent_instance_id=inst.id, run_batch=inst.run_batch,
        )
        return {
            "role": inst.role,
            "previous_run_batch": previous_batch,
            "run_batch": inst.run_batch,
            "requested_model": opt.model_id,
            "effective_model": "未执行",
            "handoff": handoff,
            "version": inst.version,
        }

    def _handoff_packet(
        self,
        actor: Actor,
        team: TeamDefinition,
        inst: AgentInstance,
        previous: ModelBinding | None,
    ) -> dict[str, Any]:
        """Structured authorization handoff — no hidden reasoning, no secrets."""
        task = self.session.get(Task, inst.subtask_id) if inst.subtask_id else None
        return {
            "goal": inst.current_goal or (task.goal if task else ""),
            "acceptance": (task.result or {}).get("acceptance") if task else None,
            "plan_version": inst.plan_version,
            "completed": list((task.result or {}).get("completed", [])) if task else [],
            "artifacts": list((task.result or {}).get("artifacts", [])) if task else [],
            "sources": list((task.result or {}).get("sources", [])) if task else [],
            "failures": [inst.blocked_reason] if inst.blocked_reason else [],
            "pending_reconciliation": list((task.result or {}).get("pending", [])) if task else [],
            "remaining_budget_usd": float(
                (team.budget_ref or {}).get("root_budget_usd", 0.0) or 0.0
            ) - float(inst.budget_spent_usd or 0),
            "previous_model": previous.requested_model if previous else None,
            "context_policy": "referenced_compression_with_citations",
            "excluded": ["provider_private_state", "hidden_reasoning", "credentials"],
        }

    # ------------------------------------------------------------------
    # Result reporting: old batches may not overwrite new ones
    # ------------------------------------------------------------------
    def report_member_result(
        self,
        actor: Actor,
        team_id: str,
        role: str,
        *,
        run_batch: int,
        output: str = "",
        status: str = "completed",
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        actor.require_owner()
        team = self.get_team(actor, team_id)
        inst = next((i for i in self._members(team_id) if i.role == role), None)
        if inst is None:
            raise NotFound(f"Member role not found: {role}")
        if int(run_batch) != int(inst.run_batch):
            # Late result from a superseded batch. Recorded, never applied.
            self._emit_event(
                actor, team, "member.result_rejected",
                details={"role": role, "presented_batch": run_batch,
                         "current_batch": inst.run_batch,
                         "reason": "stale_batch_result_discarded"},
                agent_instance_id=inst.id, run_batch=inst.run_batch,
            )
            raise Conflict(
                "stale_run_batch",
                f"Result presented for run batch {run_batch} but the member is on batch "
                f"{inst.run_batch}; the late result was discarded, not applied.",
            )
        inst.state = status
        inst.steps += 1
        inst.version += 1
        task = self.session.get(Task, inst.subtask_id) if inst.subtask_id else None
        if task is not None:
            task.status = "completed" if status == "completed" else task.status
            task.result = {
                "output": output[:4000],
                "plan_version": inst.plan_version,
                "run_batch": inst.run_batch,
                "evidence_refs": list(evidence_refs or []),
            }
        self.session.flush()
        self._emit_event(
            actor, team, f"member.{status}",
            details={"role": role, "run_batch": run_batch,
                     "output_excerpt": output[:400]},
            agent_instance_id=inst.id, run_batch=run_batch,
            evidence_refs=list(evidence_refs or []),
        )
        return {"role": role, "state": inst.state, "run_batch": inst.run_batch,
                "version": inst.version}

    # ------------------------------------------------------------------
    # Execution (honest about credentials)
    # ------------------------------------------------------------------
    def execute_member(
        self,
        actor: Actor,
        team_id: str,
        role: str,
        *,
        prompt: str,
        max_tokens: int = 512,
        run_batch: int | None = None,
    ) -> dict[str, Any]:
        """Run one member through the real gateway under its frozen binding.

        With no provider configured this raises ``ModelNotConfigured``; it never
        fabricates an answer.
        """
        actor.require_authenticated()
        team = self.get_team(actor, team_id)
        inst = next((i for i in self._members(team_id) if i.role == role), None)
        if inst is None:
            raise NotFound(f"Member role not found: {role}")
        if run_batch is not None and int(run_batch) != int(inst.run_batch):
            raise Conflict(
                "stale_run_batch",
                f"Batch {run_batch} is superseded by {inst.run_batch}.",
            )
        binding = self._binding_for(inst)
        if binding is None or not binding.frozen:
            raise Conflict("binding_not_frozen", "Member has no frozen model binding")

        result = self.gateway.complete(
            actor,
            task_id=inst.subtask_id or inst.root_task_id or inst.id,
            model=binding.requested_model,
            prompt=prompt,
            target_domain="work",
            max_tokens=max_tokens,
        )
        # Record what the provider actually reported. A provider that hides
        # routing leaves effective_model unknown rather than pretending.
        reported = getattr(result, "provider_request_id", "") or ""
        if reported.startswith("mock-req-"):
            inst.effective_model = binding.requested_model
            inst.effective_confidence = "exact"
        else:
            inst.effective_model = None
            inst.effective_confidence = "unknown"
        binding.effective_model = inst.effective_model
        binding.effective_confidence = inst.effective_confidence
        inst.budget_spent_usd = float(inst.budget_spent_usd or 0) + float(result.settled_amount)
        self.session.flush()
        self._emit_event(
            actor, team, "member.executed",
            details={
                "role": role,
                "requested_model": binding.requested_model,
                "effective_model": inst.effective_model or "unknown",
                "effective_confidence": inst.effective_confidence,
                "session_id": inst.session_id,
                "usage": result.usage,
                "settled_usd": str(result.settled_amount),
            },
            agent_instance_id=inst.id, run_batch=inst.run_batch,
        )
        return {
            "role": role,
            "session_id": inst.session_id,
            "text": result.text,
            "requested_model": binding.requested_model,
            "effective_model": inst.effective_model or "unknown",
            "effective_confidence": inst.effective_confidence,
            "usage": result.usage,
            "settled_usd": str(result.settled_amount),
            "run_batch": inst.run_batch,
        }
