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

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from find_yourself.services.hub.connections import HubService
from find_yourself.services.hub.orchestrator import (
    DEFAULT_MAX_WORKERS,
    Orchestrator,
    _as_text,
)
from find_yourself.services.hub.router import CapabilityRouter


def _sig_shape(fn: Any) -> list[tuple[str, str]]:
    """把可调用对象的签名压成 ``[(参数名, 参数种类), ...]``（去掉 ``self``）。

    为什么必须连「种类」一起比：``list(signature.parameters)`` 只给名字，
    丢掉``*`` 标记（keyword-only）。本轮实测把mock 的 ``route`` 从
    ``def route(self, hint, *, top_k=5, ...)`` 放宽成
    ``def route(self, hint, top_k=5, ...)``，只比名字的守卫**照样绿**。

    种类用 ``Parameter.kind.name``（如 ``POSITIONAL_OR_KEYWORD`` /
    ``KEYWORD_ONLY``）而不是枚举本身 —— 字符串在断言消息里可读，且不把
    ``inspect`` 的内部表示绑成硬依赖。
    """
    params = list(inspect.signature(fn).parameters.values())
    if params and params[0].name in {"self", "cls"}:
        params = params[1:]
    return [(p.name, p.kind.name) for p in params]

# --------------------------------------------------------------------------- #
# 契约守卫：本文件最该防住的东西
# --------------------------------------------------------------------------- #
# 历史真相：这个文件曾用 `_FakeInvoke.__call__(self, conn_id, *, action, params,
# timeout_seconds=60.0)` —— 一个**按调用方期望伪造**的签名，与真实的
# `HubService.invoke(self, actor, conn_id, *, ...)` 差一个 actor 位。321 行、20 条
# 用例全绿，却完全无法证伪真实集成：真实接线必然抛
# `TypeError: invoke() missing 1 required positional argument: 'conn_id'`。
#
# 更深一层：mock 当时连**形态**都不是Hub —— 它只实现了 `__call__`，没有
# `.invoke` 方法。而真实的 ``_bind_invoke`` 调的是 ``hub.invoke(...)``，
# 所以一旦换成真实接线就抛 ``AttributeError: ... has no attribute 'invoke'``。
# 换句话说，它既不是 Hub 的形状，也没有 Hub 的签名，却因为「按调用方期望」
# 而永远「一致」。
#
# 下面的守卫把「真实签名」钉成不可协商的基准：mock 必须逐参数复刻真实签名，
# 一旦真实接口改了而 mock 没跟上，这里立刻红，而不是让生产炸。
#
# ⚠️ 比对**参数名**不够，必须连**参数种类**（positional-or-keyword /
# keyword-only）一起比。这是实测证伪出来的：早期版本只比
# `list(signature.parameters)`（纯名字列表），把 mock 的 `route` 从
# `def route(self, hint, *, top_k=5, ...)` 改成允许位置传参的
# `def route(self, hint, top_k=5, ...)` 后守卫**仍然绿** —— 因为两种写法的
# 参数名列表一模一样，只有 kinds 不同。少比这一项，守卫就漏了半个签名。

#: 逐参数 ``(名字, 种类)`` 序列。去掉 ``self`` 后与真实方法对齐。
_INVOKE_PARAMS = _sig_shape(HubService.invoke)
_ROUTE_PARAMS = _sig_shape(CapabilityRouter.route)


def test_fake_invoke_matches_real_hub_invoke_signature() -> None:
    """mock 的 invoke 签名必须与真实 ``HubService.invoke`` 逐参数一致。

    这是本文件存在的前提。少了它，任何「改真实接口忘了改 mock」的错位都会
    再次被20 条绿灯掩盖。

    注意比对的是 ``_FakeInvoke.invoke``（**不是** ``__call__``）：桩必须是 Hub 形态，
    ``_bind_invoke`` 内部走的是 ``hub.invoke(...)``。只实现 ``__call__`` 的桩
    在真实接线上会抛 ``AttributeError: ... has no attribute 'invoke'``。

    比对的是 (名字, 种类) 而非纯名字 —— 见 ``_sig_shape`` 的说明。
    """
    fake = _sig_shape(_FakeInvoke.invoke)
    assert fake == _INVOKE_PARAMS, (
        f"_FakeInvoke.invoke 签名与真实 HubService.invoke 不一致：\n"
        f"  mock = {fake}\n  real = {_INVOKE_PARAMS}"
    )
    assert fake[0][0] == "actor", (
        f"actor 必须是第一个位置参数——它就是被漏掉的那个参数；实际 {fake[0]}"
    )
    assert fake[1][0] == "conn_id", f"第二个位置参数必须是 conn_id；实际 {fake[1]}"


