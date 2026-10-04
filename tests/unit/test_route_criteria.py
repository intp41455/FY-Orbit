"""ADR-07: ordered, observable route-criteria table.

These tests pin the *decision policy* of ``runtime/graph.py``:

* criteria are an explicit ordered table (a rule asset), first match wins;
* the matched criterion name is returned and recorded in the audit chain;
* tool_step is guarded by an atomic budget reservation and refuses an unknown
  price (never treated as zero cost);
* delegate is guarded by the existing team cycle detector;
* a model-suggested route can never bypass the table;
* when nothing matches, we fall back to single_agent.

They are pure-local: in-memory SQLite, no model calls.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from find_yourself.db.models import AuditEvent, BudgetReservation, Task
from find_yourself.db.types import utcnow
from find_yourself.runtime import graph
from find_yourself.runtime.graph import (
    FALLBACK_CRITERION,
    ROUTE_CRITERIA,
    RouteDecision,
    evaluate_route,
    node_planning,
    route_decision,
)
from find_yourself.services.actor import Actor
from find_yourself.services.budget import BudgetLimits, BudgetService

OWNER = "owner-1"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _decision(**state) -> RouteDecision:
    state.setdefault("goal", "")
    return evaluate_route(state)


def _make_task(session, task_id: str = "t-route", *, status: str = "running") -> Task:
    task = Task(
        id=task_id, owner_id=OWNER, goal="route test", domain="work",
        status=status, deadline=utcnow(), idempotency_key=f"k-{task_id}",
    )
    session.add(task)
    session.flush()
    return task


def _budget(session, audit, *, per_task: str = "0.50") -> BudgetService:
    return BudgetService(
        session, audit,
        limits=BudgetLimits(per_task_usd=Decimal(per_task), per_month_usd=Decimal("10.00")),
    )


def _route_events(session) -> list[AuditEvent]:
    return list(
        session.execute(
            select(AuditEvent).where(AuditEvent.action == "route.decided")
            .order_by(AuditEvent.seq.asc())
        ).scalars()
    )


@pytest.fixture(autouse=True)
def _reset_route_runtime():
    """Keep the module-level runtime wiring from leaking between tests."""
    graph.configure_route_runtime(budget=None, auditor=None, actor=None)
    yield
    graph.configure_route_runtime(budget=None, auditor=None, actor=None)


# ---------------------------------------------------------------------------
# 0. The table itself
# ---------------------------------------------------------------------------

def test_criteria_is_ordered_module_constant():
    assert [c.name for c in ROUTE_CRITERIA] == [
        "empathetic_single_agent",
        "source_research",
        "authorized_tool_step",
        "expert_delegation",
    ]
    assert [c.route for c in ROUTE_CRITERIA] == [
        "single_agent", "research", "tool_step", "delegate",
    ]
    assert all(c.description for c in ROUTE_CRITERIA)
    # Only the two high-risk routes carry guards.
    guarded = {c.name for c in ROUTE_CRITERIA if c.guard is not None}
    assert guarded == {"authorized_tool_step", "expert_delegation"}


def test_route_decision_signature_still_compatible():
    assert route_decision({}) == "single_agent"
    assert route_decision({"route": "research"}) == "research"


# ---------------------------------------------------------------------------
# 1. empathetic_single_agent  (positive + negative)
# ---------------------------------------------------------------------------

def test_c1_positive_english_listen():
    d = _decision(goal="please just listen to me for a moment")
    assert (d.route, d.criterion) == ("single_agent", "empathetic_single_agent")


def test_c1_positive_chinese_陪伴():
    d = _decision(goal="我今天很累，只想要有人陪伴一下")
    assert (d.route, d.criterion) == ("single_agent", "empathetic_single_agent")


def test_c1_negative_pure_chat_falls_to_fallback():
    d = _decision(goal="summarize today's meeting notes")
    assert d.criterion == "default_single_agent"
    assert d.criterion != "empathetic_single_agent"


def test_c1_negative_research_goal_does_not_match_c1():
    d = _decision(goal="research recent papers on alignment")
    assert d.criterion != "empathetic_single_agent"


# ---------------------------------------------------------------------------
# 2. source_research  (positive + negative)
# ---------------------------------------------------------------------------

def test_c2_positive_research():
    d = _decision(goal="Research recent publications on attention")
    assert (d.route, d.criterion) == ("research", "source_research")


def test_c2_positive_chinese_调查():
    d = _decision(goal="帮我调查一下这个来源是否可靠")
    assert (d.route, d.criterion) == ("research", "source_research")


def test_c2_negative_listening_goal_is_not_research():
    d = _decision(goal="I feel anxious, please listen")
    assert d.criterion != "source_research"
    assert d.route == "single_agent"


# ---------------------------------------------------------------------------
# 3. authorized_tool_step  (positive + negative, budget guarded)
# ---------------------------------------------------------------------------

def test_c3_positive_with_known_price_and_budget(session, audit):
    _make_task(session)
    bs = _budget(session, audit)
    actor = Actor.owner(OWNER)
    d = evaluate_route(
        {"goal": "use the tool to run the aggregation", "task_id": "t-route",
         "tool_price_status": "known", "estimated_cost_usd": "0.02"},
        budget=bs, auditor=audit, actor=actor,
    )
    assert (d.route, d.criterion) == ("tool_step", "authorized_tool_step")
    assert d.reservation_id  # an atomic reservation was made
    row = session.get(BudgetReservation, d.reservation_id)
    assert row is not None and str(row.amount) == "0.020000"


def test_c3_negative_price_unknown_is_not_allowed(session, audit):
    actor = Actor.owner(OWNER)
    d = evaluate_route(
        {"goal": "run the tool now", "task_id": "t-route",
         "tool_price_status": "unknown", "estimated_cost_usd": "0.0"},
        auditor=audit, actor=actor,
    )
    assert d.route != "tool_step"
    rejection = _route_events(session)[-1].details["rejections"][0]
    assert rejection["criterion"] == "authorized_tool_step"
    assert "price_unknown" in rejection["reason"]


def test_c3_negative_missing_price_is_not_zero_cost(session, audit):
    _make_task(session)
    bs = _budget(session, audit)
    actor = Actor.owner(OWNER)
    # price status known but no amount -> must not be waved through as zero.
    d = evaluate_route(
        {"goal": "run the tool", "task_id": "t-route", "tool_price_status": "known"},
        budget=bs, auditor=audit, actor=actor,
    )
    assert d.route != "tool_step"
    events = _route_events(session)
    assert events and any(
        r["criterion"] == "authorized_tool_step" for r in events[-1].details["rejections"]
    )


def test_c3_negative_budget_insufficient_is_not_allowed(session, audit):
    _make_task(session)
    bs = _budget(session, audit, per_task="0.001")
    actor = Actor.owner(OWNER)
    d = evaluate_route(
        {"goal": "execute the tool", "task_id": "t-route",
         "tool_price_status": "known", "estimated_cost_usd": "0.5"},
        budget=bs, auditor=audit, actor=actor,
    )
    assert d.route != "tool_step"
    events = _route_events(session)
    rejection = events[-1].details["rejections"][0]
    assert rejection["criterion"] == "authorized_tool_step"
    assert "budget_insufficient" in rejection["reason"]


def test_c3_negative_without_budget_service_fails_closed():
    d = _decision(goal="run the tool", task_id="t-route",
                  tool_price_status="known", estimated_cost_usd="0.01")
    assert d.route != "tool_step"


def test_c3_known_zero_price_is_allowed_without_reservation(session, audit):
    _make_task(session)
    bs = _budget(session, audit)
    actor = Actor.owner(OWNER)
    d = evaluate_route(
        {"goal": "run the local echo tool", "task_id": "t-route",
         "tool_price_status": "known", "estimated_cost_usd": "0.0"},
        budget=bs, auditor=audit, actor=actor,
    )
    assert d.route == "tool_step"
    assert d.reservation_id is None  # nothing to reserve for a known-free tool


# ---------------------------------------------------------------------------
# 4. expert_delegation  (positive + negative, cycle guarded)
# ---------------------------------------------------------------------------

def test_c4_positive_delegate_without_graph():
    d = _decision(goal="delegate this to a specialist expert")
    assert (d.route, d.criterion) == ("delegate", "expert_delegation")


def test_c4_positive_acyclic_graph_allowed():
    d = _decision(
        goal="委派专家处理",
        delegation_graph=[
            {"role": "coordinator", "depends_on": []},
            {"role": "reviewer", "depends_on": ["coordinator"]},
        ],
    )
    assert d.route == "delegate"


def test_c4_negative_cycle_is_rejected(session, audit):
    actor = Actor.owner(OWNER)
    d = evaluate_route(
        {"goal": "delegate to experts", "task_id": "t-route",
         "delegation_graph": [
             {"role": "a", "depends_on": ["b"]},
             {"role": "b", "depends_on": ["a"]},
         ]},
        auditor=audit, actor=actor,
    )
    assert d.route != "delegate"
    events = _route_events(session)
    assert "delegation_cycle" in events[-1].details["rejections"][0]["reason"]


def test_c4_negative_unknown_dependency_is_rejected():
    d = _decision(
        goal="delegate to experts",
        delegation_graph=[{"role": "a", "depends_on": ["ghost"]}],
    )
    assert d.route != "delegate"


def test_c4_negative_no_delegate_keyword():
    d = _decision(goal="write a short poem about the sea")
    assert d.criterion != "expert_delegation"


# ---------------------------------------------------------------------------
# 5. ORDER SENSITIVITY (the core of ADR-07) + mutation
# ---------------------------------------------------------------------------

def test_order_listen_beats_research():
    d = _decision(goal="listen to me and research the sources")
    assert (d.route, d.criterion) == ("single_agent", "empathetic_single_agent")


def test_order_research_beats_tool():
    d = _decision(goal="research the sources then run the tool")
    assert (d.route, d.criterion) == ("research", "source_research")


def test_order_tool_beats_delegate(session, audit):
    _make_task(session)
    bs = _budget(session, audit)
    actor = Actor.owner(OWNER)
    d = evaluate_route(
        {"goal": "run the tool and delegate to an expert", "task_id": "t-route",
         "tool_price_status": "known", "estimated_cost_usd": "0.01"},
        budget=bs, auditor=audit, actor=actor,
    )
    assert (d.route, d.criterion) == ("tool_step", "authorized_tool_step")


def test_mutation_reordering_table_flips_order_sensitive_route(monkeypatch):
    """If the table order is perturbed, the order-sensitive assertion must flip.

    This is the mutation guard: it proves ``test_order_listen_beats_research``
    is actually testing *order*, not an accident of keyword sets.
    """
    state = {"goal": "listen to me and research the sources"}
    assert evaluate_route(state).route == "single_agent"

    monkeypatch.setattr(graph, "ROUTE_CRITERIA", tuple(reversed(ROUTE_CRITERIA)))
    assert evaluate_route(state).route == "research"


# ---------------------------------------------------------------------------
# 6. audit records the matched criterion name
# ---------------------------------------------------------------------------

def test_audit_records_matched_criterion_name(session, audit):
    actor = Actor.owner(OWNER)
    d = evaluate_route({"goal": "research this topic", "task_id": "t-audit"},
                       auditor=audit, actor=actor)
    events = _route_events(session)
    assert len(events) == 1
    details = events[-1].details
    assert details["criterion"] == "source_research"
    assert details["route"] == d.route
    assert details["contains_private_text"] is False


def test_audit_records_fallback_criterion_name(session, audit):
    actor = Actor.owner(OWNER)
    evaluate_route({"goal": "just say hello", "task_id": "t-audit"},
                   auditor=audit, actor=actor)
    details = _route_events(session)[-1].details
    assert details["criterion"] == FALLBACK_CRITERION.name


def test_audit_records_explicit_override_criterion(session, audit):
    actor = Actor.owner(OWNER)
    evaluate_route({"goal": "run tool", "route": "tool_step", "task_id": "t-audit"},
                   auditor=audit, actor=actor)
    details = _route_events(session)[-1].details
    assert details["criterion"] == "explicit_override"


def test_audit_records_guard_rejections(session, audit):
    actor = Actor.owner(OWNER)
    evaluate_route(
        {"goal": "run the tool", "task_id": "t-audit", "tool_price_status": "unknown"},
        auditor=audit, actor=actor,
    )
    details = _route_events(session)[-1].details
    assert details["rejections"][0]["criterion"] == "authorized_tool_step"
    assert "price_unknown" in details["rejections"][0]["reason"]


# ---------------------------------------------------------------------------
# 7. model cannot bypass the table
# ---------------------------------------------------------------------------

def test_model_suggested_route_cannot_bypass_criteria(session, audit):
    actor = Actor.owner(OWNER)
    d = evaluate_route(
        {"goal": "listen to me", "task_id": "t-model", "model_suggested_route": "tool_step"},
        auditor=audit, actor=actor,
    )
    assert d.route == "single_agent"
    details = _route_events(session)[-1].details
    assert details["model_route_ignored"] is True
    assert details["model_suggested_route"] == "tool_step"
    assert details["criterion"] == "empathetic_single_agent"


# ---------------------------------------------------------------------------
# 8. explicit override keeps callers/tests compatible
# ---------------------------------------------------------------------------

def test_explicit_override_skips_guards():
    # Explicit tool_step with no budget wired: the override is honored (this is
    # the path existing F3 tests take), and it is audited as an override.
    d = evaluate_route({"goal": "use tool", "route": "tool_step"})
    assert (d.route, d.criterion) == ("tool_step", "explicit_override")


# ---------------------------------------------------------------------------
# 9. node_planning surfaces the criterion and stage
# ---------------------------------------------------------------------------

def test_node_planning_returns_criterion_and_stage():
    update = node_planning({"goal": "research recent papers"})
    assert update["route"] == "research"
    assert update["route_criterion"] == "source_research"
    assert update["stage"] == "exec_research"


def test_node_planning_fallback_route_and_stage():
    update = node_planning({"goal": "nothing special here"})
    assert update["route"] == "single_agent"
    assert update["route_criterion"] == "default_single_agent"
    assert update["stage"] == "exec_single_agent"


def test_node_planning_records_reservation_id(session, audit):
    _make_task(session)
    bs = _budget(session, audit)
    graph.configure_route_runtime(budget=bs, auditor=audit, actor=Actor.owner(OWNER))
    update = node_planning({
        "goal": "run the tool", "task_id": "t-route",
        "tool_price_status": "known", "estimated_cost_usd": "0.01",
    })
    assert update["route"] == "tool_step"
    assert update["tool_reservation_id"]
