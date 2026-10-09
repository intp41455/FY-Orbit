"""补测试：守住端到端暴露的三个缺陷不复发。

三个缺陷（都是真实跑出来的，不是假想）
----------------------------------------
A. 显式指定 mcp 连接时无脑用 action='invoke'，而 mcp 的 call_tool 读
   ``params["name"]``（工具名）—— 实测 ``{prompt, tool, arguments}`` 失败、
   ``{name, arguments}`` 成功，所以 ``tool`` 键无效，必须映射成 ``name``。
B. 路由打平时并行侧面会全落同一个 Agent（实测三个候选同为 4.5 分），
   「多 Agent 协同」退化成单 Agent 重复调用。
C. ``_pick`` 排除已用连接后，若候选全被排除，错误信息必须区分
   「都被占用」与「没匹配上」——两者对调用方意义完全不同。
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from find_yourself.services.hub.connections import HubService
from find_yourself.services.hub.orchestrator import Orchestrator, _tool_name
from find_yourself.services.hub.router import CapabilityRouter


def _sig_shape(fn: Any) -> list[tuple[str, str]]:
    """把签名压成 ``[(参数名, 参数种类), ...]``（去掉 ``self``/``cls``）。

    必须连**种类**一起比：``list(signature.parameters)`` 只给名字、丢掉 ``*`` 标记。
    本轮实测里，只比名字的守卫对「删掉 ``*`` 让keyword-only 退化成位置传参」
    完全无感（照样绿）。种类用 ``Parameter.kind.name``，字符串在断言消息里可读。
    """
    params = list(inspect.signature(fn).parameters.values())
    if params and params[0].name in {"self", "cls"}:
        params = params[1:]
    return [(p.name, p.kind.name) for p in params]


def test_mocks_match_real_hub_signatures() -> None:
    """两个桩的签名都必须与真实方法逐参数（名+ 种类）一致。

    本文件曾与 ``test_orchestrator.py`` 犯同一个错：桩签名**按调用方期望伪造**
    （漏 ``actor``、``route`` 允许位置传参），用例全绿却证伪不了真实接线。
    这里把真实签名钉成基准 —— 真实接口改了而桩没跟上，立刻红。
    """
    assert _sig_shape(_Invoke.invoke) == _sig_shape(HubService.invoke), (
        "_Invoke.invoke 与真实 HubService.invoke 不一致："
        f"{_sig_shape(_Invoke.invoke)} vs {_sig_shape(HubService.invoke)}"
    )
    assert _sig_shape(_Router.route) == _sig_shape(CapabilityRouter.route), (
        "_Router.route 与真实 CapabilityRouter.route 不一致："
        f"{_sig_shape(_Router.route)} vs {_sig_shape(CapabilityRouter.route)}"
    )


def test_invoke_stub_is_hub_shaped_with_actor_bound() -> None:
    """桩必须带 ``.invoke``，且 ``_orch`` 真的把 actor 绑住了。

    只实现 ``__call__`` 的桩在真实 ``_bind_invoke`` 下会抛
    ``AttributeError: ... has no attribute 'invoke'``；actor 没绑住则 conn_id
    错位。两件事都在这里钉死。
    """
    inv = _Invoke()
    assert callable(getattr(inv, "invoke", None)), "桩必须是 Hub 形态：带 .invoke"
    _orch(inv, _Router([[]])).run_pipeline(
        [{"connection_id": "m", "tool": "add", "instruction": "算"}]
    )
    assert inv.calls[0]["actor"] == "owner-test", (
        f"actor 应被闭包绑住，实际={inv.calls[0]['actor']!r}"
    )
    assert inv.calls[0]["conn"] == "m"


class _Router:
    """可配置「同分候选」的路由桩，模拟实测中三个候选同为 4.5 的情形。

    签名逐参数复刻 ``CapabilityRouter.route``（``hint`` 之后全部keyword-only）。
    原先写成 ``route(self, hint, top_k=5, kind=None)`` —— 允许位置传参、与真实
    签名不符，只因 ``_pick`` 传的是关键字才巧合能过。
    """

    def __init__(self, cands_per_call: list[list[dict[str, Any]]]):
        self.cands_per_call = cands_per_call
        self.calls = 0
        self.seen_hints: list[str] = []

    def route(self, hint: str, *, top_k: int = 5, kind: str | None = None,
              include_unhealthy: bool = False) -> list[dict[str, Any]]:
        self.seen_hints.append(hint)
        idx = min(self.calls, len(self.cands_per_call) - 1)
        self.calls += 1
        return self.cands_per_call[idx]


class _Invoke:
    """**Hub 形态**的调用桩：带 ``.invoke`` 方法，记录每次调用。

    ⚠️ 两个坑，改之前先读：

    1. **形态**：必须是带 ``.invoke`` 的**对象**，不能只实现 ``__call__``。
       真实的 ``_bind_invoke`` 内部调的是 ``hub.invoke(...)``，只靠 ``__call__``
       的桩会抛 ``AttributeError: ... has no attribute 'invoke'``。
    2. **签名**：真实 ``HubService.invoke(self, actor, conn_id, *, action, params,
       timeout_seconds, transport, sleep)`` —— **actor 是第一个位置参数**。
       原先这里是 ``__call__(self, conn_id, *, action, params, timeout_seconds=60.0)``，
       签名**按调用方期望伪造**，于是全部用例能过却完全无法证伪真实集成。

    本文件的桩与 ``test_orchestrator.py`` 的桩是同一个错误的两个副本，
    修的时候必须一起改，否则「改了一处、另一处继续绿」。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, actor: Any, conn_id: str, *, action: str,
               params: dict[str, Any] | None = None, timeout_seconds: float = 15.0,
               transport: Any = None, sleep: Any = None) -> dict[str, Any]:
        self.calls.append({"actor": actor, "conn": conn_id, "action": action,
                           "params": params, "timeout": timeout_seconds})
        return {"result": {"ok": True, "output": {"text": f"{conn_id}输出"}}}


