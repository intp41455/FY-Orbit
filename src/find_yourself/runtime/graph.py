"""LangGraph phase-internal orchestration (FROZEN_CONTRACT §7, §10, Execution Manual F3).

Implements the phase-internal reasoning and execution graph:
* Pipeline: requirements -> planning -> [single_agent | research | tool_step | delegate] -> validate
  -> {planning | finish} (bounded loop while ``step_count < max_steps``).
* Graph State: task_id, attempt, stage, route, route_criterion, goal, domain, budget_balance,
  granted_source_ids, evidence_refs, step_count, max_steps, history, output, error.
* Route selection (ADR-07): an explicit, ORDERED criteria table (``ROUTE_CRITERIA``) is evaluated
  top-to-bottom before any model call; first match wins and the matched criterion name is recorded
  in the audit chain. tool_step and delegate carry guards (budget headroom / cycle detection).
* Four Verified Routes:
  1. Single Agent: Empathetic listening, reflection without unsolicited diagnosis or forced assessment.
  2. Source Research: Provenance-preserving research with source IDs and outbound privacy checks.
  3. Authorized Tool: Guarded tool execution with atomic budget reservation and settlement.
  4. Expert Delegation: Scoped delegation where the subagent receives targeted context, NOT raw transcripts.
* Checkpointer: persistent (sqlite on disk, T6-B) checkpointing ensuring idempotent
  stage replay without duplicated side effects; survives process death (S4).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Literal, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph

from ..contracts import Conflict, NotFound, ValidationFailed
from ..identity import Actor
from .conflict_guard import PHASE_PRE_ACTION, action_signal

# ---------------------------------------------------------------------------
# ADR-07: explicit, ORDERED route-criteria table.
#
# This table is a *rule asset*: it is a module-level constant so it can be
# reviewed and changed as data, never hidden in an if/elif chain. Criteria are
# evaluated top-to-bottom; the FIRST match wins. The table is evaluated BEFORE
# any model call, and a model-suggested route is never consulted (see
# ``evaluate_route``) — the model cannot bypass the table to pick its own route.
# ---------------------------------------------------------------------------

#: Route ids (kept identical to the historic literals for caller compatibility).
ROUTE_SINGLE_AGENT = "single_agent"
ROUTE_RESEARCH = "research"
ROUTE_TOOL_STEP = "tool_step"
ROUTE_DELEGATE = "delegate"


@dataclass(frozen=True)
class RouteGuardResult:
    """Outcome of a criterion guard. ``ok=False`` means: do not route here."""

    ok: bool
    reason: str = ""
    reservation_id: str | None = None


@dataclass
class RouteContext:
    """Everything a criterion predicate/guard may inspect.

    Service handles are optional; when a guard needs one and it is missing it
    must fail *closed* (refuse the route) rather than assume the check passed.
    """

    state: Mapping[str, Any]
    goal: str  # lower-cased goal, used for keyword matching
    budget: Any | None = None  # BudgetService
    auditor: Any | None = None  # AuditService
    actor: Actor | None = None


@dataclass(frozen=True)
class RouteCriterion:
    name: str
    route: str
    keywords: tuple[str, ...]
    description: str
    guard: Callable[[RouteContext], RouteGuardResult] | None = None


@dataclass(frozen=True)
class RouteDecision:
    route: str
    criterion: str
    reason: str
    reservation_id: str | None = None


def _guard_tool_budget(ctx: RouteContext) -> RouteGuardResult:
    """tool_step requires a KNOWN price and a proven budget headroom.

    Contract: an unknown price must never be waved through as zero cost. We
    therefore require an explicit ``tool_price_status == "known"`` and a
    concrete ``estimated_cost_usd``, then prove sufficiency with an *atomic*
    reservation through :class:`~find_yourself.budget.BudgetService`.
    A missing budget service is treated as failure to prove sufficiency.
    """
    state = ctx.state
    if state.get("tool_price_status") != "known":
        return RouteGuardResult(False, "price_unknown: 工具价格未知，禁止按零费用放行")
    raw_price = state.get("estimated_cost_usd")
    if raw_price is None:
        return RouteGuardResult(False, "price_unknown: 缺少 estimated_cost_usd，禁止按零费用放行")
    amount = Decimal(str(raw_price))
    if amount < 0:
        return RouteGuardResult(False, "bad_price: 预估费用为负")
    if ctx.budget is None or ctx.actor is None or not state.get("task_id"):
        return RouteGuardResult(False, "budget_unavailable: 未接线预算服务，无法证明额度充足")
    if amount == 0:
        # A price that is *known* to be zero needs no reservation, but it still
        # passed the known-price gate above (it is not "unknown treated as 0").
        return RouteGuardResult(True, "price_known_zero")
    try:
        reservation = ctx.budget.reserve(
            ctx.actor,
            task_id=str(state["task_id"]),
            amount=amount,
            idempotency_key=f"route:{state['task_id']}:{state.get('attempt', 1)}",
        )
    except (Conflict, ValidationFailed, NotFound) as exc:
        return RouteGuardResult(False, f"budget_insufficient: {getattr(exc, 'code', 'error')}")
    return RouteGuardResult(True, "budget_reserved", reservation.id)


def _proposed_delegation_members(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Collect a proposed delegation dependency graph, if one was supplied."""
    raw = state.get("delegation_graph") or state.get("subtasks") or []
    members: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role") or item.get("name")
        if not role:
            continue
        members.append({"role": str(role), "depends_on": list(item.get("depends_on") or [])})
    return members


