"""Tests for F3: LangGraph 4 routes, local agent harness isolation, and PostgresCorePorts guards."""

from decimal import Decimal

import pytest
from langgraph.checkpoint.memory import MemorySaver

from find_yourself.db.models import Grant
from find_yourself.db.types import utcnow
from find_yourself.runtime.gateway import (
    MockModelProvider,
    ModelGateway,
    ModelRequest,
    PriceUnknown,
)
from find_yourself.runtime.graph import compile_task_graph
from find_yourself.runtime.local_agents import LocalAgentsConfig, LocalAgentsHarness
from find_yourself.services.actor import Actor
from find_yourself.services.errors import PermissionDenied
from find_yourself.services.pg_ports import PostgresCorePorts

# ---------------------------------------------------------------------------
# 1. LangGraph: 4 Verified Routes
#
# 诚实契约（FY-A-1 之后）：路由节点的输出来自**真实** ``ModelGateway.complete``
# 调用，费用取真实 ``CallResult.settled_amount``。没有可服务的模型时节点**诚实
# 失败**——不返回模板 canned 文本、不伪造工具执行回执、不记账任何 spend。
# 因此这里统一注入 ``MockModelProvider`` 驱动的真实网关验证成功路径，并另设
# 「网关缺失」用例验证诚实失败路径。
# ---------------------------------------------------------------------------

#: 成功路径统一断言「文本逐字来自 provider」，以此证明输出不是模板捏造。
PROVIDER_TEXT = "PROVIDER-GROUNDED-TEXT"


def _mock_gateway(response: str = PROVIDER_TEXT) -> tuple[ModelGateway, MockModelProvider]:
    provider = MockModelProvider(default_response=response)
    # 未传 settings -> ``_runtime_model_name`` 回落 "gpt-4o-mini"（在价目表内），
    # 因此 complete() 会按真实 usage 结算出一个非零 settled_amount。
    return ModelGateway(provider=provider), provider


def test_langgraph_route_1_single_agent_empathetic_listening():
    """Route 1: Pure empathetic listening.

    Does not force psychological diagnosis, assessment, or summarize away user emotion.
    The reflection must be the provider's real text, and the cost the real settlement.
    """
    gw, provider = _mock_gateway()
    app = compile_task_graph(checkpointer=MemorySaver(), gateway=gw)
    initial_state = {
        "task_id": "task-listen-01",
        "attempt": 1,
        "goal": "I feel very overwhelmed by work and need someone to listen",
        "route": "single_agent",
        "domain": "personal",
        "budget_balance": 1.0,
        "max_steps": 5,
        "history": [{"role": "user", "content": "I feel overwhelmed"}],
    }
    result = app.invoke(initial_state, config={"configurable": {"thread_id": "thread-01"}})
    assert result["status"] == "completed"
    out = result["output"]
    assert out["mode"] == "listening"
    assert out["forced_assessment"] is False
    # 文本逐字来自 provider（而非任何内置模板），并带真实 provider 回执。
    assert out["reflection"] == PROVIDER_TEXT
    assert out["model_backed"] is True
    assert out["provider_request_id"].startswith("mock-req-")
    assert provider.last_call["prompt"]  # 确实发生过一次真实调用
    assert any(ev.startswith("ev:model:") for ev in result["evidence_refs"])
    # 费用是真实结算值（按实际 usage 计价），不是硬编码常数。
    assert result["spent_usd"] > 0.0
    assert result["budget_balance"] == pytest.approx(1.0 - result["spent_usd"])


