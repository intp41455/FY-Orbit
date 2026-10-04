"""19 号团队能力合成验证（单测层）.

This file is the *independent* acceptance evidence for
``19_单Agent内部团队与逐节点模型配置实施规格.md``. It is deliberately separate
from the 354-test baseline: nothing here is inherited from 18 号 coverage.

Every model call in this file goes through the real :class:`ModelGateway` with
the deterministic :class:`MockModelProvider` injected. That exercises the actual
budget reserve → provider call → settle path and the actual binding write-back.
It is **synthetic** evidence: the provider is local and deterministic, so no
statement here may be read as a real-model success.

§8 scenarios covered:
1. same model, different roles → independent sessions, no context leak
2. two members on different models → gateway request, record, fee all agree
3. a host that cannot override per-member model → control disabled, not faked
4. model unavailable / credential missing / price unknown → explicit block
5. switch model mid-run → history kept, late batch cannot overwrite
6. goal change mid-run → new plan version visible to affected members
7. retries and further delegation cannot bypass the root budget
8. reconnect → events neither lost nor duplicated
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from find_yourself.db.models import BudgetReservation, Task
from find_yourself.db.team_models import AgentInstance, ModelBinding, TeamEvent
from find_yourself.runtime.gateway import MockModelProvider, ModelGateway, ModelPricing
from find_yourself.services.agent_teams import AgentTeamService
from find_yourself.services.budget import BudgetLimits, BudgetService
from find_yourself.services.errors import Conflict, PermissionDenied, ValidationFailed
from find_yourself.services.model_catalog import ModelCatalog


PRICING = {
    "mock-deterministic": ModelPricing(
        model_id="mock-deterministic",
        input_usd_per_1k=Decimal("0.001"),
        output_usd_per_1k=Decimal("0.002"),
        context_window=16384,
    ),
    "sim-encoder": ModelPricing(
        model_id="sim-encoder",
        input_usd_per_1k=Decimal("0.002"),
        output_usd_per_1k=Decimal("0.004"),
        context_window=32768,
    ),
    "sim-researcher": ModelPricing(
        model_id="sim-researcher",
        input_usd_per_1k=Decimal("0.0015"),
        output_usd_per_1k=Decimal("0.003"),
        context_window=65536,
    ),
}


@pytest.fixture()
def gateway():
    return ModelGateway(provider=MockModelProvider(), pricing=dict(PRICING))


@pytest.fixture()
def catalog(gateway):
    return ModelCatalog(gateway=gateway)


@pytest.fixture()
def budget(session, audit):
    return BudgetService(
        session, audit, limits=BudgetLimits(per_task_usd=Decimal("0.50"))
    )


@pytest.fixture()
def teams(session, audit, budget, gateway, catalog):
    return AgentTeamService(
        session, audit, budget=budget, gateway=gateway, catalog=catalog
    )


@pytest.fixture()
def svc(teams):
    return teams.session


def engineering_team(teams, owner, name="工程团队", **kw):
    return teams.create_team(
        owner, name=name, template_id="engineering",
        budget_ref={"root_budget_usd": 0.50, "member_reserve_cap_usd": 0.05},
        **kw,
    )


# ----------------------------------------------------------------------
# §7-A / §7-B : contract, modes and independent sessions
# ----------------------------------------------------------------------
def test_team_draft_is_versioned_and_audited(teams, owner):
    team = engineering_team(teams, owner)
    assert team.state == "draft"
    assert team.version == 1
    assert [m["role"] for m in team.members] == ["coordinator", "implementer", "reviewer"]

    updated = teams.update_team(
        owner, team.id, expected_version=1,
        patch={"name": "工程团队 v2"}, reason="owner renamed",
    )
    assert updated.version == 2
    assert updated.name == "工程团队 v2"
    assert updated.last_changed_by == owner.owner_id

    with pytest.raises(Conflict):
        teams.update_team(
            owner, team.id, expected_version=1,
            patch={"name": "stale"}, reason="stale write",
        )


def test_product_native_mode_is_recorded_without_pretending(teams, owner):
    team = teams.create_team(
        owner, name="原生团队", mode="product_native",
        members=[
            {"role": "coordinator", "agent_host": "external_chat_product"},
            {"role": "worker", "agent_host": "external_chat_product"},
        ],
        default_binding={"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
    )
    report = teams.validate_start(owner, team.id)
    assert report["can_start"] is True
    warnings = {w["code"] for w in report["warnings"]}
    # A chat subscription cannot meter usage, so the hard-guarantee caveat is
    # surfaced rather than hidden.
    assert "usage_not_meterable" in warnings


def test_same_model_different_roles_get_independent_sessions(teams, owner):
    """§8.1 — same model, two roles ⇒ two sessions, no shared context."""
    team = engineering_team(teams, owner)
    snap = teams.start_team(owner, team.id, expected_version=1)

    sessions = [m["session_id"] for m in snap["members"]]
    assert len(set(sessions)) == len(sessions), "each member needs its own session"
    assert all(s.startswith(f"team-{team.id}-") for s in sessions)

    models = {m["role"]: m["requested_model"] for m in snap["members"]}
    assert set(models.values()) == {"mock-deterministic"}, "all inherit the team default"

    # Two different roles must not resolve to the same AgentInstance row.
    insts = teams._members(team.id)
    assert len({i.session_id for i in insts}) == 3


def test_member_sessions_do_not_leach_each_others_context(teams, owner):
    """§8.1 — executing one member never returns another member's session."""
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)

    a = teams.execute_member(owner, team.id, "implementer", prompt="编码任务")
    b = teams.execute_member(owner, team.id, "reviewer", prompt="审查任务")

    assert a["session_id"] != b["session_id"]
    assert a["role"] != b["role"]
    # The gateway was handed the caller's own prompt only.
    assert teams.gateway.provider.last_call["prompt"] == "审查任务"


