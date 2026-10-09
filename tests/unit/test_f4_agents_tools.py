"""F4 Comprehensive Verification: Dynamic Agents & Tool Interoperability.

Tests:
1. Outbound A2A Client discovery, task submission, status polling, cancellation.
2. Server-side trusted endpoint registry rejecting arbitrary/untrusted URLs.
3. Draining agents rejecting new tasks while preserving in-flight tasks.
4. Scoped service credentials bounded by task, domains, tools, budget, and deadline.
5. Full agent lifecycle (register -> health -> enable -> lease -> drain -> revoke)
   with immediate credential revocation.
6. MCP client & real stdio subprocess server (initialize, tools/list, tools/call,
   permission gate denial, cancellation).
7. Parallel/hierarchical delegation guards: max depth, concurrency, budget, and bounded retries.
"""

from __future__ import annotations

import hashlib
import sys

import pytest

from find_yourself.adapters.a2a import (
    A2AClient,
    A2ARpcError,
    TrustedEndpointConfig,
    TrustedEndpointRegistry,
    UntrustedEndpointError,
)
from find_yourself.adapters.mcp import (
    McpClient,
    McpError,
    McpStdioServer,
    McpTool,
)
from find_yourself.adapters.specialized_agents import (
    EngineeringAgentService,
    ResearchAgentService,
    create_agent_app,
)
from find_yourself.runtime.delegation import (
    BudgetExhausted,
    DelegationCoordinator,
    MaxConcurrencyExceeded,
    MaxDepthExceeded,
    SubtaskFailed,
)
from find_yourself.services.agent import AgentService
from find_yourself.services.audit import AuditService
from find_yourself.services.auth import AuthService
from find_yourself.services.errors import Conflict, PermissionDenied, Unauthenticated

# ============================================================================
# 1. Outbound A2A Client & Specialized Agents
# ============================================================================

def test_a2a_outbound_client_discovery_and_tasks():
    research_svc = ResearchAgentService(version="1.0.0")
    research_app = create_agent_app(research_svc)

    registry = TrustedEndpointRegistry([
        TrustedEndpointConfig("researcher", "http://research-agent.local", "ResearchAgent"),
    ])
    client = A2AClient(registry=registry, app=research_app, default_base_url="http://research-agent.local")

    # Discover Agent Card
    card = client.get_agent_card("researcher")
    assert card["name"] == "ResearchAgent"
    assert card["version"] == "1.0.0"
    assert card["protocolVersion"] == "0.3.0"
    assert len(card["skills"]) >= 2

    # Submit task via message/send
    task = client.send_message(
        "researcher",
        {"role": "user", "parts": [{"type": "text", "text": "quantum computing advances"}]},
        task_id="task-r1",
    )
    assert task["id"] == "task-r1"
    assert task["status"]["state"] == "completed"
    assert "findings" in task["result"]
    assert len(task["result"]["citations"]) >= 1

    # Poll task via tasks/get
    polled = client.get_task("researcher", "task-r1")
    assert polled["id"] == "task-r1"
    assert polled["status"]["state"] == "completed"

    # Cancel task via tasks/cancel
    cancel_res = client.cancel_task("researcher", "task-r1")
    assert cancel_res["status"]["state"] == "canceled"


def test_engineering_agent_analysis():
    eng_svc = EngineeringAgentService(version="1.0.0")
    eng_app = create_agent_app(eng_svc)

    registry = TrustedEndpointRegistry([
        TrustedEndpointConfig("engineer", "http://eng-agent.local", "EngineeringAgent"),
    ])
    client = A2AClient(registry=registry, app=eng_app, default_base_url="http://eng-agent.local")

    card = client.get_agent_card("engineer")
    assert card["name"] == "EngineeringAgent"

    task = client.send_message(
        "engineer",
        {"role": "user", "parts": [{"type": "text", "text": "def add(a, b): return a + b"}]},
        task_id="eng-001",
    )
    assert task["status"]["state"] == "completed"
    assert task["result"]["exit_code"] == 0
    assert "ast_parse" in task["result"]["checks_passed"]


def test_a2a_untrusted_endpoint_rejected():
    registry = TrustedEndpointRegistry([
        TrustedEndpointConfig("known_agent", "http://known.local"),
    ])
    client = A2AClient(registry=registry)

    # Attempt to target untrusted URL or endpoint key
    with pytest.raises(UntrustedEndpointError):
        client.get_agent_card("http://evil-external-target.com")

    with pytest.raises(UntrustedEndpointError):
        client.send_message(
            "unknown_key",
            {"role": "user", "parts": [{"type": "text", "text": "ping"}]},
        )