def test_langgraph_route_1_without_model_fails_honestly():
    """Route 1 degradation: no serving model -> honest failure, no canned text, no spend.

    FY-A-1 红线用例：网关缺失时**不得**退回模板共情话术（旧实现硬编码
    "I hear you completely"），也不得记账任何费用。
    """
    import find_yourself.runtime.graph as graph_mod

    original = graph_mod._default_gateway
    graph_mod._default_gateway = lambda: None  # 模拟宿主未注入任何可用网关
    try:
        app = compile_task_graph(checkpointer=MemorySaver())
    finally:
        graph_mod._default_gateway = original

    result = app.invoke(
        {
            "task_id": "task-listen-nomodel",
            "attempt": 1,
            "goal": "I feel very overwhelmed by work and need someone to listen",
            "route": "single_agent",
            "domain": "personal",
            "budget_balance": 1.0,
            "max_steps": 5,
        },
        config={"configurable": {"thread_id": "thread-01-nomodel"}},
    )
    out = result["output"]
    assert out["mode"] == "listening"
    assert out["model_backed"] is False
    assert out["error"] == "model_not_configured"
    # 诚实失败的直接体现：没有捏造的 reflection、没有伪造证据、没有计费。
    assert "reflection" not in out
    assert result.get("evidence_refs", []) == []
    assert out["final_spent_usd"] == 0.0
    assert result["budget_balance"] == 1.0


def test_langgraph_route_2_source_research():
    """Route 2: Provenance-preserving source research with citation tracking.

    Citations may only come from granted source ids; findings must be the
    provider's real text.
    """
    gw, _ = _mock_gateway()
    app = compile_task_graph(checkpointer=MemorySaver(), gateway=gw)
    initial_state = {
        "task_id": "task-research-01",
        "attempt": 1,
        "goal": "Research recent publications on attention mechanisms",
        "route": "research",
        "domain": "work",
        "granted_source_ids": ["paper_2026_01", "paper_2026_02"],
        "budget_balance": 1.0,
        "max_steps": 5,
    }
    result = app.invoke(initial_state, config={"configurable": {"thread_id": "thread-02"}})
    assert result["status"] == "completed"
    out = result["output"]
    assert out["mode"] == "research"
    assert out["citations"] == ["src:paper_2026_01", "src:paper_2026_02"]
    assert out["verified"] is True
    assert out["findings"] == PROVIDER_TEXT
    assert out["model_backed"] is True
    assert "src:paper_2026_01" in result["evidence_refs"]


def test_langgraph_route_2_without_grants_is_unverified():
    """Route 2 degradation: nothing granted -> zero citations, unverified, never invented ones."""
    gw, _ = _mock_gateway()
    app = compile_task_graph(checkpointer=MemorySaver(), gateway=gw)
    result = app.invoke(
        {
            "task_id": "task-research-nogrant",
            "attempt": 1,
            "goal": "Research recent publications on attention mechanisms",
            "route": "research",
            "domain": "work",
            "granted_source_ids": [],
            "budget_balance": 1.0,
            "max_steps": 5,
        },
        config={"configurable": {"thread_id": "thread-02-nogrant"}},
    )
    out = result["output"]
    assert out["mode"] == "research"
    assert out["citations"] == []
    assert out["verified"] is False
    # 绝不出现旧实现伪造的公共语料来源。
    assert not any("public_corpus_2026" in ev for ev in result["evidence_refs"])


def test_langgraph_route_3_authorized_tool_step():
    """Route 3: Guarded tool slot.

    No tool executor is wired into this node, so it must report ``not_executed``
    rather than fabricate an execution receipt（旧用例断言的是一个从未真正
    执行过的工具回执）。模型调用只做**计划**，其真实结算费用照实记账。
    """
    gw, _ = _mock_gateway()
    app = compile_task_graph(checkpointer=MemorySaver(), gateway=gw)
    initial_state = {
        "task_id": "task-tool-01",
        "attempt": 1,
        "goal": "Calculate aggregate metrics using tool",
        "route": "tool_step",
        "domain": "work",
        "budget_balance": 0.50,
        "max_steps": 5,
    }
    result = app.invoke(initial_state, config={"configurable": {"thread_id": "thread-03"}})
    assert result["status"] == "completed"
    out = result["output"]
    assert out["mode"] == "tool"
    # 计划的目标工具名可以给出，但执行状态必须诚实。
    assert out["tool_used"] == "data_analysis_tool"
    assert out["status"] == "not_executed"
    assert out["result"] is None
    assert out["planned_step"] == PROVIDER_TEXT
    # 未真实执行 -> 不得产出任何工具证据；费用仍按真实结算值扣减。
    assert result["evidence_refs"] == []
    assert result["spent_usd"] > 0.0
    assert result["budget_balance"] < 0.50


