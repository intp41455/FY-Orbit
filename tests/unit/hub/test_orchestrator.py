"""守住多 Agent 协同编排的行为约定。

要守住的约定
------------
1. **诚实**：匹配不上就不派单（不硬凑）、单步失败如实记录、不吞异常
2. **上下文**：sequential/pipeline 后一步能拿到前一步的产出（多轮 messages）
3. **并行**：各侧面互不知情（不共享上下文）
4. **汇总**：汇总器不是 Agent 时如实返回原结果，不硬造「总结」
5. **显式指定**：pipeline 能绕过路由直接指定连接
"""

from __future__ import annotations

from typing import Any

import pytest

from find_yourself.services.hub.orchestrator import Orchestrator, _as_text


class _FakeRouter:
    """按 hint 里的关键词返回固定候选，模拟确定性路由。"""

    def __init__(self, table: dict[str, list[dict[str, Any]]]):
        self.table = table
        self.calls: list[str] = []

    def route(self, hint: str, top_k: int = 5, kind: str | None = None):
        self.calls.append(hint)
        for key, cands in self.table.items():
            if key in hint:
                return cands[:top_k]
        return []


def _cand(conn: str, cap: str, name: str = "") -> dict[str, Any]:
    return {"connection_id": conn, "connection_name": name or conn,
            "capability": type("C", (), {"name": cap})()}


class _FakeInvoke:
    """记录每次调用，可指定哪些连接会失败。"""

    def __init__(self, *, fail: set[str] | None = None, raise_on: set[str] | None = None):
        self.fail = fail or set()
        self.raise_on = raise_on or set()
        self.calls: list[dict[str, Any]] = []

    def __call__(self, conn_id, *, action, params, timeout_seconds=60.0):
        self.calls.append({"conn": conn_id, "action": action,
                           "params": params, "timeout": timeout_seconds})
        if conn_id in self.raise_on:
            raise RuntimeError(f"{conn_id} 炸了")
        if conn_id in self.fail:
            return {"result": {"ok": False, "error": f"{conn_id} 拒绝服务"}}
        return {"result": {"ok": True, "output": {"text": f"{conn_id} 的产出",
                                                  "model": "m"}}}


# --------------------------------------------------------------------------- #
# 诚实原则
# --------------------------------------------------------------------------- #


def test_no_match_does_not_guess() -> None:
    """匹配不上就报失败，绝不随便挑一个 Agent 凑数。"""
    orch = Orchestrator(invoke=_FakeInvoke(), route=_FakeRouter({}).route)
    res = orch.run_sequential("任务", ["第一步"])
    assert not res.ok
    assert "没有 Agent 能处理" in res.error


def test_partial_failure_is_recorded_not_swallowed() -> None:
    """单步失败要出现在 failed 里，且整体标失败。"""
    router = _FakeRouter({"写": [_cand("good", "chat")],
                          "审": [_cand("bad", "chat")]})
    inv = _FakeInvoke(fail={"bad"})
    orch = Orchestrator(invoke=inv, route=router.route)
    res = orch.run_sequential("任务", ["写初稿", "审校"])
    assert not res.ok
    assert len(res.failed) == 1
    assert res.failed[0].connection_id == "bad"
    assert "拒绝服务" in res.failed[0].error


def test_exception_from_invoke_is_caught() -> None:
    """invoke 抛异常不该让整个编排崩掉——要变成一条失败记录。"""
    router = _FakeRouter({"写": [_cand("boom", "chat")]})
    inv = _FakeInvoke(raise_on={"boom"})
    orch = Orchestrator(invoke=inv, route=router.route)
    res = orch.run_sequential("任务", ["写"])
    assert not res.ok
    assert "RuntimeError" in res.error


def test_failed_step_stops_sequential() -> None:
    """一步失败后不应继续跑——后续步骤以上文为前提，硬跑只会产出垃圾。"""
    router = _FakeRouter({"写": [_cand("bad", "chat")]})
    inv = _FakeInvoke(fail={"bad"})
    orch = Orchestrator(invoke=inv, route=router.route)
    res = orch.run_sequential("任务", ["写", "改", "润色"])
    assert len(res.steps) == 1
    assert "第 1 步" in res.error


def test_empty_parallel_aspects_rejected() -> None:
    orch = Orchestrator(invoke=_FakeInvoke(), route=_FakeRouter({}).route)
    res = orch.run_parallel("任务", [])
    assert not res.ok
    assert "侧面" in res.error


# --------------------------------------------------------------------------- #
# 上下文传递（多轮 messages 的实际用处）
# --------------------------------------------------------------------------- #