def _guard_delegation_acyclic(ctx: RouteContext) -> RouteGuardResult:
    """delegate must run cycle detection on the proposed member graph.

    Reuses the existing detector in
    :class:`~find_yourself.services.agent_teams.AgentTeamService` rather than
    re-implementing graph traversal.
    """
    members = _proposed_delegation_members(ctx.state)
    if not members:
        return RouteGuardResult(True, "no_dependency_graph_supplied")
    # Lazy import keeps the runtime layer free of an import-order dependency on
    # the service layer; the capability itself is unchanged.
    from ..services.agent_teams import AgentTeamService

    try:
        AgentTeamService._assert_no_cycle(members, "delegation")
    except (Conflict, ValidationFailed) as exc:
        return RouteGuardResult(False, f"delegation_cycle: {getattr(exc, 'code', 'error')}")
    return RouteGuardResult(True, "acyclic")


#: THE ordered criteria table. First match wins. Do not reorder casually: the
#: order is the decision policy (e.g. "listen + research" routes to
#: single_agent because empathetic listening is listed first).
ROUTE_CRITERIA: tuple[RouteCriterion, ...] = (
    RouteCriterion(
        name="empathetic_single_agent",
        route=ROUTE_SINGLE_AGENT,
        keywords=("listen", "倾听", "feel", "陪伴", "talk"),
        description="共情倾听：陪伴与情绪表达，不做强制诊断或评估。",
    ),
    RouteCriterion(
        name="source_research",
        route=ROUTE_RESEARCH,
        keywords=("research", "search", "调查", "研究", "source"),
        description="带出处的资料检索，保留来源可溯源。",
    ),
    RouteCriterion(
        name="authorized_tool_step",
        route=ROUTE_TOOL_STEP,
        keywords=("tool", "calc", "execute", "run", "工具"),
        description="受预算约束的工具执行；须先原子预留且价格已知。",
        guard=_guard_tool_budget,
    ),
    RouteCriterion(
        name="expert_delegation",
        route=ROUTE_DELEGATE,
        keywords=("delegate", "expert", "subagent", "委派", "专家"),
        description="专家委派；须通过环检测，子代理只收窄上下文。",
        guard=_guard_delegation_acyclic,
    ),
)

#: Safe fallback when nothing matches (kept as a named criterion for audit).
FALLBACK_CRITERION = RouteCriterion(
    name="default_single_agent",
    route=ROUTE_SINGLE_AGENT,
    keywords=(),
    description="全不命中时的安全回落。",
)

#: Services used by criterion guards. The graph node has no session of its own,
#: so the application wires them here at setup time (see ``configure_route_runtime``).
ROUTE_RUNTIME: dict[str, Any] = {"budget": None, "auditor": None, "actor": None}