def test_langgraph_route_4_expert_delegation_context_isolation():
    """Route 4: Expert delegation must receive scoped context, NOT raw history."""
    gw, provider = _mock_gateway()
    app = compile_task_graph(checkpointer=MemorySaver(), gateway=gw)
    raw_user_secrets = [{"role": "user", "content": "My private personal secrets and sensitive past"}]
    initial_state = {
        "task_id": "task-delegate-01",
        "attempt": 1,
        "goal": "Consult specialized expert on architecture decisions",
        "route": "delegate",
        "domain": "work",
        "history": raw_user_secrets,
        "granted_source_ids": ["doc_arch_001"],
        "budget_balance": 1.0,
        "max_steps": 5,
    }
    result = app.invoke(initial_state, config={"configurable": {"thread_id": "thread-04"}})
    assert result["status"] == "completed"
    out = result["output"]
    assert out["mode"] == "delegation"
    scoped_ctx = out["scoped_context"]
    # Verify raw history was intentionally withheld from subagent
    assert scoped_ctx["raw_history_provided"] is False
    assert "doc_arch_001" in scoped_ctx["citations"]
    # 隔离必须在**出网提示词**层面成立，而不只是状态里的一个布尔标记。
    assert "My private personal secrets" not in provider.last_call["prompt"]
    assert out["expert_answer"] == PROVIDER_TEXT
    assert out["model_backed"] is True


def test_langgraph_checkpoint_state_persistence():
    """Checkpointer persists state by thread_id for resumable workflows."""
    saver = MemorySaver()
    app = compile_task_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": "resumable-01"}}

    state_1 = {
        "task_id": "task-resumable-01",
        "attempt": 1,
        "goal": "Please listen to me",
        "route": "single_agent",
        "budget_balance": 1.0,
    }
    res_1 = app.invoke(state_1, config=config)
    assert res_1["status"] == "completed"

    # Fetch snapshot from checkpointer
    snapshot = app.get_state(config)
    assert snapshot.values["task_id"] == "task-resumable-01"
    assert snapshot.values["output"]["mode"] == "listening"


# ---------------------------------------------------------------------------
# 2. Deep Agents: Filesystem Sandbox & Task Depth Containment
# ---------------------------------------------------------------------------

def test_local_agents_filesystem_sandbox(tmp_path):
    """Deep Agents harness restricts filesystem operations to isolated artifact sandbox."""
    art_dir = tmp_path / "artifacts"
    harness = LocalAgentsHarness(config=LocalAgentsConfig(artifacts_path=str(art_dir)))

    # Legitimate write and read inside sandbox
    rel_path = harness.write_artifact_file("safe_output.txt", b"Safe data inside sandbox")
    assert rel_path == "safe_output.txt"
    read_data = harness.read_artifact_file("safe_output.txt")
    assert read_data == b"Safe data inside sandbox"

    # Path traversal outside sandbox is strictly blocked
    with pytest.raises(PermissionDenied) as exc_info:
        harness.write_artifact_file("../evil_override.txt", b"malicious host write")
    assert exc_info.value.code == "fs_jail_violation"

    with pytest.raises(PermissionDenied):
        harness.read_artifact_file("../../secret_config.env")


def test_local_agents_max_depth_enforcement(tmp_path):
    """Deep Agents subagents are strictly bounded by max_depth."""
    art_dir = tmp_path / "artifacts"
    harness = LocalAgentsHarness(config=LocalAgentsConfig(artifacts_path=str(art_dir), max_depth=2))
    actor = Actor.owner("owner-1")

    # Depth 1: allowed
    trace_1 = harness.run_subagent_task(actor, parent_task_id="t-root", goal="Subtask 1", depth=1)
    assert trace_1.success is True

    # Depth 2: allowed
    trace_2 = harness.run_subagent_task(actor, parent_task_id="t-root", goal="Subtask 2", depth=2)
    assert trace_2.success is True

    # Depth 3: exceeds max_depth=2 -> fails immediately with error
    trace_3 = harness.run_subagent_task(actor, parent_task_id="t-root", goal="Subtask 3", depth=3)
    assert trace_3.success is False
    assert "max_depth_reached" in trace_3.error