def _orch(hub: _Invoke, router: _Router) -> Orchestrator:
    """走生产接线唯一正确入口 ``Orchestrator.bind``。

    直接 ``Orchestrator(invoke=hub.invoke, route=router.route)`` 会让 conn_id
    错位到 actor 上（真实 ``invoke`` 的 actor 在第一位，而编排器按 conn_id 第一个
    实参调用），运行时必炸。详见 ``test_orchestrator.py`` 的 ``_orch``。
    """
    return Orchestrator.bind(hub, actor="owner-test", router=router)


def _cand(conn: str, cap: str) -> dict[str, Any]:
    return {"connection_id": conn, "connection_name": conn,
            "capability": type("C", (), {"name": cap})()}


# --------------------------------------------------------------------------- #
# 缺陷 A：tool 名映射到 params["name"]
# --------------------------------------------------------------------------- #


def test_tool_name_maps_to_params_name() -> None:
    """mcp 类连接的 tool 必须落到 params['name']——实测只有 name 键有效。"""
    inv = _Invoke()
    orch = _orch(inv, _Router([[]]))
    orch.run_pipeline([{"connection_id": "m", "tool": "add",
                        "instruction": "算一下"}])
    assert inv.calls[0]["params"]["name"] == "add"


def test_pipeline_passes_tool_through() -> None:
    inv = _Invoke()
    orch = _orch(inv, _Router([[]]))
    orch.run_pipeline([{"connection_id": "m", "tool": "echo",
                        "instruction": "回显"}])
    assert inv.calls[0]["params"]["name"] == "echo"


def test_tool_survives_multiturn_context() -> None:
    """多轮时也要带工具名——不能因为换成 messages 就丢掉。"""
    inv = _Invoke()
    orch = _orch(inv, _Router([[]]))
    orch.run_pipeline([
        {"connection_id": "a", "instruction": "第一步"},
        {"connection_id": "m", "tool": "add", "instruction": "第二步"},
    ])
    assert inv.calls[1]["params"].get("name") == "add"
    assert "messages" in inv.calls[1]["params"]


def test_tool_name_extraction() -> None:
    """从路由能力名里取工具段：tool:add -> add；chat -> 空。"""
    assert _tool_name("tool:add") == "add"
    assert _tool_name("tool:echo") == "echo"
    assert _tool_name("chat") == ""
    assert _tool_name("chat.completions") == ""


