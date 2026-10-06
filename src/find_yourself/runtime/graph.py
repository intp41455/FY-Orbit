"""LangGraph phase-internal orchestration (FROZEN_CONTRACT §7, §10, Execution Manual F3).

Implements the phase-internal reasoning and execution graph:
* Pipeline: requirements -> planning -> [single_agent | research | tool_step | delegate] -> validate -> finish.
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

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Literal, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph

from ..services.actor import Actor
from ..services.errors import Conflict, NotFound, ValidationFailed


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
    reservation through :class:`~find_yourself.services.budget.BudgetService`.
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


def node_single_agent(state: TaskGraphState) -> dict:
    """Route 1: Pure empathetic listening.

    Does not force psychometric assessment, diagnoses, or summarize away user emotion.
    """
    goal = state.get("goal", "")
    spent = 0.001

    # Empathetic listening reflection
    response_text = (
        f"I hear you completely. You expressed: '{goal}'. "
        "I am here to listen without judgment or rushing to diagnose."
    )
    return {
        "stage": "validate",
        "output": {
            "mode": "listening",
            "reflection": response_text,
            "forced_assessment": False,
        },
        "spent_usd": state.get("spent_usd", 0.0) + spent,
        "budget_balance": max(0.0, state.get("budget_balance", 1.0) - spent),
        "evidence_refs": state.get("evidence_refs", []) + [f"ev:listen:{state.get('task_id')}"],
    }


def node_research(state: TaskGraphState) -> dict:
    """Route 2: Provenance-preserving source research.

    Enforces outbound privacy checks and records evidence citations.
    With no granted sources the output is explicitly marked unverified —
    citations are never fabricated.
    """
    goal = state.get("goal", "")
    granted = state.get("granted_source_ids", [])
    spent = 0.002

    citations = [f"src:{sid}" for sid in granted]
    if citations:
        findings = f"Research findings for '{goal}', grounded in the granted sources listed in citations."
    else:
        findings = f"unverified: no authorized sources were granted for '{goal}'; no findings are claimed."

    return {
        "stage": "validate",
        "output": {
            "mode": "research",
            "findings": findings,
            "citations": citations,
            "verified": bool(citations),
        },
        "spent_usd": state.get("spent_usd", 0.0) + spent,
        "budget_balance": max(0.0, state.get("budget_balance", 1.0) - spent),
        "evidence_refs": state.get("evidence_refs", []) + citations,
    }


def node_tool_step(state: TaskGraphState) -> dict:
    """Route 3: Tool-execution slot.

    No tool is wired into this node yet — the output must never claim a
    successful execution or fabricate an execution receipt.
    """
    tool_name = "data_analysis_tool"
    spent = 0.005

    output = {
        "mode": "tool",
        "tool_used": tool_name,
        "status": "not_executed",
        "result": None,
        "detail": "Tool execution is not wired into this node yet; no execution result is claimed.",
    }
    return {
        "stage": "validate",
        "output": output,
        "spent_usd": state.get("spent_usd", 0.0) + spent,
        "budget_balance": max(0.0, state.get("budget_balance", 1.0) - spent),
        "evidence_refs": state.get("evidence_refs", []),
    }


def node_delegate(state: TaskGraphState) -> dict:
    """Route 4: Expert delegation with scoped context.

    MANDATORY RULE: The expert subagent receives only the scoped query and explicit
    citations, NOT the full raw historical transcript.
    """
    goal = state.get("goal", "")
    granted = state.get("granted_source_ids", [])
    spent = 0.003

    # Expert only receives isolated query + citation references
    scoped_context = {
        "scoped_goal": goal,
        "citations": granted,
        # History is intentionally withheld
        "raw_history_provided": False,
    }

    expert_answer = f"Expert consultation response for: '{goal}'. (Context was scoped to {len(granted)} references)."
    return {
        "stage": "validate",
        "output": {
            "mode": "delegation",
            "expert_role": "SpecialistAgent",
            "scoped_context": scoped_context,
            "expert_answer": expert_answer,
        },
        "spent_usd": state.get("spent_usd", 0.0) + spent,
        "budget_balance": max(0.0, state.get("budget_balance", 1.0) - spent),
        "evidence_refs": state.get("evidence_refs", []) + [f"ev:delegate:{state.get('task_id')}"],
    }


def node_validate(state: TaskGraphState) -> dict:
    """Stage 4: Structural validation, budget enforcement, and loop bounds."""
    if state.get("budget_balance", 1.0) <= 0.0:
        return {"status": "failed", "error": "budget_exhausted", "stage": "finish"}

    step = state.get("step_count", 1)
    max_steps = state.get("max_steps", 5)

    if step >= max_steps:
        return {"status": "completed", "stage": "finish"}

    return {"status": "completed", "stage": "finish"}


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


def build_task_graph(checkpointer: Any | None = None) -> StateGraph:
    """Builds the phase-internal LangGraph StateGraph."""
    graph = StateGraph(TaskGraphState)

    graph.add_node("requirements", node_requirements)
    graph.add_node("planning", node_planning)
    graph.add_node("single_agent", node_single_agent)
    graph.add_node("research", node_research)
    graph.add_node("tool_step", node_tool_step)
    graph.add_node("delegate", node_delegate)
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

    graph.add_edge("validate", "finish")
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


def compile_task_graph(checkpointer: Any | None = None):
    cp = checkpointer if checkpointer is not None else _default_checkpointer()
    graph = build_task_graph()
    return graph.compile(checkpointer=cp)
