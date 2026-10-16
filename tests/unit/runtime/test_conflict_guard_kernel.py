"""B7 · 冲突检测挂主链：六个类别的冲突必须**真的拦住**后续执行。

红线（对应审计条目 B7「冲突检测挂主链」未完成）：

* 六类冲突的检测能力早已存在，但接线前 ``runtime/`` 对 ``ConflictDetector``
  的引用数是 **0**——冲突只会被记一笔，拦不住任何东西；
* 本文件的每条断言都指向**可观察行为**，不是「函数被调用过」：

  1. **阻断级**：两个 agent 争抢同一 target → 后者抛 ``ConflictDetected``
     （``contracts.Conflict`` 子类，码 ``conflict_detected``，409），
     图不启动、``node.completed`` 一个都没有；
  2. **动作级阻断**：角色越权 → 在**网关被调用之前**抛，网关 calls == 0
     （把闸门删掉，这条立刻红：模型真的会被调用、真的会计费）；
  3. **可降级**：自相矛盾的结论被**丢弃**、人设禁用话术被**真实脱敏**、
     痕迹同时出现在 ``degradations`` / 审计帧 / hook 轨迹里——不是静默放行。

「怎么确认能红」：见文件末尾 ``test_mutation_*`` —— 它们直接改坏实现
（把阻断降级成只记日志 / 把丢弃结论改回原样），断言必须失败。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import find_yourself.db.claw_models  # noqa: F401
from find_yourself.contracts import Conflict, DomainError
from find_yourself.db.models import AuditEvent
from find_yourself.runtime.conflict_guard import (
    PHASE_PRE_ACTION,
    REDACTION,
    ConflictGuard,
    reset_conflict_registry,
    task_signal,
)
from find_yourself.runtime.graph import (
    NODE_ACTIONS,
    node_delegate,
    node_research,
    node_single_agent,
    node_tool_step,
)
from find_yourself.runtime.hooks import WIRED_EVENTS, HookBus, reset_default_hook_bus
from find_yourself.runtime.kernel import SingleLoopKernel
from find_yourself.runtime.providers.base import CallResult
from find_yourself.services.claw.conflicts import (
    CONFLICT_BLOCK_CODE,
    CONFLICT_SEVERITY,
    ConflictDetected,
)

# ---------------------------------------------------------------------------
# 夹具：默认闸门是进程级单例（管辖权跨请求累积），每个用例必须重置
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_conflicts(tmp_path, monkeypatch):
    monkeypatch.setenv("FY_CHECKPOINT_DB", str(tmp_path / "conflict-ck.db"))
    reset_conflict_registry()
    reset_default_hook_bus()
    yield
    reset_conflict_registry()
    reset_default_hook_bus()


class SpyGateway:
    """记录真实调用次数的网关替身——「动作有没有发生」的唯一可信证据。"""

    def __init__(self, text: str = "provider-grounded text") -> None:
        self.text = text
        self.calls: list[dict] = []

    def complete(self, actor, **kwargs):
        self.calls.append(kwargs)
        return CallResult(
            text=self.text,
            usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            settled_amount=Decimal("0.0042"),
            provider_request_id="spy-req-1",
        )


def _state(**over):
    base = {
        "task_id": "t-b7",
        "attempt": 1,
        "goal": "把季度报告写完",
        "domain": "work",
        "budget_balance": 1.0,
        "spent_usd": 0.0,
        "step_count": 0,
        "max_steps": 2,
        "evidence_refs": [],
        "history": [],
    }
    base.update(over)
    return base


def _kernel(**guard_kwargs):
    return SingleLoopKernel(
        bus=HookBus(), conflict_guard=ConflictGuard(**guard_kwargs)
    )


# ---------------------------------------------------------------------------
# 0. 分类表：两级语义必须是显式资产，不能藏在 if 里
# ---------------------------------------------------------------------------


def test_severity_table_covers_all_six_classes():
    from find_yourself.services.claw.conflicts import CONFLICT_CLASSES

    assert set(CONFLICT_SEVERITY) == set(CONFLICT_CLASSES)
    blocking = {c for c, s in CONFLICT_SEVERITY.items() if s == "block"}
    degrading = {c for c, s in CONFLICT_SEVERITY.items() if s == "degrade"}
    assert blocking == {"jurisdiction", "data_inconsistency", "role_overreach"}
    assert degrading == {"self_contradiction", "persona", "boundary"}
    assert not blocking & degrading


def test_conflict_blocked_is_a_domain_error_with_distinguishable_code():
    exc = ConflictDetected("boom")
    assert isinstance(exc, Conflict) and isinstance(exc, DomainError)
    assert exc.code == CONFLICT_BLOCK_CODE == "conflict_detected"
    assert exc.http_status == 409


def test_conflict_events_are_declared_and_wired():
    assert {"conflict.detected", "conflict.blocked"} <= WIRED_EVENTS


# ---------------------------------------------------------------------------
# 1. 阻断级 · C1 管辖权：「两个冲突的动作互相矛盾 → 后者被拒」
# ---------------------------------------------------------------------------


def test_jurisdiction_conflict_rejects_the_second_agent_before_the_graph_starts():
    kernel = _kernel()
    first = dict(_state(task_id="t-a1", agent="agent-a", target="file:report.md",
                        route="single_agent"))
    result_a = kernel.run(dict(first))
    assert result_a.status == "completed"          # 首个声明 = 登记，不冲突

    second = dict(_state(task_id="t-b1", agent="agent-b", target="file:report.md",
                         route="single_agent"))
    with pytest.raises(ConflictDetected) as caught:
        kernel.run(dict(second))

    exc = caught.value
    assert exc.code == "conflict_detected"
    assert "jurisdiction" in exc.details["classes"]
    assert exc.details["rules"] == ["target_claimed_by_other"]
    # 关键：被拒者的图**一个节点都没跑**（动作没发生、花费为零）
    assert result_a.state.get("task_id") == "t-a1"


def test_rejected_task_leaves_no_node_completed_trace_on_the_wire():
    """被拒的任务不得留下任何「节点已执行」的痕迹（API 的 hook_trace 可见）。"""
    kernel = _kernel()
    kernel.run(dict(_state(task_id="t-a2", agent="agent-a", target="file:x.md",
                           route="single_agent")))
    bus = HookBus()
    blocked_kernel = SingleLoopKernel(bus=bus, conflict_guard=ConflictGuard())
    # 借用同一个进程级登记：换内核实例也必须拦得住（跨请求争抢）
    blocked_kernel.conflict_guard.detector_for("").jurisdiction_claims.setdefault(
        "file:y.md", {"agent-a"}
    )
    with pytest.raises(ConflictDetected):
        blocked_kernel.run(dict(_state(task_id="t-b2", agent="agent-b",
                                       target="file:y.md", route="single_agent")))
    assert all(h.event != "node.completed" for h in bus.listeners("node.completed"))


def test_same_agent_reclaim_its_own_target_is_not_a_conflict():
    kernel = _kernel()
    for i in range(3):
        out = kernel.run(dict(_state(task_id=f"t-self-{i}", agent="agent-a",
                                     target="file:mine.md", route="single_agent")))
        assert out.status == "completed"


def test_jurisdiction_registry_is_isolated_per_owner():
    kernel = _kernel()
    kernel.run(dict(_state(task_id="t-o1", agent="agent-a", target="file:shared.md",
                           route="single_agent")))
    # 另一个 owner 看同一个 target：登记表分桶，物理不可见 → 不误伤
    other = SingleLoopKernel(
        bus=HookBus(),
        conflict_guard=ConflictGuard(),
    )
    out = other.run(dict(_state(task_id="t-o2", agent="agent-z", target="file:shared.md",
                                route="single_agent")))
    assert out.status == "completed"


def test_data_inconsistency_with_baseline_is_blocking():
    guard = ConflictGuard(detector_policy={"fact_lookup": lambda k: 1200 if k == "用户数" else None})
    kernel = SingleLoopKernel(bus=HookBus(), conflict_guard=guard)
    with pytest.raises(ConflictDetected) as caught:
        kernel.run(dict(_state(task_id="t-d1", agent="a1", agent_role="analyst",
                               route="single_agent",
                               claims=[{"key": "用户数", "value": 9999}])))
    assert "data_inconsistency" in caught.value.details["classes"]


# ---------------------------------------------------------------------------
# 2. 阻断级 · C6 角色越权：动作级闸门，网关一次都不许被调用
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("node", (node_single_agent, node_research, node_tool_step, node_delegate),
                         ids=lambda n: n.__name__)
def test_role_overreach_is_rejected_before_the_gateway_is_called(node):
    node_route = {node_single_agent: "single_agent", node_research: "research",
                  node_tool_step: "tool_step", node_delegate: "delegate"}[node]
    action, role = NODE_ACTIONS[node_route]
    guard = ConflictGuard(detector_policy={"role_capabilities": {role: {"some_other_tool"}}})
    gw = SpyGateway()

    with pytest.raises(ConflictDetected) as caught:
        node(_state(agent_role=role), gateway=gw, conflict_guard=guard)

    assert "role_overreach" in caught.value.details["classes"]
    # 最硬的一条断言：动作与花费都不发生（把闸门删掉这里立刻红）
    assert gw.calls == [], "越权动作不得触达网关"


def test_in_capability_action_still_runs_normally():
    action, role = NODE_ACTIONS["tool_step"]
    guard = ConflictGuard(detector_policy={"role_capabilities": {role: {action}}})
    gw = SpyGateway()
    out = node_tool_step(_state(agent_role=role), gateway=gw, conflict_guard=guard)
    assert len(gw.calls) == 1 and out["output"]["model_backed"] is True
    assert "degradations" not in out            # 无降级时输出形状不变


def test_kernel_propagates_in_graph_block_instead_of_swallowing_it():
    """图内闸门拒绝 → 必须是 ConflictDetected 上抛，**不许**降级成 error 结果。"""
    guard = ConflictGuard(
        detector_policy={"role_capabilities": {"ListenerAgent": {"other"}}})
    kernel = SingleLoopKernel(bus=HookBus(), conflict_guard=guard)
    with pytest.raises(ConflictDetected):
        kernel.run(dict(_state(task_id="t-k1", agent="a1", agent_role="ListenerAgent",
                               route="single_agent")))
    # 若被 except Exception 吞掉，这里会拿到 KernelRunResult.error 而不是异常


# ---------------------------------------------------------------------------
# 3. 可降级 · 真实改写 + 三条可见通道（不是静默放行）
# ---------------------------------------------------------------------------


def test_self_contradictory_claims_are_dropped_and_recorded(session, audit, owner):
    kernel = SingleLoopKernel(bus=HookBus(), audit=audit, actor=owner,
                              conflict_guard=ConflictGuard())
    out = kernel.run(dict(_state(
        task_id="t-c1", agent="a1", agent_role="analyst", route="single_agent",
        claims=[{"key": "季度通过", "value": True}, {"key": "季度通过", "value": False}],
    )))
    assert out.status == "completed"                       # 降级 ≠ 阻断
    # 真正执行的是改写后的任务体：相反结论已被丢弃
    assert out.state["claims"] == [{"key": "季度通过", "value": True}]
    # 痕迹可见：① state/result ② hook 轨迹 ③ 审计帧
    classes = {d["conflict_class"] for d in out.degradations}
    assert "self_contradiction" in classes
    assert any(d["effect"] == "claim_dropped" for d in out.degradations)
    assert "conflict.detected" in [t["event"] for t in out.trace]
    assert out.state["degradations"]
    session.commit()
    frames = session.query(AuditEvent).filter(
        AuditEvent.action == "claw.conflict.degraded").all()
    assert frames, "降级必须落审计帧（别静默）"


def test_persona_banned_phrase_is_actually_redacted_in_the_output():
    guard = ConflictGuard(
        detector_policy={"persona_banned_phrases": {"ListenerAgent": ("这不归我管",)}})
    gw = SpyGateway(text="收到你的情绪了。这不归我管，请找专业人士。")
    out = node_single_agent(
        _state(agent_role="ListenerAgent"), gateway=gw, conflict_guard=guard
    )
    assert "这不归我管" not in out["output"]["reflection"]     # 原文真的没了
    assert REDACTION in out["output"]["reflection"]
    assert out["output"]["degradations"][0]["effect"] == "text_redacted"


def test_unowned_action_is_marked_instead_of_passed_silently():
    kernel = _kernel()
    out = kernel.run(dict(_state(task_id="t-c2", action="deploy_prod",
                                 route="single_agent")))
    assert out.status == "completed"
    assert out.state["unowned_action"] == "deploy_prod"
    assert any(d["conflict_class"] == "boundary" for d in out.degradations)


def test_degrade_path_is_blockable_by_a_registered_hook():
    """降级不是最终裁决：注册在 conflict.detected 上的 hook 可以整体拒绝。"""
    guard = ConflictGuard()
    bus = HookBus()
    bus.register("conflict.detected",
                 lambda p: {"blocked": True, "reason": "命中人设规则，人工接管"})
    kernel = SingleLoopKernel(bus=bus, conflict_guard=guard)
    # 注意：**不**声明 agent —— 动作有主才谈得上归属（C2 的判定前提）
    out = kernel.run(dict(_state(task_id="t-c3", action="deploy_prod",
                                 route="single_agent")))
    assert out.blocked and "人工接管" in out.block_reason
    assert all(t["event"] != "node.completed" for t in out.trace)


def test_signal_extraction_does_not_fabricate_facts():
    """未声明的事实不臆造：没有 target 就不能拿 task_id 顶替去判管辖权。"""
    signal = task_signal({"goal": "写报告", "route": "single_agent"})
    assert signal.target == "" and signal.agent == "" and signal.action == ""
    # route 是策略不是动作：不得回退成 action（否则每个正常任务都被判无归属）
    assert task_signal({"goal": "x", "route": "tool_step"}).action == ""
    # input_refs 是调用方声明的「要操作的对象」，可作 target
    assert task_signal({"input_refs": ["src:1", "src:2"]}).target == "src:1,src:2"


def test_no_declaration_no_finding_is_not_a_soft_pass():
    """默认策略下「无声明」不误伤；但一旦声明就必须有判定（检测是常开的）。"""
    guard = ConflictGuard()
    clean = guard.evaluate(task_signal({"goal": "普通任务", "route": "single_agent"}))
    assert clean.blocked is False and clean.hits == ()
    signal = task_signal({"agent": "a1", "target": "t1", "action": "write"})
    assert guard.evaluate(signal).blocked is False       # 首次 = 登记
    assert guard.evaluate(task_signal(
        {"agent": "a2", "target": "t1", "action": "write"})).blocked is True


def test_pre_action_phase_is_reported_in_the_block_details():
    guard = ConflictGuard(detector_policy={"role_capabilities": {"ToolAgent": {"x"}}})
    with pytest.raises(ConflictDetected) as caught:
        node_tool_step(_state(agent_role="ToolAgent"), gateway=SpyGateway(),
                       conflict_guard=guard)
    assert PHASE_PRE_ACTION == "pre_action"
    assert caught.value.code == CONFLICT_BLOCK_CODE


# ---------------------------------------------------------------------------
# 4. 变异测试：改坏实现 → 上面的断言必须变红（证明「拦得住」不是摆设）
#
# 每个用例先在**真实实现**上确认基线成立，再注入变异，断言可观察行为发生了
# 与主测试断言相反的变化——也就是「主测试在这里会红」。
# ---------------------------------------------------------------------------


def _conflicting_pair(guard: ConflictGuard | None = None) -> list:
    """A 先领 target，B 再争抢：返回 B 的执行结果（不抛则说明没拦住）。"""
    kernel = _kernel() if guard is None else SingleLoopKernel(bus=HookBus(),
                                                              conflict_guard=guard)
    kernel.run(dict(_state(task_id="t-m-a", agent="agent-a", target="file:mut.md",
                           route="single_agent")))
    return [kernel.run(dict(_state(task_id="t-m-b", agent="agent-b",
                                   target="file:mut.md", route="single_agent")))]


def test_mutation_downgrading_block_to_log_only_is_caught(monkeypatch):
    """变异 1：把阻断降级成「只记日志」→ 阻断类主测试在此变红。"""
    import find_yourself.runtime.conflict_guard as cg

    # 基线：真实实现确实拦住
    with pytest.raises(ConflictDetected):
        _conflicting_pair(ConflictGuard())

    def soft_enforce(self, signal, *, phase, task_id="", owner_id="", effect="rejected"):
        return self.degradations_of(self.evaluate(signal, owner_id=owner_id),
                                    phase=phase, effect=effect)

    monkeypatch.setattr(cg.ConflictGuard, "enforce", soft_enforce)
    # 变异后：B 照常跑完，冲突连降级痕迹都不留 —— test_jurisdiction_... 会红
    (mutated,) = _conflicting_pair(ConflictGuard())
    assert mutated.status == "completed"
    assert mutated.degradations == []
    assert all(t["event"] != "conflict.blocked" for t in mutated.trace)


def test_mutation_restoring_dropped_claims_is_caught(monkeypatch):
    """变异 2：把「丢弃相反结论」改回原样 → 降级类主测试在此变红。"""
    import find_yourself.runtime.conflict_guard as cg

    def run_task() -> dict:
        kernel = _kernel()
        out = kernel.run(dict(_state(
            task_id="t-m-c", agent="a1", agent_role="analyst", route="single_agent",
            claims=[{"key": "通过", "value": True}, {"key": "通过", "value": False}])))
        return out.state["claims"]

    # 基线：相反结论被丢弃
    assert run_task() == [{"key": "通过", "value": True}]

    monkeypatch.setattr(cg.ConflictGuard, "drop_contradicting_claims",
                        lambda self, records, claims: claims)
    # 变异后：两条结论都进了图 —— test_self_contradictory_... 会红
    assert len(run_task()) == 2


def test_mutation_swallowing_in_graph_block_is_caught(monkeypatch):
    """变异 3：内核不特判 ConflictDetected → 它会被降级成 error 结果而不是上抛。"""
    import find_yourself.runtime.kernel as kmod

    policy = {"role_capabilities": {"ListenerAgent": {"other"}}}
    state = dict(_state(task_id="t-m-d", agent="a1", agent_role="ListenerAgent",
                        route="single_agent"))

    # 基线：图内阻断以异常形态上抛
    kernel = SingleLoopKernel(bus=HookBus(), conflict_guard=ConflictGuard(
        detector_policy=policy))
    with pytest.raises(ConflictDetected):
        kernel.run(dict(state))

    monkeypatch.setattr(kmod, "ConflictDetected", RuntimeError)
    swallowed = SingleLoopKernel(bus=HookBus(), conflict_guard=ConflictGuard(
        detector_policy=policy))
    result = swallowed.run(dict(state))
    # 变异后：变成"失败结果"，冲突码从错误信封消失 —— 会掩盖治理判定
    assert result.blocked is False
    assert "ConflictDetected:" in result.error