def test_fake_invoke_is_hub_shaped_not_callable() -> None:
    """桩必须**带 ``.invoke`` 方法**，不能只靠 ``__call__``。

    这是本轮实测踩出来的第二个假绿：把桩写成可调用对象后，单测里 12 条直接崩在
    ``AttributeError: '_FakeInvoke' object has no attribute 'invoke'`` —— 因为真实
    的 ``_bind_invoke`` 调的是 ``hub.invoke(...)``。反过来，如果有人「顺手」把生产
    代码改成 ``hub(...)`` 让桩能过，这条守卫就是唯一的拦阻。
    """
    hub = _FakeInvoke()
    assert callable(getattr(hub, "invoke", None)), (
        "桩必须是 Hub 形态：带可调用的 .invoke 方法"
    )


def test_every_orchestrator_is_built_via_bind() -> None:
    """本文件**不允许**出现裸 ``Orchestrator(...)`` 构造。

    生产接线唯一正确入口是 ``Orchestrator.bind()``（它内部把 actor 绑进闭包）。
    裸构造要么漏 actor（运行时错位炸），要么需要调用方自己手工绑 —— 后者就是
    「签名错位」的源头。

    为什么需要这条守卫：上面的签名守卫只能保证「桩的形状对」，保证不了
    「用例真的走了 bind」。两条合起来才封死整条错位路径。

    用**AST** 而不是文本扫描：文本扫描会被 docstring 和注释里的
    ``Orchestrator(...)`` 字面量误伤（本文件自己就有一堆），那样的守卫必然
    被人加白名单绕过 —— 加了白名单就等于没守。AST 只看真实调用表达式。
    """
    tree = ast.parse(Path(inspect.getfile(_FakeInvoke)).read_text(encoding="utf-8"))
    bare = [
        f"第 {node.lineno} 行"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Orchestrator"
    ]
    assert not bare, (
        "本文件出现裸 Orchestrator(...) 构造，必须改用 _orch(...) 工厂：\n"
        + "\n".join(bare)
    )


def test_orch_factory_binds_actor_so_conn_id_lands_right() -> None:
    """``_orch`` 工厂必须真的把 actor 绑住——这是它存在的全部意义。

    桩的 ``calls`` 里记了 ``actor``。逐条断言它等于工厂传进去的 owner，
    这样「actor 漏绑/绑错」会立刻在断言里现形，而不是退化成运行时的
    ``missing 1 required positional argument: 'conn_id'``。
    """
    inv = _FakeInvoke()
    orch = _orch(inv, _FakeRouter({"写": [_cand("good", "chat")]}))
    res = orch.run_sequential("任务", ["写初稿"])
    assert res.ok, res.error
    assert len(inv.calls) == 1
    assert inv.calls[0]["actor"] == "owner-test", (
        f"actor 应被闭包绑住，实际={inv.calls[0]['actor']!r}"
    )
    assert inv.calls[0]["conn"] == "good"


def test_fake_router_matches_real_capability_router_signature() -> None:
    """mock 的 route 签名也要对齐真实 ``CapabilityRouter.route``（keyword-only）。

    早先的 ``route(self, hint, top_k=5, kind=None)`` 允许位置传参，与真实签名不符，
    巧合能过只因为 ``_pick`` 传的是关键字。留着这个宽松口子，真实签名一旦改成
    位置参数，测试就会以「能过」的方式掩盖差异。

    ⚠️ 这条守卫**曾经是无效的**：早期只比参数名列表，而「删掉 ``*``」不改变名字
    列表，所以把 mock 改回位置传参形态它照样绿。是靠「注入缺陷 → 必须变红」的
    证伪实验才发现的 —— 现在比对 (名字, 种类)，见 ``_sig_shape``。
    """
    fake = _sig_shape(_FakeRouter.route)
    assert fake == _ROUTE_PARAMS, (
        f"_FakeRouter.route 签名与真实 CapabilityRouter.route 不一致：\n"
        f"  mock = {fake}\n  real = {_ROUTE_PARAMS}"
    )