def test_sequential_passes_previous_output_as_context() -> None:
    """第二步的 messages 里必须含第一步的产出——这是协同的核心。"""
    router = _FakeRouter({})  # 任何 hint 都匹配不到，走显式指定
    inv = _FakeInvoke()
    orch = Orchestrator(invoke=inv, route=router.route)
    res = orch.run_pipeline([
        {"connection_id": "a", "capability": "chat", "step": "起草", "instruction": "写初稿"},
        {"connection_id": "b", "capability": "chat", "step": "润色", "instruction": "润色"},
    ])
    assert res.ok, res.error
    second = inv.calls[1]["params"]
    assert "messages" in second, "第二步应带多轮上下文"
    roles = [m["role"] for m in second["messages"]]
    assert roles == ["assistant", "user"], f"上下文结构不对：{roles}"
    assert "a 的产出" in second["messages"][0]["content"]
    assert second["messages"][-1]["content"] == "润色"


def test_first_step_has_no_context() -> None:
    """第一步没有上文，应走单轮 prompt 而不是空 messages。"""
    inv = _FakeInvoke()
    orch = Orchestrator(invoke=inv, route=_FakeRouter({}).route)
    orch.run_pipeline([{"connection_id": "a", "capability": "chat",
                        "step": "起草", "instruction": "写"}])
    assert "prompt" in inv.calls[0]["params"]
    assert "messages" not in inv.calls[0]["params"]


def test_parallel_aspects_do_not_share_context() -> None:
    """并行侧面互不知情——共享上下文会互相污染结论。"""
    # 两个连接：修「并行全落同一Agent」后，侧面会被分散到不同连接。
    router = _FakeRouter({"角度": [_cand("c1", "chat"), _cand("c2", "chat")]})
    inv = _FakeInvoke()
    orch = Orchestrator(invoke=inv, route=router.route)
    res = orch.run_parallel("评估 X", ["技术角度", "经济角度"], max_workers=2)
    assert res.ok, res.error
    assert len(inv.calls) == 2
    for call in inv.calls:
        assert "messages" not in call["params"], "并行步骤不该带上下文"


def test_parallel_uses_task_plus_aspect() -> None:
    """每个侧面收到「任务 + 自己的角度」，不是只有角度。"""
    router = _FakeRouter({"角度": [_cand("c1", "chat")]})
    inv = _FakeInvoke()
    orch = Orchestrator(invoke=inv, route=router.route)
    orch.run_parallel("评估 X", ["技术角度"], max_workers=1)
    prompt = inv.calls[0]["params"]["prompt"]
    assert "评估 X" in prompt
    assert "技术角度" in prompt


def test_parallel_survives_partial_failure() -> None:
    """一个侧面失败，其余仍应成功（部分结果也有价值）。"""
    router = _FakeRouter({"A": [_cand("ok1", "chat")],
                          "B": [_cand("bad", "chat")],
                          "C": [_cand("ok2", "chat")]})
    inv = _FakeInvoke(fail={"bad"})
    orch = Orchestrator(invoke=inv, route=router.route)
    res = orch.run_parallel("任务", ["A", "B", "C"], max_workers=3)
    assert res.ok, "有侧面成功就该算可用"
    assert len(res.succeeded) == 2
    assert len(res.failed) == 1
    assert "1/3 个侧面失败" in res.error


def test_parallel_results_keep_input_order() -> None:
    """结果顺序必须与输入一致，否则汇总时张冠李戴。"""
    def route(hint, top_k=5, kind=None):
        pick = "a" if "A" in hint else "b"
        return [_cand(pick, "chat")]
    inv = _FakeInvoke()
    orch = Orchestrator(invoke=inv, route=route)
    res = orch.run_parallel("任务", ["A", "B"], max_workers=2)
    assert [s.index for s in res.steps] == [0, 1]
    assert res.steps[0].step == "A"


# --------------------------------------------------------------------------- #
# pipeline 显式指定
# --------------------------------------------------------------------------- #


def test_pipeline_bypasses_router() -> None:
    """显式指定时不该走路由——路由打分未必符合意图。"""
    router = _FakeRouter({})  # 路由必然匹配不到
    inv = _FakeInvoke()
    orch = Orchestrator(invoke=inv, route=router.route)
    res = orch.run_pipeline([{"connection_id": "explicit", "capability": "chat",
                              "instruction": "直接干活"}])
    assert res.ok, res.error
    assert router.calls == [], "显式指定时不该调路由"
    assert inv.calls[0]["conn"] == "explicit"


