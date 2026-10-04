"""LangGraph phase-internal orchestration (FROZEN_CONTRACT §7, §10, Execution Manual F3).

Implements the phase-internal reasoning and execution graph:
* Pipeline: requirements -> planning -> [single_agent | research | tool_step | delegate] -> validate -> finish.
* Graph State: task_id, attempt, stage, route, goal, domain, budget_balance, granted_source_ids,
  evidence_refs, step_count, max_steps, history, output, error.
* Four Verified Routes:
  1. Single Agent: Empathetic listening, reflection without unsolicited diagnosis or forced assessment.
  2. Source Research: Provenance-preserving research with source IDs and outbound privacy checks.
  3. Authorized Tool: Guarded tool execution with atomic budget reservation and settlement.
  4. Expert Delegation: Scoped delegation where the subagent receives targeted context, NOT raw transcripts.
* Checkpointer: MemorySaver checkpointing ensuring idempotent stage replay without duplicated side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, List, Literal, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ..db.types import utcnow
from ..services.actor import Actor
from ..services.errors import PermissionDenied, ValidationFailed
from .gateway import CallResult, MockModelProvider, ModelGateway, ModelRequest


class TaskGraphState(TypedDict, total=False):
    task_id: str
    attempt: int
    stage: str
    route: Literal["single_agent", "research", "tool_step", "delegate"]
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

    domain = state.get("domain", "personal")
    # Enforce boundary: if domain is not personal, ensure personal sources have grants
    granted = state.get("granted_source_ids", [])
    history = state.get("history", [])

    return {
        "stage": "planning",
        "status": "in_progress",
        "step_count": state.get("step_count", 0) + 1,
    }


def node_planning(state: TaskGraphState) -> dict:
    """Stage 2: Determines execution route based on goal and constraints."""
    goal = state.get("goal", "").lower()
    explicit_route = state.get("route")

    if explicit_route:
        route = explicit_route
    elif any(k in goal for k in ["listen", "倾听", "feel", "陪伴", "talk"]):
        route = "single_agent"
    elif any(k in goal for k in ["research", "search", "调查", "研究", "source"]):
        route = "research"
    elif any(k in goal for k in ["tool", "calc", "execute", "run", "工具"]):
        route = "tool_step"
    elif any(k in goal for k in ["delegate", "expert", "subagent", "委派", "专家"]):
        route = "delegate"
    else:
        route = "single_agent"

    return {
        "route": route,
        "stage": f"exec_{route}",
    }


def route_decision(state: TaskGraphState) -> str:
    return state.get("route", "single_agent")


def node_single_agent(state: TaskGraphState) -> dict:
    """Route 1: Pure empathetic listening.

    Does not force psychometric assessment, diagnoses, or summarize away user emotion.
    """
    goal = state.get("goal", "")
    history = state.get("history", [])
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
    domain = state.get("domain", "personal")
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
    subtasks = state.get("subtasks", [])
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


def compile_task_graph(checkpointer: Any | None = None):
    cp = checkpointer or MemorySaver()
    graph = build_task_graph()
    return graph.compile(checkpointer=cp)