def test_production_wiring_is_the_only_correct_entry() -> None:
    """生产接线必须走 ``Orchestrator.bind``，不能直接注入 ``hub.invoke``。

    这条从「文档提醒」升级为可执行断言：直接注入会让 conn_id 错位到 actor 上，
    运行时必炸。绑定后 actor 被闭包预先捕获，编排器看到的才是真实调用面。
    """
    from find_yourself.services.hub.orchestrator import _bind_invoke

    class _Hub:
        def invoke(self, actor, conn_id, *, action, params=None, timeout_seconds=15.0):
            return {"result": {"actor": actor, "conn_id": conn_id}}

    # 错位铁证：按编排器的方式直接调 hub.invoke 会抛 TypeError
    with pytest.raises(TypeError):
        _Hub().invoke("conn-1", action="chat", params={}, timeout_seconds=5.0)

    # 正确路径：bind 之后 actor 已绑好，conn_id 落在正确位置
    bound = _bind_invoke(_Hub(), "owner-1")
    inner = bound("conn-1", action="chat", params={}, timeout_seconds=5.0)["result"]
    assert inner == {"actor": "owner-1", "conn_id": "conn-1"}


class _FakeRouter:
    """按 hint 里的关键词返回固定候选，模拟确定性路由。

    签名逐参数复刻 ``CapabilityRouter.route``（``hint`` 之后全部 keyword-only）——
    见本文件顶部的契约守卫。
    """

    def __init__(self, table: dict[str, list[dict[str, Any]]]):
        self.table = table
        self.calls: list[str] = []

    def route(self, hint: str, *, top_k: int = 5, kind: str | None = None,
              include_unhealthy: bool = False) -> list[dict[str, Any]]:
        self.calls.append(hint)
        for key, cands in self.table.items():
            if key in hint:
                return cands[:top_k]
        return []


def _cand(conn: str, cap: str, name: str = "") -> dict[str, Any]:
    return {"connection_id": conn, "connection_name": name or conn,
            "capability": type("C", (), {"name": cap})()}


class _FakeInvoke:
    """**Hub 形态**的调用桩：带 ``.invoke`` 方法，记录每次调用，可指定哪些连接会失败。

    ⚠️ 这里最容易犯的错是把它写成一个「可调用对象」（只实现 ``__call__``）。
    那样做测试会「看起来能过」，但它**不是 HubService 的形态**：真实的
    ``_bind_invoke`` 内部调的是 ``hub.invoke(...)``，要求第一个实参是**带
    ``.invoke`` 方法的对象**。只实现 ``__call__`` 的桩会在运行时报
    ``AttributeError: '_FakeInvoke' object has no attribute 'invoke'``。

    第二个坑是签名。真实签名是
    ``HubService.invoke(self, actor, conn_id, *, action, params, timeout_seconds,
    transport, sleep)`` —— **actor 是第一个位置参数**。历史上这里写成
    ``__call__(self, conn_id, *, action, params, timeout_seconds=60.0)``，
    是**按调用方期望伪造**的签名，于是 20 条用例全绿却完全无法证伪真实集成
    （真实接线必然抛 ``TypeError: missing 1 required positional argument: 'conn_id'``）。

    改动此方法前先读本文件顶部的「契约守卫」。
    """

    def __init__(self, *, fail: set[str] | None = None, raise_on: set[str] | None = None):
        self.fail = fail or set()
        self.raise_on = raise_on or set()
        self.calls: list[dict[str, Any]] = []

    def invoke(self, actor: Any, conn_id: str, *, action: str,
               params: dict[str, Any] | None = None, timeout_seconds: float = 15.0,
               transport: Any = None, sleep: Any = None) -> dict[str, Any]:
        self.calls.append({"actor": actor, "conn": conn_id, "action": action,
                           "params": params, "timeout": timeout_seconds})
        if conn_id in self.raise_on:
            raise RuntimeError(f"{conn_id} 炸了")
        if conn_id in self.fail:
            return {"result": {"ok": False, "error": f"{conn_id} 拒绝服务"}}
        return {"result": {"ok": True, "output": {"text": f"{conn_id} 的产出",
                                                  "model": "m"}}}