def configure_route_runtime(
    *, budget: Any = None, auditor: Any = None, actor: Actor | None = None
) -> None:
    """Wire the services that route guards need (budget reservation, audit)."""
    ROUTE_RUNTIME["budget"] = budget
    ROUTE_RUNTIME["auditor"] = auditor
    ROUTE_RUNTIME["actor"] = actor


def _criterion_matches(criterion: RouteCriterion, goal: str) -> bool:
    return any(keyword in goal for keyword in criterion.keywords)


def _emit_route_audit(
    auditor: Any,
    actor: Actor | None,
    state: Mapping[str, Any],
    decision: RouteDecision,
    rejections: list[dict[str, str]],
    model_suggested: str | None,
) -> None:
    """Record the chosen criterion (and any guard rejections) into the audit chain."""
    if auditor is None or actor is None:
        return
    target = str(state.get("task_id") or "") or None
    auditor.append(actor, "route.decided", target, {
        "route": decision.route,
        "criterion": decision.criterion,
        "reason": decision.reason,
        "reservation_id": decision.reservation_id,
        "rejections": list(rejections),
        # Observability without leaking private goal text.
        "goal_length": len(str(state.get("goal", "") or "")),
        "model_suggested_route": model_suggested,
        "model_route_ignored": bool(model_suggested),
        "contains_private_text": False,
    })


def evaluate_route(
    state: Mapping[str, Any],
    *,
    budget: Any = None,
    auditor: Any = None,
    actor: Actor | None = None,
) -> RouteDecision:
    """Evaluate the ordered criteria table and return the route + matched name.

    Precedence:
    1. An explicit caller-supplied ``route`` wins (test/API override).
    2. Otherwise the criteria table is evaluated top-to-bottom, first match wins.
    3. If nothing matches, fall back to ``single_agent``.

    A ``model_suggested_route`` in state is recorded for audit but NEVER used.
    """
    raw_goal = str(state.get("goal", "") or "")
    ctx = RouteContext(state=state, goal=raw_goal.lower(), budget=budget, auditor=auditor, actor=actor)
    rejections: list[dict[str, str]] = []

    explicit = state.get("route")
    if explicit:
        decision = RouteDecision(
            route=str(explicit),
            criterion="explicit_override",
            reason="caller supplied an explicit route",
        )
    else:
        decision = None
        for criterion in ROUTE_CRITERIA:
            if not _criterion_matches(criterion, ctx.goal):
                continue
            if criterion.guard is not None:
                guard = criterion.guard(ctx)
                if not guard.ok:
                    # Fail closed and keep evaluating the rest of the table.
                    rejections.append({"criterion": criterion.name, "reason": guard.reason})
                    continue
                decision = RouteDecision(
                    criterion.route,
                    criterion.name,
                    guard.reason or "matched",
                    guard.reservation_id,
                )
            else:
                decision = RouteDecision(criterion.route, criterion.name, "matched")
            break
        if decision is None:
            decision = RouteDecision(
                FALLBACK_CRITERION.route, FALLBACK_CRITERION.name, "no criterion matched"
            )

    model_suggested = state.get("model_suggested_route")
    _emit_route_audit(auditor, actor, state, decision, rejections, model_suggested)
    return decision


#: EXPLICIT BOUNDARY: ``TaskGraphState.stage`` is a **graph-internal milestone
#: marker** (node names such as ``validate`` / ``finish`` / ``finished`` /
#: ``exec_<route>``). It is NOT the persisted ``tasks.stage`` column, whose
#: allowed values are the ``workflows.models.Stage`` enum enforced by DB CHECK
#: ``ck_task_stage``. There is deliberately no bridge from this value into
#: ``Task.stage``: the only DB writer is ``CorePorts.task_update_stage``, and
#: every caller passes ``Stage.*.value``. Do not start persisting this field —
#: if that ever happens, the graph vocabulary must first be reconciled with the
#: ``Stage`` enum. Guarded by ``tests/unit/test_task_stage_constraint.py``.
GRAPH_STAGE_IS_NOT_PERSISTED = True