def test_pipeline_failure_keeps_earlier_steps() -> None:
    """中途失败时，已成功的步骤产出不能丢。"""
    inv = _FakeInvoke(fail={"b"})
    orch = Orchestrator(invoke=inv, route=_FakeRouter({}).route)
    res = orch.run_pipeline([
        {"connection_id": "a", "capability": "chat", "step": "一", "instruction": "x"},
        {"connection_id": "b", "capability": "chat", "step": "二", "instruction": "y"},
    ])
    assert not res.ok
    assert len(res.steps) == 2
    assert res.steps[0].ok
    assert not res.steps[1].ok


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


def test_summarize_merges_all_successful_outputs() -> None:
    # 路由键要按hint 区分：「总结…」->汇总器；「任务…」-> 两个执行者。
    # 注意 _FakeRouter 按 dict 顺序匹配，所以「总结」要写在前面。
    class _R:
        def route(self, hint, top_k=5, kind=None):
            if "总结" in hint:
                return [_cand("s", "chat")]
            return [_cand("c1", "chat"), _cand("c2", "chat")]

    inv = _FakeInvoke()
    orch = Orchestrator(invoke=inv, route=_R().route)
    base = orch.run_parallel("任务", ["角度A", "角度B"], max_workers=2)
    assert base.ok, base.error
    assert len(base.succeeded) == 2
    # 显式传指令，让它与路由表的键对上——默认指令里没有「总结」二字。
    merged = orch.summarize(base, instruction="总结这些侧面。")
    assert merged.ok, merged.error
    assert merged.mode.endswith("+summarize")
    body = inv.calls[-1]["params"]["prompt"]
    assert "角度A" in body and "角度B" in body, "汇总应看到各侧面产出"


def test_summarize_without_agent_returns_original() -> None:
    """没有汇总 Agent 时如实返回原结果，不硬造「总结」。"""
    router = _FakeRouter({})  # 匹配不到汇总 Agent
    inv = _FakeInvoke()
    orch = Orchestrator(invoke=inv, route=router.route)
    base = orch.run_parallel("任务", ["A"], max_workers=1)
    merged = orch.summarize(base)
    assert len(merged.steps) == len(base.steps), "不该凭空多出一步"
    assert merged.mode == "parallel"


def test_summarize_failure_keeps_steps_and_reports() -> None:
    """汇总失败要如实报，且不抹掉已成功的步骤。"""
    router = _FakeRouter({"总结": [_cand("bad", "chat")],
                          "任务": [_cand("good", "chat")]})
    inv = _FakeInvoke(fail={"bad"})
    orch = Orchestrator(invoke=inv, route=router.route)
    base = orch.run_parallel("任务", ["角度A"], max_workers=1)
    assert base.ok, base.error
    merged = orch.summarize(base)
    assert "汇总失败" in merged.error
    assert "已保留各步产出" in merged.error
    assert base.steps[0].ok


def test_summarize_on_empty_result_is_noop() -> None:
    orch = Orchestrator(invoke=_FakeInvoke(), route=_FakeRouter({}).route)
    empty = orch.run_parallel("任务", [], max_workers=1)
    assert orch.summarize(empty) is empty


# --------------------------------------------------------------------------- #
# _as_text：把各种输出压成可拼上下文的文本
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw,expected", [
    (None, ""),
    ("直接字符串", "直接字符串"),
    ({"text": "回复"}, "回复"),
    ({"content": "内容"}, "内容"),
    ({"text": ""}, ""),
    ({}, ""),
    ({"data": {"text": "嵌套"}}, "嵌套"),
    ([{"text": "一"}, {"text": "二"}], "一\n二"),
    (42, "42"),
])
def test_as_text(raw: Any, expected: str) -> None:
    assert _as_text(raw) == expected


def test_to_public_reports_counts() -> None:
    router = _FakeRouter({"写": [_cand("good", "chat")],
                          "审": [_cand("bad", "chat")]})
    orch = Orchestrator(invoke=_FakeInvoke(fail={"bad"}), route=router.route)
    res = orch.run_sequential("任务", ["写", "审"])
    data = res.to_public()
    assert data["step_count"] == 2
    assert data["succeeded_count"] == 1
    assert data["failed_count"] == 1
    assert data["mode"] == "sequential"


def test_max_workers_is_clamped() -> None:
    """并发上限被夹到 1..8，防止把免费额度打爆或退化成串行。"""
    assert Orchestrator(invoke=_FakeInvoke(), route=_FakeRouter({}).route,
                        max_workers=99)._max_workers == 8
    assert Orchestrator(invoke=_FakeInvoke(), route=_FakeRouter({}).route,
                        max_workers=0)._max_workers == 1