def _orch(hub: _FakeInvoke, router: Any, *, max_workers: int = DEFAULT_MAX_WORKERS
          ) -> Orchestrator:
    """走**生产接线唯一正确入口** ``Orchestrator.bind`` 造编排器。

    为什么不能直接 ``Orchestrator(invoke=hub.invoke, route=router.route)``：
    真实 ``HubService.invoke`` 的 actor 在**第一位**，而编排器内部按
    ``invoke(conn_id, *, action, ...)`` 调用。直接注入会让 conn_id 错位到 actor 上，
    运行时必炸。``bind()`` 内部的 ``_bind_invoke`` 用闭包把 actor 预先绑好，
    编排器才看到正确的调用面。

    本文件**所有**用例都必须经这个工厂 —— 让「签名错位」这一类 bug 在单测里
    就炸掉，而不是等到生产接线时才炸。

    参数 ``router`` 是**带 ``.route 方法的对象**``（对应 ``bind(router=...)``），
    不是裸函数。历史上传裸函数/方法，靠巧合也能跑通，但那样就绕开了
    ``bind`` 的真实调用面。
    """
    return Orchestrator.bind(hub, actor="owner-test", router=router,
                             max_workers=max_workers)


# --------------------------------------------------------------------------- #
# 诚实原则
# --------------------------------------------------------------------------- #


def test_no_match_does_not_guess() -> None:
    """匹配不上就报失败，绝不随便挑一个 Agent 凑数。"""
    orch = _orch(_FakeInvoke(), _FakeRouter({}))
    res = orch.run_sequential("任务", ["第一步"])
    assert not res.ok
    assert "没有 Agent 能处理" in res.error


def test_partial_failure_is_recorded_not_swallowed() -> None:
    """单步失败要出现在 failed 里，且整体标失败。"""
    router = _FakeRouter({"写": [_cand("good", "chat")],
                          "审": [_cand("bad", "chat")]})
    inv = _FakeInvoke(fail={"bad"})
    orch = _orch(inv, router)
    res = orch.run_sequential("任务", ["写初稿", "审校"])
    assert not res.ok
    assert len(res.failed) == 1
    assert res.failed[0].connection_id == "bad"
    assert "拒绝服务" in res.failed[0].error


def test_exception_from_invoke_is_caught() -> None:
    """invoke 抛异常不该让整个编排崩掉——要变成一条失败记录。"""
    router = _FakeRouter({"写": [_cand("boom", "chat")]})
    inv = _FakeInvoke(raise_on={"boom"})
    orch = _orch(inv, router)
    res = orch.run_sequential("任务", ["写"])
    assert not res.ok
    assert "RuntimeError" in res.error


def test_failed_step_stops_sequential() -> None:
    """一步失败后不应继续跑——后续步骤以上文为前提，硬跑只会产出垃圾。"""
    router = _FakeRouter({"写": [_cand("bad", "chat")]})
    inv = _FakeInvoke(fail={"bad"})
    orch = _orch(inv, router)
    res = orch.run_sequential("任务", ["写", "改", "润色"])
    assert len(res.steps) == 1
    assert "第 1 步" in res.error


def test_empty_parallel_aspects_rejected() -> None:
    orch = _orch(_FakeInvoke(), _FakeRouter({}))
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
    orch = _orch(inv, router)
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
    orch = _orch(inv, _FakeRouter({}))
    orch.run_pipeline([{"connection_id": "a", "capability": "chat",
                        "step": "起草", "instruction": "写"}])
    assert "prompt" in inv.calls[0]["params"]
    assert "messages" not in inv.calls[0]["params"]