class TaskGraphState(TypedDict, total=False):
    task_id: str
    attempt: int
    stage: str
    route: Literal["single_agent", "research", "tool_step", "delegate"]
    route_criterion: str
    route_reason: str
    tool_reservation_id: str | None
    tool_price_status: str
    estimated_cost_usd: float | str
    delegation_graph: list[dict[str, Any]]
    model_suggested_route: str
    goal: str
    domain: str
    granted_source_ids: list[str]
    history: list[dict[str, str]]
    budget_balance: float
    spent_usd: float
    step_count: int
    max_steps: int
    evidence_refs: list[str]
    subtasks: list[dict[str, Any]]
    output: dict[str, Any]
    status: str
    error: str | None
    # --- 冲突闸门（B7）：调用方声明的执行事实，必须进 TypedDict 才会到达节点 ---
    agent: str
    agent_role: str
    target: str
    action: str
    claims: list[dict[str, Any]]
    input_refs: list[str]
    degradations: list[dict[str, Any]]


#: 每个执行节点自己声明的「动作 + 角色」——C2（无归属）/ C6（越权）的机检输入。
#: 动作与角色是节点的事实，不接受调用方伪造，因此放在这里而不是 state 里。
NODE_ACTIONS: dict[str, tuple[str, str]] = {
    ROUTE_SINGLE_AGENT: ("empathetic_reflection", "ListenerAgent"),
    ROUTE_RESEARCH: ("source_research", "ResearchAgent"),
    ROUTE_TOOL_STEP: ("data_analysis_tool", "ToolAgent"),
    ROUTE_DELEGATE: ("expert_delegation", "SpecialistAgent"),
}


def _conflict_gate(
    state: TaskGraphState, node_route: str, guard: Any | None
) -> tuple[str, list[dict[str, Any]]]:
    """执行动作**之前**的冲突闸门（B7）。

    阻断级 → 由 :class:`ConflictGuard` 抛 ``ConflictDetected``，本函数不返回：
    图在此终止，网关一次都不会被调用（动作与花费都不发生）。
    可降级级 → 返回可见记录，由节点写进 ``output["degradations"]``。
    """
    action, role = NODE_ACTIONS.get(node_route, (node_route, ""))
    if guard is None:
        return role, []
    records = guard.enforce(
        action_signal(state, action=action, role=role),
        phase=PHASE_PRE_ACTION,
        task_id=str(state.get("task_id") or ""),
        owner_id="",
        effect="action_downgraded",
    )
    return role, [r.to_public() for r in records]


def _guard_result(
    state: TaskGraphState, guard: Any | None, role: str, text: str
) -> tuple[str, list[dict[str, Any]]]:
    """模型文本落地后的 C4 人设脱敏（真实改写原文，不是打标记）。"""
    if guard is None or not text:
        return text, []
    sink: list[Any] = []
    out = guard.redact_persona(sink, text, role=role)
    return out, [r.to_public() for r in sink]