def test_a2a_draining_agent_rejects_new_work_keeps_existing():
    svc = ResearchAgentService(version="1.0.0")
    app = create_agent_app(svc)
    registry = TrustedEndpointRegistry([
        TrustedEndpointConfig("res", "http://res.local"),
    ])
    client = A2AClient(registry=registry, app=app, default_base_url="http://res.local")

    # Complete task 1
    t1 = client.send_message("res", {"role": "user", "parts": [{"type": "text", "text": "first"}]}, task_id="t1")
    assert t1["status"]["state"] == "completed"

    # Set draining
    svc.draining = True

    # New task rejected with ERR_AGENT_DRAINING (-32002)
    with pytest.raises(A2ARpcError) as exc_info:
        client.send_message("res", {"role": "user", "parts": [{"type": "text", "text": "second"}]})
    assert exc_info.value.code == -32002

    # Existing task can still be queried
    polled = client.get_task("res", "t1")
    assert polled["id"] == "t1"


# ============================================================================
# 2. Scoped Service Credentials Bounded by Task, Domain, Tools, Budget, Expiry
# ============================================================================

def test_scoped_service_credential_boundaries(session, owner):
    audit = AuditService(session)
    auth = AuthService(session, audit)

    # Owner issues credential bounded to task, domain "work", tools ["search", "summarize"]
    ident, token = auth.issue_scoped_service_credential(
        owner,
        name="specialist-worker",
        kind="agent",
        task_id="task-work-99",
        domains=["work"],
        tools=["search", "summarize"],
        budget_cents=100,
        ttl_seconds=3600,
    )
    assert token

    # Authenticate service actor
    actor = auth.service_actor(token)
    assert actor.subject_type == "service"
    assert actor.service_id == ident.id

    # Domain checks
    assert actor.can_access_domain("work") is True
    assert actor.can_access_domain("personal") is False
    with pytest.raises(PermissionDenied) as exc:
        actor.require_domain("personal")
    assert exc.value.code == "domain_forbidden"

    # Tool checks
    assert actor.can_use_tool("search") is True
    assert actor.can_use_tool("summarize") is True
    assert actor.can_use_tool("destructive_wipe") is False
    with pytest.raises(PermissionDenied) as exc:
        actor.require_tool("destructive_wipe")
    assert exc.value.code == "tool_forbidden"

    # Task checks
    actor.require_task("task-work-99")  # should not raise
    with pytest.raises(PermissionDenied) as exc:
        actor.require_task("task-other")
    assert exc.value.code == "task_mismatch"


def test_expired_service_credential_rejected(session, owner):
    audit = AuditService(session)
    auth = AuthService(session, audit)

    # Issue credential with negative TTL (already expired)
    ident, token = auth.issue_scoped_service_credential(
        owner,
        name="temp-agent",
        ttl_seconds=-10,
    )

    with pytest.raises(Unauthenticated) as exc:
        auth.service_actor(token)
    assert exc.value.code == "service_expired"


# ============================================================================
# 3. Full Agent Lifecycle and Immediate Credential Revocation
# ============================================================================

def test_agent_lifecycle_and_credential_revocation(session, owner):
    audit = AuditService(session)
    auth = AuthService(session, audit)
    agents = AgentService(session, audit)

    # 1. Register
    a = agents.register(
        owner,
        name="ExplorerService",
        semantic_version="1.0.0",
        capabilities=["crawl"],
        domains=["work"],
        endpoint_key="ep-explorer",
        max_concurrency=2,
    )
    assert a.state == "registered"

    # Cannot enable until healthy
    with pytest.raises(Conflict):
        agents.enable(owner, a.id)

    # 2. Health & Enable
    agents.set_health(owner, a.id, healthy=True)
    agents.enable(owner, a.id)
    assert a.state == "enabled"

    # 3. Issue Service Credential
    ident, token = auth.issue_scoped_service_credential(
        owner,
        name="ExplorerService",
        task_id="task-live-1",
        domains=["work"],
        tools=["crawl"],
    )
    actor = auth.service_actor(token)
    assert actor.subject_type == "service"

    # 4. Acquire lease on v1.0.0
    lease1 = agents.acquire_lease(owner, a.id, "task-live-1")
    assert lease1.state == "active"

    # 5. Upgrade: register v2.0.0
    a2 = agents.register(
        owner,
        name="ExplorerService",
        semantic_version="2.0.0",
        capabilities=["crawl", "deep_crawl"],
        domains=["work"],
        endpoint_key="ep-explorer-v2",
        max_concurrency=2,
    )
    agents.set_health(owner, a2.id, healthy=True)
    agents.enable(owner, a2.id)

    # In-flight lease1 remains active and attached to v1.0.0
    assert session.get(type(lease1), lease1.id).state == "active"

    # 6. Drain v1.0.0
    agents.drain(owner, a.id)
    assert a.state == "draining"
    # Draining rejects new leases
    with pytest.raises(Conflict):
        agents.acquire_lease(owner, a.id, "task-new")

    # 7. Revoke v1.0.0
    agents.revoke(owner, a.id)
    assert a.state == "revoked"
    assert session.get(type(lease1), lease1.id).state == "revoked"

    # Credentials are now completely invalid and rejected
    with pytest.raises(Unauthenticated) as exc:
        auth.service_actor(token)
    assert exc.value.code == "bad_service_token"