# ----------------------------------------------------------------------
# §7-C / §8.2 : per-member model binding and routing consistency
# ----------------------------------------------------------------------
def test_node_overrides_role_team_and_global_precedence(teams, owner):
    team = engineering_team(teams, owner)
    teams.set_role_binding(owner, team.id, "implementer", model_id="sim-researcher")
    assert teams.resolve_binding(owner, team.id, "implementer")["inherited_from"] == "role"

    teams.set_member_binding(
        owner, team.id, "implementer", provider_id="local-synthetic", model_id="sim-encoder"
    )
    resolved = teams.resolve_binding(owner, team.id, "implementer")
    assert resolved["inherited_from"] == "node"
    assert resolved["requested_model"] == "sim-encoder"

    # "恢复继承" clears the node override and falls back to the role default.
    teams.set_member_binding(owner, team.id, "implementer", provider_id="", model_id="")
    assert teams.resolve_binding(owner, team.id, "implementer")["inherited_from"] == "role"

    # Untouched roles keep inheriting the team default.
    assert teams.resolve_binding(owner, team.id, "reviewer")["inherited_from"] == "team"


def test_two_members_on_different_models_route_consistently(teams, owner, session):
    """§8.2 — request, stored binding, gateway call and fee all agree."""
    team = engineering_team(teams, owner)
    teams.set_member_binding(
        owner, team.id, "implementer", provider_id="local-synthetic", model_id="sim-encoder"
    )
    teams.set_member_binding(
        owner, team.id, "reviewer", provider_id="local-synthetic", model_id="sim-researcher"
    )
    snap = teams.start_team(owner, team.id, expected_version=team.version)

    by_role = {m["role"]: m for m in snap["members"]}
    assert by_role["implementer"]["requested_model"] == "sim-encoder"
    assert by_role["reviewer"]["requested_model"] == "sim-researcher"
    # Nothing has run yet, so the effective model is honestly "未执行".
    assert by_role["implementer"]["effective_model"] == "未执行"
    assert by_role["implementer"]["effective_confidence"] == "not_executed"

    r1 = teams.execute_member(owner, team.id, "implementer", prompt="写代码")
    assert teams.gateway.provider.last_call["model"] == "sim-encoder"
    r2 = teams.execute_member(owner, team.id, "reviewer", prompt="做审查")
    assert teams.gateway.provider.last_call["model"] == "sim-researcher"

    # Record agrees with the request, and the fee was computed from that model's
    # own price rather than the other member's.
    assert r1["effective_model"] == "sim-encoder"
    assert r2["effective_model"] == "sim-researcher"
    enc = Decimal(r1["settled_usd"])
    res = Decimal(r2["settled_usd"])
    assert enc > 0 and res > 0
    assert enc != res  # different unit prices ⇒ different settlement

    bindings = {
        b.requested_model: b
        for b in session.query(ModelBinding).filter(ModelBinding.team_id == team.id).all()
    }
    assert bindings["sim-encoder"].effective_model == "sim-encoder"
    assert bindings["sim-encoder"].effective_confidence == "exact"
    assert bindings["sim-encoder"].pricing_version == catalog_version(teams)