def _stamp_degradations(
    state: TaskGraphState, update: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    """把降级记录**追加**进节点产出（无降级时**不**改动输出形状）。

    追加而非覆盖：``degradations`` 是同一条通道（last-write-wins），任务级
    （pre_execution）与节点级（pre/post_action）的记录必须合并，否则后写的
    节点会把先写的任务级降级痕迹抹掉——那正是「别静默」要防的事。
    """
    if not records:
        return
    previous = list(state.get("degradations") or [])
    merged = (previous + records)[:20]
    update["degradations"] = merged
    update["output"] = {
        **update.get("output", {}),
        "degradations": merged,
    }


def node_requirements(state: TaskGraphState) -> dict:
    """Stage 1: Validates task scope, domain boundaries, and authorized data references."""
    goal = state.get("goal", "").strip()
    if not goal:
        return {"status": "failed", "error": "Goal cannot be empty", "stage": "requirements"}

    return {
        "stage": "planning",
        "status": "in_progress",
        "step_count": state.get("step_count", 0) + 1,
    }


def node_planning(state: TaskGraphState) -> dict:
    """Stage 2: selects the execution route via the ordered criteria table.

    The table (``ROUTE_CRITERIA``) is the sole policy for implicit routing and is
    evaluated before any model call; a model-suggested route is never consulted.
    The matched criterion name is surfaced in state and recorded in the audit
    chain so a route can be explained and regressed.
    """
    decision = evaluate_route(
        state,
        budget=ROUTE_RUNTIME["budget"],
        auditor=ROUTE_RUNTIME["auditor"],
        actor=ROUTE_RUNTIME["actor"],
    )
    update: dict[str, Any] = {
        "route": decision.route,
        "route_criterion": decision.criterion,
        "route_reason": decision.reason,
        "stage": f"exec_{decision.route}",
    }
    if decision.reservation_id:
        update["tool_reservation_id"] = decision.reservation_id
    return update


def route_decision(state: TaskGraphState) -> str:
    return state.get("route", "single_agent")


# ---------------------------------------------------------------------------
# Model-call plumbing for the execution nodes.
#
# Honesty red line (FY-A-1): every execution node must obtain its output AND its
# cost from a *real* model call. There is no template/fabrication fallback: when
# no gateway can serve the call the node reports an honest failure and books no
# spend. The gateway computes ``CallResult.settled_amount`` from the *actual*
# token usage, so the node never invents a cost figure.
# ---------------------------------------------------------------------------


def _runtime_actor() -> Actor:
    """Actor for graph-issued calls: the wired one, else a system owner identity."""
    actor = ROUTE_RUNTIME.get("actor")
    return actor if actor is not None else Actor.owner("system")


def _runtime_model_name(gateway: Any) -> str:
    """Resolve the model id from the gateway's own settings (never hard-coded per node)."""
    import os

    settings = getattr(gateway, "settings", None)
    name = getattr(settings, "model_name", "") if settings is not None else ""
    return str(name or os.environ.get("FY_MODEL_NAME") or "gpt-4o-mini")


def _default_gateway() -> Any:
    """Build the process-wide gateway when the host injects none.

    Mirrors ``companion._gateway``: the app settings plus the route runtime's
    budget service. Construction is best-effort — if it fails the nodes fall
    back to the honest "unconfigured" path (``model_not_configured``) instead of
    inventing output or spend.
    """
    try:
        from ..config import settings as app_settings
        from .gateway import ModelGateway

        return ModelGateway(app_settings(), ROUTE_RUNTIME.get("budget"))
    except Exception:  # noqa: BLE001 - a failed build degrades honestly to unconfigured
        return None


def _model_failure(mode: str, code: str) -> dict:
    """Honest non-success update: no spend, no budget change, no evidence.

    The node never invents a response or a cost. The failure is visible in the
    returned ``output``. (The graph's top-level loop ``status`` is owned by
    :func:`node_validate`, which this change deliberately leaves untouched.)
    """
    return {
        "status": "failed",
        "error": code,
        "stage": "validate",
        "output": {
            "mode": mode,
            "model_backed": False,
            "error": code,
        },
    }


def _call_model(
    gateway: Any, state: TaskGraphState, *, prompt: str, max_tokens: int = 512
) -> tuple[Any | None, str | None]:
    """Invoke the gateway, returning ``(result, error_code)`` and never raising.

    A missing/undeclared gateway or any provider failure becomes an error code
    the caller turns into an honest failure. On success the returned
    ``CallResult`` carries the real ``settled_amount`` derived from actual usage.
    """
    if gateway is None:
        return None, "model_not_configured"
    try:
        result = gateway.complete(
            _runtime_actor(),
            task_id=str(state.get("task_id") or "task"),
            model=_runtime_model_name(gateway),
            prompt=prompt,
            target_domain=str(state.get("domain") or "personal"),
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - every failure must degrade honestly
        code = getattr(exc, "code", None) or f"model_unavailable:{type(exc).__name__}"
        return None, str(code)
    return result, None


def _spend_update(state: TaskGraphState, settled: Decimal) -> dict[str, Any]:
    """Book the *actual* settled cost against the running totals."""
    spent = float(settled)
    return {
        "stage": "validate",
        "spent_usd": state.get("spent_usd", 0.0) + spent,
        "budget_balance": max(0.0, state.get("budget_balance", 1.0) - spent),
    }


def node_single_agent(
    state: TaskGraphState, *, gateway: Any = None, conflict_guard: Any = None
) -> dict:
    """Route 1: Pure empathetic listening, backed by a real model call.

    Does not force psychometric assessment, diagnoses, or summarize away user
    emotion. Without a serving model the node fails honestly — no canned
    reflection and no fabricated spend.
    """
    role, gate_records = _conflict_gate(state, ROUTE_SINGLE_AGENT, conflict_guard)
    goal = state.get("goal", "")
    prompt = (
        "You are an empathetic listener. Reflect the user's feeling without "
        "diagnosing, assessing, or rushing to solutions.\n"
        f"User: {goal}"
    )
    result, error = _call_model(gateway, state, prompt=prompt, max_tokens=512)
    if result is None:
        return _model_failure("listening", error or "model_not_configured")

    reflection, redacted = _guard_result(state, conflict_guard, role, result.text)
    update = _spend_update(state, result.settled_amount)
    update["output"] = {
        "mode": "listening",
        "reflection": reflection,
        "forced_assessment": False,
        "model_backed": True,
        "provider_request_id": result.provider_request_id,
        "usage": dict(result.usage),
    }
    update["evidence_refs"] = state.get("evidence_refs", []) + [
        f"ev:model:{result.provider_request_id}"
    ]
    _stamp_degradations(state, update, gate_records + redacted)
    return update


def node_research(
    state: TaskGraphState, *, gateway: Any = None, conflict_guard: Any = None
) -> dict:
    """Route 2: Provenance-preserving source research, backed by a real model call.

    Citations come only from the *granted* source ids; with none granted the
    output is explicitly marked unverified. Findings are the model's real text,
    never a fabricated summary; without a serving model the node fails honestly.
    """
    role, gate_records = _conflict_gate(state, ROUTE_RESEARCH, conflict_guard)
    goal = state.get("goal", "")
    granted = state.get("granted_source_ids", [])
    citations = [f"src:{sid}" for sid in granted]
    sources = ", ".join(granted) if granted else "(no authorized sources granted)"
    prompt = (
        "Answer strictly from the authorized sources listed below and cite their "
        "ids. If no sources are provided, state that the result is unverified.\n"
        f"Authorized sources: {sources}\nQuestion: {goal}"
    )
    result, error = _call_model(gateway, state, prompt=prompt, max_tokens=768)
    if result is None:
        return _model_failure("research", error or "model_not_configured")

    findings, redacted = _guard_result(state, conflict_guard, role, result.text)
    update = _spend_update(state, result.settled_amount)
    update["output"] = {
        "mode": "research",
        "findings": findings,
        "citations": citations,
        "verified": bool(citations),
        "model_backed": True,
        "provider_request_id": result.provider_request_id,
        "usage": dict(result.usage),
    }
    update["evidence_refs"] = state.get("evidence_refs", []) + citations
    _stamp_degradations(state, update, gate_records + redacted)
    return update


def node_tool_step(
    state: TaskGraphState, *, gateway: Any = None, conflict_guard: Any = None
) -> dict:
    """Route 3: Guarded tool-execution slot, backed by a real model call.

    No tool is actually wired into this node, so the execution result is
    honestly reported as ``not_executed`` — the model call only *plans* the step
    and its real settled cost is booked. No execution receipt is ever
    fabricated; without a serving model the node fails honestly.
    """
    role, gate_records = _conflict_gate(state, ROUTE_TOOL_STEP, conflict_guard)
    goal = state.get("goal", "")
    tool_name = "data_analysis_tool"
    prompt = (
        "Plan (do not execute) a single tool step for the goal below and return "
        "the intended tool arguments only; no execution result may be claimed.\n"
        f"Tool: {tool_name}\nGoal: {goal}"
    )
    result, error = _call_model(gateway, state, prompt=prompt, max_tokens=512)
    if result is None:
        return _model_failure("tool", error or "model_not_configured")

    planned, redacted = _guard_result(state, conflict_guard, role, result.text)
    update = _spend_update(state, result.settled_amount)
    update["output"] = {
        "mode": "tool",
        "tool_used": tool_name,
        "status": "not_executed",
        "result": None,
        "planned_step": planned,
        "detail": "Tool execution is not wired into this node; only a model-backed plan is returned.",
        "model_backed": True,
        "provider_request_id": result.provider_request_id,
        "usage": dict(result.usage),
    }
    update["evidence_refs"] = state.get("evidence_refs", [])
    _stamp_degradations(state, update, gate_records + redacted)
    return update


def node_delegate(
    state: TaskGraphState, *, gateway: Any = None, conflict_guard: Any = None
) -> dict:
    """Route 4: Expert delegation with scoped context, backed by a real model call.

    MANDATORY RULE: the expert subagent receives only the scoped query and
    explicit citations, NOT the full raw historical transcript. The answer is the
    model's real text; without a serving model the node fails honestly.
    """
    role, gate_records = _conflict_gate(state, ROUTE_DELEGATE, conflict_guard)
    goal = state.get("goal", "")
    granted = state.get("granted_source_ids", [])
    # Expert only receives an isolated query + citation references; raw history
    # is intentionally withheld.
    scoped_context = {
        "scoped_goal": goal,
        "citations": list(granted),
        "raw_history_provided": False,
    }
    prompt = (
        "You are a specialist consultant. Answer only from the scoped query and "
        "the cited references; assume no access to any raw conversation.\n"
        f"Scoped query: {goal}\nCitations: {', '.join(granted) or '(none)'}"
    )
    result, error = _call_model(gateway, state, prompt=prompt, max_tokens=768)
    if result is None:
        return _model_failure("delegation", error or "model_not_configured")

    answer, redacted = _guard_result(state, conflict_guard, role, result.text)
    update = _spend_update(state, result.settled_amount)
    update["output"] = {
        "mode": "delegation",
        "expert_role": "SpecialistAgent",
        "scoped_context": scoped_context,
        "expert_answer": answer,
        "model_backed": True,
        "provider_request_id": result.provider_request_id,
        "usage": dict(result.usage),
    }
    update["evidence_refs"] = state.get("evidence_refs", []) + [
        f"ev:model:{result.provider_request_id}"
    ]
    _stamp_degradations(state, update, gate_records + redacted)
    return update


def node_validate(state: TaskGraphState) -> dict:
    """Stage 4: budget enforcement + the single collection point of the loop bound.

    This node is the ONLY close of the ``planning -> route -> validate`` cycle. Each
    pass advances ``step_count`` by exactly 1, and the two non-failure branches
    return *different* values (``stage="planning"`` to iterate vs ``stage="finish"``
    to stop) — this eliminates the earlier dead-code duplication where both branches
    returned an identical ``{"status": "completed", "stage": "finish"}``.

    Termination guarantee: ``step_count`` is strictly increasing and the only loop
    guard is ``step_count < max_steps`` (``max_steps`` is finite int), so the cycle
    converges to ``finish`` after at most ``max_steps`` passes. A budget-exhausted
    run also lands on ``finish`` (never spins). Honesty red line: this node never
    reports a completion state it did not actually reach.
    """
    step = int(state.get("step_count", 0)) + 1
    max_steps = int(state.get("max_steps", 5))

    if state.get("budget_balance", 1.0) <= 0.0:
        return {
            "status": "failed",
            "error": "budget_exhausted",
            "stage": "finish",
            "step_count": step,
        }

    if step >= max_steps:
        # Step bound reached: the bounded loop closes here (its normal completion).
        return {"status": "completed", "stage": "finish", "step_count": step}

    # Headroom remains: hand back to planning for the next bounded iteration.
    return {"status": "in_progress", "stage": "planning", "step_count": step}


def route_after_validate(state: TaskGraphState) -> str:
    """Exit router for ``validate``: loop back to planning, or terminate.

    Reads only the ``stage`` marker set by :func:`node_validate`, so the back-edge
    can never loop back on a terminal (``finish``) or budget-exhausted pass.
    """
    return "planning" if state.get("stage") == "planning" else "finish"


def node_finish(state: TaskGraphState) -> dict:
    """Stage 5: Final settlement and result packaging."""
    status = state.get("status", "completed")
    return {
        "stage": "finished",
        "status": status,
        "output": {
            **state.get("output", {}),
            "task_id": state.get("task_id"),
            "attempt": state.get("attempt", 1),
            "evidence_count": len(state.get("evidence_refs", [])),
            "final_spent_usd": state.get("spent_usd", 0.0),
        },
    }


def build_task_graph(
    checkpointer: Any | None = None, gateway: Any | None = None,
    conflict_guard: Any | None = None,
) -> StateGraph:
    """Builds the phase-internal LangGraph StateGraph.

    ``gateway`` (optional) is the model gateway the execution nodes call. When
    omitted, the process-wide gateway is resolved from settings at build time; if
    none can be built the nodes degrade *honestly* (``model_not_configured``)
    rather than fabricate output or spend. It is bound into each execution node
    via :func:`functools.partial`, so the public node signature
    ``node(state) -> dict`` is unchanged for every existing caller.

    ``conflict_guard`` (optional) is the B7 冲突闸门；绑定进四个执行节点，
    因此阻断级冲突会在**网关被调用之前**拒绝动作。``None`` = 不装闸门
    （仅供纯函数级单测直接调用节点时使用；内核一定会装）。
    """
    if gateway is None:
        gateway = _default_gateway()
    graph = StateGraph(TaskGraphState)

    graph.add_node("requirements", node_requirements)
    graph.add_node("planning", node_planning)
    graph.add_node("single_agent", functools.partial(
        node_single_agent, gateway=gateway, conflict_guard=conflict_guard))
    graph.add_node("research", functools.partial(
        node_research, gateway=gateway, conflict_guard=conflict_guard))
    graph.add_node("tool_step", functools.partial(
        node_tool_step, gateway=gateway, conflict_guard=conflict_guard))
    graph.add_node("delegate", functools.partial(
        node_delegate, gateway=gateway, conflict_guard=conflict_guard))
    graph.add_node("validate", node_validate)
    graph.add_node("finish", node_finish)

    graph.add_edge(START, "requirements")
    graph.add_edge("requirements", "planning")

    graph.add_conditional_edges(
        "planning",
        route_decision,
        {
            "single_agent": "single_agent",
            "research": "research",
            "tool_step": "tool_step",
            "delegate": "delegate",
        },
    )

    graph.add_edge("single_agent", "validate")
    graph.add_edge("research", "validate")
    graph.add_edge("tool_step", "validate")
    graph.add_edge("delegate", "validate")

    # Bounded back-edge: ``validate`` loops to ``planning`` while headroom
    # remains, otherwise terminates. Termination is guaranteed by
    # ``node_validate`` advancing ``step_count`` every pass (its only loop
    # guard is the finite ``step_count < max_steps``).
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {"planning": "planning", "finish": "finish"},
    )
    graph.add_edge("finish", END)

    return graph


def _default_checkpointer() -> Any:
    """T6-B（补 G1）：默认检查点必须落盘——进程一崩进度全丢曾是 S4 的直接违反。

    优先级：显式 checkpointer 参数 > ``FY_CHECKPOINT_DB`` > settings.
    checkpoint_db_path > ``.runtime/checkpoints/langgraph.sqlite``。
    初始化失败**原样抛出**（诚实红线：不许静默降级回内存态检查点假装安全）。
    """
    import os

    from ..config import settings as app_settings
    from .checkpoint_sqlite import SqliteCheckpointer

    path = (
        os.environ.get("FY_CHECKPOINT_DB")
        or getattr(app_settings, "checkpoint_db_path", "")
        or ".runtime/checkpoints/langgraph.sqlite"
    )
    return SqliteCheckpointer(path)


def compile_task_graph(checkpointer: Any | None = None, gateway: Any | None = None,
                       conflict_guard: Any | None = None):
    cp = checkpointer if checkpointer is not None else _default_checkpointer()
    graph = build_task_graph(gateway=gateway, conflict_guard=conflict_guard)
    return graph.compile(checkpointer=cp)