def test_no_tool_key_when_not_given() -> None:
    """没给 tool 时不该塞一个空 name——否则适配器会以为要调空工具名。"""
    inv = _Invoke()
    orch = _orch(inv, _Router([[]]))
    orch.run_pipeline([{"connection_id": "c", "instruction": "聊天"}])
    assert "name" not in inv.calls[0]["params"]


# --------------------------------------------------------------------------- #
# 缺陷 B：并行派单要分散到不同 Agent
# --------------------------------------------------------------------------- #


def test_parallel_spreads_across_connections_when_scores_tie() -> None:
    """实测三个候选同为 4.5 分——不排除就会全落同一个连接。"""
    tie = [_cand("a", "chat"), _cand("b", "chat"), _cand("c", "chat")]
    inv = _Invoke()
    orch = _orch(inv, _Router([tie]))
    res = orch.run_parallel("任务", ["角度1", "角度2", "角度3"], max_workers=3)
    assert res.ok, res.error
    used = {s.connection_id for s in res.steps}
    assert len(used) == 3, f"应分散到 3 个不同连接，实际 {used}"


def test_parallel_falls_back_when_agents_exhausted() -> None:
    """Agent 少于侧面数时，多出的侧面该失败而不是重复用一个。"""
    two = [_cand("a", "chat"), _cand("b", "chat")]
    inv = _Invoke()
    orch = _orch(inv, _Router([two]))
    res = orch.run_parallel("任务", ["角度1", "角度2", "角度3"], max_workers=3)
    assert res.succeeded, "至少两个该成功"
    exhausted = [s for s in res.failed if "已被本轮其它步骤占用" in s.error]
    assert exhausted, "超出可用 Agent 的侧面应报『已被占用』"


def test_exhausted_error_differs_from_no_match() -> None:
    """『都被占用』与『没匹配上』对调用方意义完全不同，不能混为一谈。"""
    one = [_cand("only", "chat")]
    orch = _orch(_Invoke(), _Router([one]))
    res = orch.run_parallel("任务", ["角度1", "角度2"], max_workers=2)
    errs = " ".join(s.error for s in res.failed)
    assert "已被本轮其它步骤占用" in errs
    assert "没有 Agent 能处理" not in errs


def test_parallel_keeps_input_order_after_spreading() -> None:
    """分散派单后结果顺序仍要与输入一致，否则汇总会张冠李戴。"""
    tie = [_cand("a", "chat"), _cand("b", "chat")]
    orch = _orch(_Invoke(), _Router([tie]))
    res = orch.run_parallel("任务", ["甲", "乙"], max_workers=2)
    assert [s.index for s in res.steps] == [0, 1]
    assert [s.step for s in res.steps] == ["甲", "乙"]


def test_mcp_capability_routed_in_parallel_gets_tool_name() -> None:
    """路由把侧面派给 mcp 能力时，要自动带上工具名否则必失败。"""
    only_mcp = [_cand("m", "tool:add")]
    inv = _Invoke()
    orch = _orch(inv, _Router([only_mcp]))
    res = orch.run_parallel("算一下", ["角度1"], max_workers=1)
    assert res.ok, res.error
    assert inv.calls[0]["params"]["name"] == "add"


# --------------------------------------------------------------------------- #
# params_extra：让调用方补适配器需要的其它参数
# --------------------------------------------------------------------------- #


def test_params_extra_merged() -> None:
    """调用方应能补上适配器需要的任意参数（如 arguments）。"""
    inv = _Invoke()
    orch = _orch(inv, _Router([[]]))
    orch._run_step(0, "算", "17+25", connection_id="m", tool="add",
                   params_extra={"arguments": {"a": 17, "b": 25}})
    assert inv.calls[0]["params"]["arguments"] == {"a": 17, "b": 25}
    assert inv.calls[0]["params"]["name"] == "add"


@pytest.mark.parametrize("cap,expected", [
    ("tool:add", "add"),
    ("tool:subtract", "subtract"),
    ("chat", ""),
    ("chat.completions", ""),
])
def test_tool_name_parametrized(cap: str, expected: str) -> None:
    assert _tool_name(cap) == expected