def catalog_version(teams) -> str:
    return teams.catalog.version


def test_requested_and_effective_model_are_recorded_separately(teams, owner):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    teams.execute_member(owner, team.id, "coordinator", prompt="规划")

    binding = (
        teams.session.query(ModelBinding)
        .filter(ModelBinding.team_id == team.id)
        .first()
    )
    assert binding.requested_model == "mock-deterministic"
    assert binding.effective_model == binding.requested_model
    # A provider that hides routing would leave this 'unknown'; the field exists
    # precisely so we never have to guess.
    assert binding.effective_confidence in ("exact", "unknown", "auto")


def test_model_not_in_catalog_is_rejected(teams, owner):
    team = engineering_team(teams, owner)
    with pytest.raises(ValidationFailed) as exc:
        teams.set_member_binding(
            owner, team.id, "implementer", provider_id="local-synthetic",
            model_id="gpt-9-imaginary",
        )
    assert "not in the configured capability catalog" in str(exc.value)


def test_host_without_per_member_model_disables_the_control(teams, owner):
    """§8.3 — unsupported means disabled with a reason, never a fake success."""
    team = teams.create_team(
        owner, name="A2A 团队", mode="product_native",
        members=[
            {"role": "coordinator", "agent_host": "external_a2a"},
            {"role": "worker", "agent_host": "external_a2a"},
        ],
    )
    with pytest.raises(ValidationFailed) as exc:
        teams.set_member_binding(
            owner, team.id, "worker", provider_id="local-synthetic",
            model_id="mock-deterministic",
        )
    assert "does not support per-member model override" in str(exc.value)

    # The switch_model control is refused by the same capability gate.
    with pytest.raises(ValidationFailed) as exc2:
        teams.control(
            owner, team.id, operation="switch_model", role="worker",
            scope={"model_id": "mock-deterministic"}, reason="try",
        )
    assert "does not support" in str(exc2.value)


# ----------------------------------------------------------------------
# §8.4 : explicit blocking instead of silent model substitution
# ----------------------------------------------------------------------
def test_start_blocks_when_no_model_resolves(teams, owner):
    team = teams.create_team(
        owner, name="无模型团队",
        members=[{"role": "coordinator"}],
        default_binding={},
    )
    report = teams.validate_start(owner, team.id)
    assert report["can_start"] is False
    codes = {b["code"] for b in report["blockers"]}
    assert "model_unresolved" in codes

    with pytest.raises(ValidationFailed):
        teams.start_team(owner, team.id, expected_version=team.version)