def test_parallel_aspects_do_not_share_context() -> None:
    """并行侧面互不知情——共享上下文会互相污染结论。"""
    # 两个连接：修「并行全落同一Agent」后，侧面会被分散到不同连接。
    router = _FakeRouter({"角度": [_cand("c1", "chat"), _cand("c2", "chat")]})
    inv = _FakeInvoke()
    orch = _orch(inv, router)
    res = orch.run_parallel("评估 X", ["技术角度", "经济角度"], max_workers=2)
    assert res.ok, res.error
    assert len(inv.calls) == 2
    for call in inv.calls:
        assert "messages" not in call["params"], "并行步骤不该带上下文"


def test_parallel_uses_task_plus_aspect() -> None:
    """每个侧面收到「任务 + 自己的角度」，不是只有角度。"""
    router = _FakeRouter({"角度": [_cand("c1", "chat")]})
    inv = _FakeInvoke()
    orch = _orch(inv, router)
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
    orch = _orch(inv, router)
    res = orch.run_parallel("任务", ["A", "B", "C"], max_workers=3)
    assert res.ok, "有侧面成功就该算可用"
    assert len(res.succeeded) == 2
    assert len(res.failed) == 1
    assert "1/3 个侧面失败" in res.error


def test_parallel_results_keep_input_order() -> None:
    """结果顺序必须与输入一致，否则汇总时张冠李戴。"""
    class _Route:
        def route(self, hint, *, top_k=5, kind=None, include_unhealthy=False):
            pick = "a" if "A" in hint else "b"
            return [_cand(pick, "chat")]
    inv = _FakeInvoke()
    orch = _orch(inv, _Route())
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
    orch = _orch(inv, router)
    res = orch.run_pipeline([{"connection_id": "explicit", "capability": "chat",
                              "instruction": "直接干活"}])
    assert res.ok, res.error
    assert router.calls == [], "显式指定时不该调路由"
    assert inv.calls[0]["conn"] == "explicit"


def test_pipeline_failure_keeps_earlier_steps() -> None:
    """中途失败时，已成功的步骤产出不能丢。"""
    inv = _FakeInvoke(fail={"b"})
    orch = _orch(inv, _FakeRouter({}))
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
        def route(self, hint, *, top_k=5, kind=None, include_unhealthy=False):
            if "总结" in hint:
                return [_cand("s", "chat")]
            return [_cand("c1", "chat"), _cand("c2", "chat")]

    inv = _FakeInvoke()
    orch = _orch(inv, _R())
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
    orch = _orch(inv, router)
    base = orch.run_parallel("任务", ["A"], max_workers=1)
    merged = orch.summarize(base)
    assert len(merged.steps) == len(base.steps), "不该凭空多出一步"
    assert merged.mode == "parallel"


def test_summarize_failure_keeps_steps_and_reports() -> None:
    """汇总失败要如实报，且不抹掉已成功的步骤。"""
    router = _FakeRouter({"总结": [_cand("bad", "chat")],
                          "任务": [_cand("good", "chat")]})
    inv = _FakeInvoke(fail={"bad"})
    orch = _orch(inv, router)
    base = orch.run_parallel("任务", ["角度A"], max_workers=1)
    assert base.ok, base.error
    merged = orch.summarize(base)
    assert "汇总失败" in merged.error
    assert "已保留各步产出" in merged.error
    assert base.steps[0].ok


def test_summarize_on_empty_result_is_noop() -> None:
    orch = _orch(_FakeInvoke(), _FakeRouter({}))
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
    orch = _orch(_FakeInvoke(fail={"bad"}), router)
    res = orch.run_sequential("任务", ["写", "审"])
    data = res.to_public()
    assert data["step_count"] == 2
    assert data["succeeded_count"] == 1
    assert data["failed_count"] == 1
    assert data["mode"] == "sequential"


def test_max_workers_is_clamped() -> None:
    """并发上限被夹到 1..8，防止把免费额度打爆或退化成串行。"""
    assert _orch(_FakeInvoke(), _FakeRouter({}), max_workers=99)._max_workers == 8
    assert _orch(_FakeInvoke(), _FakeRouter({}), max_workers=0)._max_workers == 1