# ============================================================================
# 4. MCP Protocol & Real Subprocess Execution
# ============================================================================

def test_mcp_client_in_process():
    tools = [
        McpTool(name="calc", description="Basic math", handler=lambda a: {"ans": a.get("x", 0) * 2}),
        McpTool(name="admin_secret", description="Admin only", privileged=True, handler=lambda a: {"secret": 42}),
    ]
    server = McpStdioServer(tools=tools, allowed_privileged=set())
    client = McpClient.from_server(server)

    # Initialize
    init_res = client.initialize()
    assert init_res["protocolVersion"] == "2024-11-05"

    # Tools list
    tool_list = client.list_tools()
    names = [t["name"] for t in tool_list]
    assert "calc" in names
    assert "admin_secret" in names

    # Unprivileged tool call
    res = client.call_tool("calc", {"x": 21})
    assert res == {"ans": 42}

    # Privileged tool call without permission -> permission denied (-32003)
    with pytest.raises(McpError) as exc_info:
        client.call_tool("admin_secret", {})
    assert exc_info.value.code == -32003

    # Cancellation
    server.cancelled.add("99")
    with pytest.raises(McpError) as exc_info:
        client.call_tool("calc", {"x": 1}, request_id="99")
    assert exc_info.value.code == -32000


def test_mcp_client_real_stdio_subprocess():
    """Verify real OS subprocess communication over stdio with python -m find_yourself.adapters.mcp."""
    cmd = [sys.executable, "-m", "find_yourself.adapters.mcp"]
    client = McpClient.from_subprocess(cmd)
    try:
        # Initialize handshake
        init_res = client.initialize()
        assert init_res["protocolVersion"] == "2024-11-05"
        assert init_res["serverInfo"]["name"] == "find-yourself-mcp"

        # List tools
        tools = client.list_tools()
        names = [t["name"] for t in tools]
        assert "ping" in names
        assert "sha256" in names
        assert "system_audit" in names

        # Execute unprivileged tool 'ping'
        ping_res = client.call_tool("ping", {})
        assert ping_res == {"pong": True}

        # Execute unprivileged tool 'sha256'
        sha_res = client.call_tool("sha256", {"text": "mcp_interop_2026"})
        expected_hash = hashlib.sha256(b"mcp_interop_2026").hexdigest()
        assert sha_res["hash"] == expected_hash

        # Execute privileged tool 'system_audit' without scope -> rejected
        with pytest.raises(McpError) as exc_info:
            client.call_tool("system_audit", {})
        assert exc_info.value.code == -32003
    finally:
        client.close()


# ============================================================================
# 5. Parallel & Hierarchical Delegation Guards
# ============================================================================

def test_delegation_coordinator_guards():
    coordinator = DelegationCoordinator(
        max_depth=3,
        max_concurrency=2,
        max_retries=2,
        root_budget_usd=0.02,
    )

    # Successful subtask within bounds
    res = coordinator.dispatch(
        parent_task_id="root-1",
        subtask_id="sub-1",
        current_depth=0,
        runner_fn=lambda: {"done": True},
        cost_usd=0.005,
    )
    assert res["status"] == "completed"
    assert coordinator.root_spent_usd == 0.005
    assert coordinator.remaining_budget_usd == 0.015

    # Depth limit violation (depth 3 >= max_depth 3)
    with pytest.raises(MaxDepthExceeded):
        coordinator.dispatch(
            parent_task_id="sub-3",
            subtask_id="sub-4",
            current_depth=3,
            runner_fn=lambda: {"done": True},
            cost_usd=0.001,
        )

    # Concurrency limit violation
    coordinator._active_subtasks["dummy-1"] = {}
    coordinator._active_subtasks["dummy-2"] = {}
    with pytest.raises(MaxConcurrencyExceeded):
        coordinator.dispatch(
            parent_task_id="root-1",
            subtask_id="sub-concurrent",
            current_depth=1,
            runner_fn=lambda: {"done": True},
            cost_usd=0.001,
        )
    coordinator._active_subtasks.clear()

    # Bounded retries and failure propagation
    fail_attempts = 0

    def always_fails():
        nonlocal fail_attempts
        fail_attempts += 1
        raise ValueError("Simulated external agent failure")

    with pytest.raises(SubtaskFailed) as exc_info:
        coordinator.dispatch(
            parent_task_id="root-1",
            subtask_id="sub-faulty",
            current_depth=1,
            runner_fn=always_fails,
            cost_usd=0.005,
        )
    assert fail_attempts == 2  # Bounded: retried up to max_retries=2 and stopped
    assert "ev:subtask:sub-faulty:failed_after_2_attempts" in exc_info.value.evidence_ref

    # Budget exhaustion
    with pytest.raises(BudgetExhausted):
        coordinator.dispatch(
            parent_task_id="root-1",
            subtask_id="sub-expensive",
            current_depth=1,
            runner_fn=lambda: {"done": True},
            cost_usd=0.05,  # Exceeds remaining budget (0.015)
        )