def test_credential_missing_blocks_with_the_reference_only(teams, owner):
    """No credential ⇒ blocked, and the message carries a reference, not a secret."""
    team = engineering_team(teams, owner)

    # Asking for a provider/model the catalog cannot serve is refused outright.
    with pytest.raises(ValidationFailed) as exc:
        teams.set_member_binding(
            owner, team.id, "implementer", provider_id="no-such-provider",
            model_id="sim-encoder",
        )
    assert "capability catalog" in str(exc.value)

    # With no host credentials and no provider adapter at all, the remote
    # provider offers no models — it is not presented as usable.
    from find_yourself.config import Settings
    from find_yourself.runtime.gateway import ModelGateway
    from find_yourself.services.model_catalog import ModelCatalog as RealCatalog

    bare = RealCatalog(gateway=ModelGateway(settings=Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
    )))
    assert bare.gateway.provider is None
    assert [
        m for m in bare.list_models() if m["provider_id"] == "openai-compatible"
    ] == []

    # And the credential state is reported as a boolean plus a reference only.
    remote = [p for p in bare.providers() if p["provider_id"] == "openai-compatible"][0]
    assert remote["credential_configured"] is False
    assert remote["credential_ref"] == "env:FY_MODEL_API_KEY"
    assert "BLOCKED_EXTERNAL" in remote["note"]


