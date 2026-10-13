"""FIX A1：图执行节点必须真调模型、真实计费，未配置时诚实失败（FY-A-1 / N1）。

红线（对应 03-实施方案 §0.1 第 2 条「不制造幻觉」）：

* 节点输出与费用必须来自**真实** ``ModelGateway.complete`` 调用，费用取真实
  ``CallResult.settled_amount``；
* 网关缺失/未配置/调用失败时，节点必须返回**诚实失败**
  （``model_not_configured`` / ``model_unavailable:*``），且**不得**记账任何
  spend、不得返回捏造文本；
* 被删除的历史捏造值不得复活：旧实现对四个路由分别硬编码
  ``final_spent_usd`` 0.001 / 0.002 / 0.005 / 0.003，并伪造来源
  ``src:public_corpus_2026``。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from find_yourself.runtime.graph import (
    build_task_graph,
    node_delegate,
    node_research,
    node_single_agent,
    node_tool_step,
    node_validate,
)

# 旧实现硬编码在四个节点里的假费用——任何一个节点再返回它即为回归。
LEGACY_HARDCODED_SPEND = (0.001, 0.002, 0.005, 0.003)

NODES = (node_single_agent, node_research, node_tool_step, node_delegate)


class StubGateway:
    """最小 ``ModelGateway`` 替身：记录调用，返回可控 usage 与 settled_amount。"""

    def __init__(self, settled: str = "0.0042", text: str = "provider-grounded text"):
        self.settled = Decimal(settled)
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def complete(self, actor: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        from find_yourself.runtime.providers.base import CallResult

        return CallResult(
            text=self.text,
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            settled_amount=self.settled,
            provider_request_id="stub-req-1",
        )


class UnconfiguredGateway:
    """行为等价于未配置网关：``complete`` 一律抛错。"""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, actor: Any, **kwargs: Any) -> Any:
        self.calls += 1
        exc = RuntimeError("no provider wired")
        exc.code = "ModelNotConfigured"  # type: ignore[attr-defined]
        raise exc


def _state(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task_id": "t-A1",
        "attempt": 1,
        "goal": "I feel stuck on my career decision",
        "domain": "personal",
        "granted_source_ids": ["src-alpha"],
        "budget_balance": 1.0,
        "spent_usd": 0.0,
        "step_count": 0,
        "max_steps": 5,
        "evidence_refs": [],
        "history": [],
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# 1. 网关可用：真实调用 + 真实计费
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("node", NODES, ids=lambda n: n.__name__)
def test_node_calls_real_gateway_and_books_real_spend(node):
    gw = StubGateway(settled="0.0042")
    out = node(_state(), gateway=gw)

    assert len(gw.calls) == 1, "节点必须真实调用 gateway.complete"
    assert gw.calls[0]["prompt"], "调用必须带真实 prompt"
    assert out["output"]["model_backed"] is True
    assert out["output"]["provider_request_id"] == "stub-req-1"
    assert out["output"]["usage"]["total_tokens"] == 18

    # 费用来自真实 settled_amount，且记账进 spent_usd / budget_balance。
    assert out["spent_usd"] == pytest.approx(0.0042)
    assert out["budget_balance"] == pytest.approx(1.0 - 0.0042)
    assert out["spent_usd"] not in LEGACY_HARDCODED_SPEND


@pytest.mark.parametrize("node", NODES, ids=lambda n: n.__name__)
def test_node_never_returns_legacy_hardcoded_spend(node):
    """防回归：真实用量为 0 时也必须如实记 0，绝不回退到旧硬编码常量。"""
    gw = StubGateway(settled="0")
    out = node(_state(), gateway=gw)
    assert out["spent_usd"] == pytest.approx(0.0)


def test_research_citations_come_only_from_granted_sources():
    out = node_research(_state(granted_source_ids=["src-alpha", "src-beta"]), gateway=StubGateway())
    assert out["output"]["citations"] == ["src:src-alpha", "src:src-beta"]
    assert out["output"]["verified"] is True


def test_research_without_grants_is_marked_unverified():
    out = node_research(_state(granted_source_ids=[]), gateway=StubGateway())
    assert out["output"]["citations"] == []
    assert out["output"]["verified"] is False


def test_research_does_not_fabricate_public_corpus_source():
    """旧的捏造来源 ``src:public_corpus_2026`` 不得复活。"""
    out = node_research(_state(), gateway=StubGateway())
    blob = repr(out)
    assert "public_corpus_2026" not in blob


def test_tool_step_does_not_claim_execution():
    """工具未接线时必须如实标注 not_executed，不得伪造执行回执。"""
    out = node_tool_step(_state(), gateway=StubGateway())
    assert out["output"]["status"] == "not_executed"
    assert out["output"]["result"] is None


def test_delegate_withholds_raw_history():
    out = node_delegate(_state(history=[{"role": "user", "content": "secret"}]), gateway=StubGateway())
    assert out["output"]["scoped_context"]["raw_history_provided"] is False
    assert "secret" not in repr(out["output"])


# --------------------------------------------------------------------------- #
# 2. 网关不可用：诚实失败，不记账、不伪造
# --------------------------------------------------------------------------- #


def test_missing_gateway_fails_honestly_with_no_spend():
    out = node_single_agent(_state(), gateway=None)

    assert out["status"] == "failed"
    assert out["error"] == "model_not_configured"
    assert out["output"]["model_backed"] is False
    # 关键：不记账任何费用，不伪造文本。
    assert "spent_usd" not in out
    assert out["output"].get("reflection") is None


@pytest.mark.parametrize("node", NODES, ids=lambda n: n.__name__)
def test_failing_gateway_surfaces_error_code_and_books_nothing(node):
    gw = UnconfiguredGateway()
    out = node(_state(), gateway=gw)

    assert gw.calls == 1, "必须真的尝试调用，再据实报告失败"
    assert out["status"] == "failed"
    assert "ModelNotConfigured" in out["error"]
    assert "spent_usd" not in out


# --------------------------------------------------------------------------- #
# 3. 图装配：网关注入 + 有界循环不被破坏
# --------------------------------------------------------------------------- #


def test_build_task_graph_accepts_injected_gateway():
    gw = StubGateway()
    graph = build_task_graph(gateway=gw)
    assert graph is not None


def test_build_task_graph_survives_unbuildable_default_gateway(monkeypatch):
    """默认网关构造失败时必须降级为「未配置」，而不是让建图崩掉。"""
    import find_yourself.runtime.graph as g

    monkeypatch.setattr(g, "_default_gateway", lambda: None)
    graph = build_task_graph()
    assert graph is not None


@pytest.mark.parametrize(
    ("step_count", "expected_stage"),
    [(0, "planning"), (3, "planning"), (4, "finish")],
)
def test_validate_closes_loop_by_step_count(step_count, expected_stage):
    out = node_validate(_state(step_count=step_count, max_steps=5))
    assert out["stage"] == expected_stage
    assert out["step_count"] == step_count + 1