# ---------------------------------------------------------------------------
# 3. PostgresCorePorts: Tool Executor Guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_postgres_core_ports_unconfigured_tool_fails_in_production(session):
    """Production path without configured tool executor must fail, never return status=ok."""
    # When allow_echo=False (production safe default):
    from sqlalchemy.orm import sessionmaker
    sm = sessionmaker(bind=session.bind, expire_on_commit=False, future=True)

    ports_prod = PostgresCorePorts(sm, allow_echo=False)
    res = await ports_prod.run_tool_step("t-01", 1, "unconfigured_tool", {"arg": 1}, "idemp-1", "cp-1")
    assert res["status"] == "failed"
    assert "unconfigured_tool_executor" in res["error"]

    # When tool is explicitly registered:
    def custom_executor(payload: dict) -> dict:
        return {"processed": payload["arg"] * 2}

    ports_custom = PostgresCorePorts(sm, tool_executors={"double": custom_executor}, allow_echo=False)
    res_custom = await ports_custom.run_tool_step("t-01", 1, "double", {"arg": 21}, "idemp-2", "cp-2")
    assert res_custom["status"] == "ok"
    assert res_custom["output"] == {"processed": 42}


# ---------------------------------------------------------------------------
# 4. Model Gateway: Pricing Catalogue & Atomic Budget Settlement
# ---------------------------------------------------------------------------

def test_model_gateway_atomic_budget_settlement(session, audit):
    """Model Gateway reserves budget, executes model, and settles exact token price."""
    from find_yourself.db.models import Task
    from find_yourself.services.budget import BudgetService

    t = Task(id="t-budget-01", owner_id="owner-1", goal="Test", domain="personal", deadline=utcnow(), idempotency_key="k-b1")
    session.add(t)
    session.commit()

    bs = BudgetService(session, audit)
    actor = Actor.owner("owner-1")
    mock_provider = MockModelProvider(default_response="Simulated AI assistant response")

    gw = ModelGateway(budget=bs, provider=mock_provider)

    # 1. Unknown price model raises PriceUnknown
    with pytest.raises(PriceUnknown):
        gw.complete(actor, task_id="t-budget-01", model="unpriced-exotic-model", prompt="Hello")

    # 2. Known model calculates price, reserves, and settles
    # Model: 'mock-deterministic' -> input $0.001/1k, output $0.002/1k
    call_result = gw.complete(actor, task_id="t-budget-01", model="mock-deterministic", prompt="Hello AI")
    assert call_result.text == "Simulated AI assistant response"
    assert call_result.settled_amount > Decimal("0.0")
    assert call_result.usage["total_tokens"] > 0


def test_model_gateway_outbound_privacy_spy():
    """R03: Model Gateway blocks personal sources from entering non-personal domain prompts without grant."""
    gw = ModelGateway(provider=MockModelProvider())
    actor_work = Actor.service("worker-1", kind="worker", domains=["work"])

    # Request in 'work' domain containing personal source IDs with NO grant
    req = ModelRequest(domain="work", prompt="Summarize", personal_source_ids=["pers-secret-001"])
    with pytest.raises(PermissionDenied) as exc_info:
        gw.validate_outbound_privacy(req, grants=[])
    assert exc_info.value.code == "sensitive_domain_leak"

    # Request with active, unexpired grant covering the record ID succeeds
    grant = Grant(
        id="grant-01",
        source_domain="personal",
        consumer_domain="work",
        record_ids=["pers-secret-001"],
        state="active",
        scope_hash="hash01",
        expires_at=utcnow().replace(year=2030),
    )
    # Does not raise
    gw.validate_outbound_privacy(req, grants=[grant])