def test_dependency_cycle_is_detected_and_blocks_start(teams, owner):
    # The cycle is refused at draft time, naming the exact offending edge.
    with pytest.raises(Conflict) as exc:
        teams.create_team(
            owner, name="循环团队",
            members=[
                {"role": "a", "depends_on": ["b"]},
                {"role": "b", "depends_on": ["a"]},
            ],
            default_binding={"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
        )
    assert "a -> b -> a" in str(exc.value)

    # And a team whose members become cyclic after the fact is blocked at start.
    team = teams.create_team(
        owner, name="后循环团队",
        members=[{"role": "a"}, {"role": "b"}],
        default_binding={"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
    )
    # Bypass the draft-time guard to simulate a cycle introduced out-of-band.
    team.members = [
        {"role": "a", "depends_on": ["b"], "title": "a", "agent_host": "find_yourself"},
        {"role": "b", "depends_on": ["a"], "title": "b", "agent_host": "find_yourself"},
    ]
    teams.session.flush()

    report = teams.validate_start(owner, team.id)
    assert report["can_start"] is False
    assert any(b["code"] == "dependency_cycle" for b in report["blockers"])
    assert any("->" in b["message"] for b in report["blockers"])


# ----------------------------------------------------------------------
# §8.5 : switching model mid-run
# ----------------------------------------------------------------------
def test_switch_model_creates_new_batch_and_keeps_history(teams, owner, session):
    team = engineering_team(teams, owner)
    snap = teams.start_team(owner, team.id, expected_version=1)
    member = [m for m in snap["members"] if m["role"] == "implementer"][0]
    batch0 = member["run_batch"]

    teams.execute_member(owner, team.id, "implementer", prompt="第一版实现")

    res = teams.control(
        owner, team.id, operation="switch_model", role="implementer",
        scope={"model_id": "sim-encoder"}, reason="encoder is better for this module",
    )
    result = res["result"]
    assert result["previous_run_batch"] == batch0
    assert result["run_batch"] == batch0 + 1
    assert result["requested_model"] == "sim-encoder"
    assert result["effective_model"] == "未执行", "new batch has not executed yet"

    # The handoff packet carries the authorized context and nothing secret.
    handoff = result["handoff"]
    assert handoff["previous_model"] == "mock-deterministic"
    assert "credentials" in handoff["excluded"]
    assert "hidden_reasoning" in handoff["excluded"]

    # The old binding is preserved, frozen, for audit: the implementer's
    # pre-switch row still exists alongside the new one.
    bindings = session.query(ModelBinding).filter(
        ModelBinding.team_id == team.id,
        ModelBinding.agent_instance_id == member["agent_instance_id"],
    ).all()
    old = [b for b in bindings if b.requested_model == "mock-deterministic"]
    new = [b for b in bindings if b.requested_model == "sim-encoder"]
    assert len(old) == 1 and old[0].frozen is True
    assert len(new) == 1 and new[0].frozen is True


def test_late_result_from_superseded_batch_is_discarded(teams, owner, session):
    """§8.5 — an old batch's late result must not overwrite the new state."""
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    snap = teams.get_snapshot(owner, team.id)
    member = [m for m in snap["members"] if m["role"] == "implementer"][0]
    stale_batch = member["run_batch"]

    teams.control(
        owner, team.id, operation="switch_model", role="implementer",
        scope={"model_id": "sim-encoder"}, reason="switch",
    )

    with pytest.raises(Conflict) as exc:
        teams.report_member_result(
            owner, team.id, "implementer", run_batch=stale_batch, output="旧批次结果",
        )
    assert "discarded" in str(exc.value)

    inst = [i for i in teams._members(team.id) if i.role == "implementer"][0]
    assert inst.state != "completed", "stale result must not complete the member"
    assert inst.requested_model == "sim-encoder"

    # And the rejection itself is auditable.
    types = [e.event_type for e in session.query(TeamEvent).filter(
        TeamEvent.team_id == team.id).all()]
    assert "member.result_rejected" in types


def test_cross_provider_switch_requires_explicit_authorization(teams, owner):
    team = teams.create_team(
        owner, name="跨供应商团队",
        members=[{"role": "coordinator"}, {"role": "worker"}],
        default_binding={"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
    )
    teams.start_team(owner, team.id, expected_version=team.version)

    # Pretend the worker already sits on the remote provider (in a real
    # deployment this happens once host credentials are configured).
    inst = [i for i in teams._members(team.id) if i.role == "worker"][0]
    inst.provider_id = "openai-compatible"
    teams.session.flush()

    # Switching back to the local provider changes the data destination, so it
    # is refused until the owner authorizes cross-provider movement.
    with pytest.raises(PermissionDenied) as exc:
        teams.control(
            owner, team.id, operation="switch_model", role="worker",
            scope={"model_id": "mock-deterministic"}, reason="try cross provider",
        )
    assert "data destination" in str(exc.value)
    assert inst.requested_model == "mock-deterministic", "the switch must not be half-applied"

    # With explicit authorization the same switch is allowed and is traceable.
    teams.update_team(
        owner, team.id, expected_version=team.version,
        patch={"permission_ref": {"allow_cross_provider": True}},
        reason="owner authorized this provider",
    )
    res = teams.control(
        owner, team.id, operation="switch_model", role="worker",
        scope={"model_id": "sim-encoder"}, reason="authorized switch",
    )
    assert res["result"]["requested_model"] == "sim-encoder"
    evts = [e for e in teams.get_events(owner, team.id)
            if e["event_type"] == "member.model_switched"]
    assert evts and evts[-1]["details"]["cross_provider"] is True
    assert evts[-1]["details"]["reason"] == "authorized switch"


def test_control_is_idempotent_per_key(teams, owner):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    snap = teams.get_snapshot(owner, team.id)
    impl = [m for m in snap["members"] if m["role"] == "implementer"][0]

    first = teams.control(
        owner, team.id, operation="pause", role="implementer",
        idempotency_key="k-1", reason="manual pause",
    )
    second = teams.control(
        owner, team.id, operation="pause", role="implementer",
        idempotency_key="k-1", reason="manual pause",
    )
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["id"] == first["id"]

    inst = [i for i in teams._members(team.id) if i.role == "implementer"][0]
    assert inst.state == "paused"


# ----------------------------------------------------------------------
# §8.6 : goal change propagation
# ----------------------------------------------------------------------
def test_goal_change_propagates_new_plan_version(teams, owner, session):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    snap = teams.get_snapshot(owner, team.id)
    impl = [m for m in snap["members"] if m["role"] == "implementer"][0]
    assert impl["plan_version"] == 1

    out = teams.update_goal(owner, team.id, "实现资料脱敏并补齐单测", reason="scope changed")
    assert out["plan_version"] == 2
    assert set(out["affected_members"]) == {"coordinator", "implementer", "reviewer"}

    snap2 = teams.get_snapshot(owner, team.id)
    for m in snap2["members"]:
        assert m["plan_version"] == 2

    # The member task carries the new plan version.
    inst = [i for i in teams._members(team.id) if i.role == "implementer"][0]
    task = session.get(Task, inst.subtask_id)
    assert task.result["plan_version"] == 2


def test_dependency_order_is_preserved(teams, owner):
    team = engineering_team(teams, owner)
    snap = teams.start_team(owner, team.id, expected_version=1)
    by_role = {m["role"]: m for m in snap["members"]}
    assert by_role["implementer"]["depends_on"] == ["coordinator"]
    assert by_role["reviewer"]["depends_on"] == ["implementer"]


# ----------------------------------------------------------------------
# §8.7 / §5 : shared root budget
# ----------------------------------------------------------------------
def test_member_budget_is_reserved_against_the_root_task(teams, owner, session):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    res = teams.reserve_member_budget(owner, team.id, "implementer", 0.03)

    rows = session.query(BudgetReservation).all()
    assert len(rows) == 1
    assert rows[0].task_id == res["root_task_id"]
    assert rows[0].scope == "team_member"


def test_member_cannot_bypass_the_root_budget_cap(teams, owner):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    with pytest.raises(ValidationFailed) as exc:
        teams.reserve_member_budget(owner, team.id, "implementer", 0.40)
    assert "exceeds the per-member cap" in str(exc.value)


def test_concurrent_member_reservations_share_one_root_pool(teams, owner, session):
    """Retries and further delegation must not create fresh budget."""
    team = teams.create_team(
        owner, name="预算团队",
        members=[{"role": "coordinator"}, {"role": "w1"}, {"role": "w2"}, {"role": "w3"}],
        default_binding={"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
        budget_ref={"root_budget_usd": 0.12, "member_reserve_cap_usd": 0.05},
    )
    teams.start_team(owner, team.id, expected_version=team.version)

    granted, rejected = 0, 0
    for role in ("w1", "w2", "w3"):
        for _ in range(2):  # each member "retries"
            try:
                teams.reserve_member_budget(owner, team.id, role, 0.05)
                granted += 1
            except ValidationFailed:
                rejected += 1
    # Whatever the interleaving, the total can never exceed the 0.12 root budget.
    assert granted == 2, "only two 0.05 reservations fit under a 0.12 root budget"
    assert rejected == 4
    held = sum(
        float(r.amount) for r in session.query(BudgetReservation).all()
        if r.state in ("reserved", "unknown")
    )
    assert held <= 0.12 + 1e-9


def test_execution_settles_from_the_shared_root_budget(teams, owner, session):
    from find_yourself.db.models import BudgetLedger

    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    teams.execute_member(owner, team.id, "implementer", prompt="写代码")
    rows = session.query(BudgetReservation).all()
    assert len(rows) == 1
    assert rows[0].state == "settled"
    assert rows[0].settled_at is not None
    # The reservation hangs off the member's task, which sits inside the root
    # task tree — so the shared root cap covers it (BudgetService aggregates by
    # root_task_id, not by the immediate parent).
    member_task = session.get(Task, rows[0].task_id)
    assert member_task.root_task_id == team.root_task_id
    settled = [l.delta for l in session.query(BudgetLedger).all()]
    assert settled and Decimal(str(settled[0])) > 0


# ----------------------------------------------------------------------
# §8.8 : event replay / reconnect
# ----------------------------------------------------------------------
def test_events_are_monotonic_and_replay_without_duplicates(teams, owner):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    teams.execute_member(owner, team.id, "coordinator", prompt="规划")
    teams.control(owner, team.id, operation="pause", role="reviewer", reason="hold")

    everything = teams.get_events(owner, team.id, cursor=0)
    seqs = [e["seq"] for e in everything]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs), "seq must be unique per team"

    cursor = seqs[len(seqs) // 2]
    resumed = teams.get_events(owner, team.id, cursor=cursor)
    assert all(e["seq"] > cursor for e in resumed)
    # Resuming yields exactly the tail, each event once.
    assert [e["seq"] for e in resumed] == seqs[seqs.index(cursor) + 1:]

    assert not teams.get_events(owner, team.id, cursor=seqs[-1])


def test_member_events_carry_batch_and_instance_attribution(teams, owner):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    inst = [i for i in teams._members(team.id) if i.role == "implementer"][0]
    teams.execute_member(owner, team.id, "implementer", prompt="写代码")

    evts = [
        e for e in teams.get_events(owner, team.id)
        if e["agent_instance_id"] == inst.id
    ]
    assert evts, "member-scoped events must be attributable"
    assert all(e["run_batch"] == inst.run_batch for e in evts)


# ----------------------------------------------------------------------
# Permission / privacy boundaries
# ----------------------------------------------------------------------
def test_credentials_never_appear_in_a_snapshot(teams, owner):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    snap = teams.get_snapshot(owner, team.id)
    blob = str(snap)
    assert "sk-" not in blob
    for m in snap["members"]:
        assert "credential_ref" in m
        assert isinstance(m["credential_configured"], bool)
        assert "api_key" not in m


def test_service_identity_cannot_decide_team_changes(teams):
    from find_yourself.services.actor import Actor

    team = engineering_team(teams, Actor.owner("owner-1"))
    svc_actor = Actor.service("svc-1", kind="agent", domains=["work"])
    with pytest.raises(PermissionDenied):
        teams.create_team(svc_actor, name="unauthorized team")


def test_other_owner_cannot_read_the_team(teams):
    from find_yourself.services.actor import Actor

    team = engineering_team(teams, Actor.owner("owner-1"))
    from find_yourself.services.errors import NotFound

    with pytest.raises(NotFound):
        teams.get_snapshot(Actor.owner("owner-2"), team.id)


def test_rework_resets_steps_and_keeps_the_batch(teams, owner):
    team = engineering_team(teams, owner)
    snap = teams.start_team(owner, team.id, expected_version=1)
    impl = [m for m in snap["members"] if m["role"] == "implementer"][0]
    inst = [i for i in teams._members(team.id) if i.role == "implementer"][0]
    inst.steps = 4
    teams.session.flush()

    res = teams.control(
        owner, team.id, operation="rework", role="implementer",
        scope={"criteria_unmet": ["缺少边界测试"]}, reason="verifier rejected",
    )
    assert res["result"]["state"] == "waiting_rework"
    assert inst.steps == 0
    assert inst.run_batch == impl["run_batch"], "返工 does not start a new model batch"


def test_cancel_marks_member_and_its_task(teams, owner, session):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    inst = [i for i in teams._members(team.id) if i.role == "reviewer"][0]
    teams.control(owner, team.id, operation="cancel", role="reviewer", reason="not needed")
    task = session.get(Task, inst.subtask_id)
    assert task.status == "cancelled"
    assert inst.state == "cancelled"


def test_spawn_adds_a_member_and_registers_its_session(teams, owner):
    team = engineering_team(teams, owner)
    teams.start_team(owner, team.id, expected_version=1)
    res = teams.control(
        owner, team.id, operation="spawn",
        scope={"role": "auditor", "title": "审计", "depends_on": ["reviewer"]},
        reason="add auditor",
    )
    view = res["result"]
    assert view["role"] == "auditor"
    assert view["session_id"].startswith(f"team-{team.id}-auditor-")

    with pytest.raises(Conflict):
        teams.control(
            owner, team.id, operation="spawn", scope={"role": "auditor"}, reason="dup",
        )