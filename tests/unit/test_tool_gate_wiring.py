"""T-GATE 装配层回归：闸门必须真的挂在默认运行路径上。

``tests/unit/test_tool_gate.py`` 覆盖闸门本身的语义；本文件覆盖**接线**：
默认装配是否注入闸门、环境变量是否真生效、非法策略是否响亮失败、以及
``ToolRegistryService`` 是否存在绕过闸门的第二个执行入口。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from find_yourself.services import tool_registry as tool_registry_mod
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.tool_gate import (
    ASK_ENV,
    DENY_ENV,
    ToolGate,
    install_default_gate,
    install_into,
)
from find_yourself.services.tool_registry import ToolRegistryService, tool_registry


@pytest.fixture()
def registry(tmp_path):
    reg = ToolRegistryService(persist_dir=tmp_path / "tool_registry")
    reg.register(
        name="add",
        description="两数相加",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
        entry={"type": "builtin", "executor": "add"},
    )
    reg.register(
        name="shell.exec",
        description="执行 shell 命令",
        parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
        entry={"type": "builtin", "executor": "echo"},
    )
    return reg


@pytest.fixture(autouse=True)
def _no_ambient_policy(monkeypatch):
    """本文件的每个用例都从「环境变量干净」出发，避免被跑测试的机器污染。"""
    monkeypatch.delenv(DENY_ENV, raising=False)
    monkeypatch.delenv(ASK_ENV, raising=False)


# --- 默认装配：闸门不再游离 ----------------------------------------------------

def test_process_singleton_is_gated_by_default():
    """真实运行路径用的是进程级单例，它必须自带闸门（合入前这里是 None）。"""
    assert isinstance(tool_registry.tool_gate, ToolGate)
    assert tool_registry.tool_gate.from_env is True


def test_standalone_registry_instances_stay_unwired(registry):
    """独立实例保持 None = 旧行为不变（既有单测依赖这一点，别顺手改掉）。"""
    assert registry.tool_gate is None
    assert "gate" not in registry.invoke("add", {"a": 1, "b": 2})


def test_default_gate_installs_and_allows_everything(registry):
    """默认不拦：没有 deny/ask 配置时，闸门放行一切，调用照常执行。"""
    gate = install_default_gate(registry)
    assert isinstance(gate, ToolGate)
    receipt = registry.invoke("add", {"a": 1, "b": 2})
    assert receipt["executed"] is True
    assert receipt["result"] == {"sum": 3}
    assert receipt["gate"]["action"] == "allow"


def test_install_default_gate_resolves_the_singleton():
    assert install_default_gate() is tool_registry.tool_gate


def test_install_default_gate_is_idempotent_and_keeps_host_gate(registry):
    """幂等：宿主自己装的闸门（典型是带 HITL ask_hook 的）不会被环境默认覆盖。"""
    first = install_default_gate(registry)
    assert install_default_gate(registry) is first

    custom = ToolGate(deny_rules=("add",))
    install_into(registry, custom)
    assert install_default_gate(registry) is custom
    with pytest.raises(ValidationFailed):
        registry.invoke("add", {"a": 1, "b": 2})


def test_replace_true_restores_env_policy(registry, monkeypatch):
    install_into(registry, ToolGate(deny_rules=("add",)))
    monkeypatch.setenv(DENY_ENV, "shell.*")
    gate = install_default_gate(registry, replace=True)
    assert gate.from_env is True
    assert registry.tool_gate is gate
    registry.invoke("add", {"a": 1, "b": 2})  # 环境里没 deny add => 放行


# --- FY_TOOL_GATE_DENY / FY_TOOL_GATE_ASK 真的生效 ----------------------------

def test_env_deny_blocks_the_call(registry, monkeypatch):
    monkeypatch.setenv(DENY_ENV, "shell.*")
    install_default_gate(registry, replace=True)
    with pytest.raises(ValidationFailed) as exc:
        registry.invoke("shell.exec", {"cmd": "rm -rf /"})
    assert exc.value.code == "tool_gate_denied"
    assert "denied by gate rule" in exc.value.message
    # 未命中的工具不受影响（deny 是按名字精确/前缀匹配，不是全局熔断）
    assert registry.invoke("add", {"a": 2, "b": 3})["result"] == {"sum": 5}


def test_env_ask_without_approval_channel_is_denied(registry, monkeypatch):
    """ask 语义：默认装配没有审批通道 => 降级为 deny，绝不静默执行。"""
    monkeypatch.setenv(ASK_ENV, "add")
    install_default_gate(registry, replace=True)
    with pytest.raises(ValidationFailed) as exc:
        registry.invoke("add", {"a": 1, "b": 2})
    assert exc.value.code == "tool_gate_denied"
    assert "no approval channel" in exc.value.message


def test_env_ask_with_approval_channel_executes(registry, monkeypatch):
    """ask 语义：接上审批通道后同一份环境策略放行（deny 仍优先于 ask）。"""
    monkeypatch.setenv(ASK_ENV, "add")
    monkeypatch.setenv(DENY_ENV, "shell.*")
    gate = install_default_gate(registry, replace=True)
    gate.ask_hook = lambda _name, _args, _decision: {"approved": True, "reason": "owner ok"}
    receipt = registry.invoke("add", {"a": 4, "b": 5})
    assert receipt["executed"] is True
    assert receipt["result"] == {"sum": 9}
    with pytest.raises(ValidationFailed):
        registry.invoke("shell.exec", {"cmd": "ls"})


def test_blank_env_entries_are_no_rules(registry, monkeypatch):
    monkeypatch.setenv(DENY_ENV, "  ,  ,")
    monkeypatch.setenv(ASK_ENV, ",")
    install_default_gate(registry, replace=True)
    assert registry.invoke("shell.exec", {"cmd": "ls"})["executed"] is True


# --- 非法策略：响亮失败，且不留下半装状态 --------------------------------------

def test_invalid_env_pattern_fails_fast_with_readable_reason(registry, monkeypatch):
    monkeypatch.setenv(DENY_ENV, "shell.*, NOT-VALID")
    with pytest.raises(ValueError) as exc:
        install_default_gate(registry, replace=True)
    message = str(exc.value)
    assert DENY_ENV in message          # 指明来源环境变量
    assert "NOT-VALID" in message       # 指明 offending rule
    assert "prefix.*" in message        # 指明合法写法
    # 失败必须是「响亮地不动」：旧闸门仍在，没有被半装状态污染
    assert registry.tool_gate is None
    assert registry.invoke("add", {"a": 1, "b": 2})["executed"] is True


def test_invalid_ask_pattern_also_fails_fast(registry, monkeypatch):
    monkeypatch.setenv(ASK_ENV, "a**b")
    with pytest.raises(ValueError) as exc:
        install_default_gate(registry, replace=True)
    assert ASK_ENV in str(exc.value)


def test_invalid_env_pattern_does_not_disturb_an_existing_gate(registry, monkeypatch):
    install_into(registry, ToolGate(deny_rules=("add",)))
    monkeypatch.setenv(DENY_ENV, "BAD")
    with pytest.raises(ValueError):
        install_default_gate(registry, replace=True)
    assert isinstance(registry.tool_gate, ToolGate)
    assert registry.tool_gate.deny_rules == ("add",)


# --- 绕过闸门的执行入口 ---------------------------------------------------------

_EXEC_SUBSCRIPT_BASES = {"BUILTIN_EXECUTORS", "_handlers"}
_EXEC_CALL_ATTRS = {"call_tool", "handler"}


def _executing_functions(source: str) -> set[str]:
    """找出 source 里所有「能真正执行工具」的函数名。

    判定口径 = 触达真实执行汇（取 ``BUILTIN_EXECUTORS[...]`` / ``self._handlers[...]``
    的值、调用 MCP ``call_tool``、发 httpx POST、调用 handler），只看 Load 上下文，
    因此 ``set_handler`` 往 ``_handlers`` **写**不会被误判成执行入口。
    """
    hits: set[str] = set()
    for fn in (n for n in ast.walk(ast.parse(source))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for node in ast.walk(fn):
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                base = node.value
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
                if name in _EXEC_SUBSCRIPT_BASES:
                    hits.add(fn.name)
            elif isinstance(node, ast.Call):
                func = node.func
                attr = getattr(func, "attr", None) or getattr(func, "id", None)
                if attr in _EXEC_CALL_ATTRS or (attr == "post" and isinstance(func, ast.Attribute)):
                    hits.add(fn.name)
    return hits


def test_invoke_is_the_only_execution_entry_in_the_registry():
    """防绕过回归：任何新增的执行入口都会被抓出来（必须先过闸门）。"""
    source = Path(tool_registry_mod.__file__).read_text(encoding="utf-8")
    assert _executing_functions(source) == {"invoke"}


def test_execution_entry_detector_is_not_vacuous():
    """证明上面那条检测不是恒真：给它一段绕过代码，它必须报警。"""
    bypass = (
        "def invoke(self, name, args):\n"
        "    return BUILTIN_EXECUTORS['echo'](args)\n"
        "def execute_now(self, name, args):\n"
        "    return self._handlers[name](args)\n"
        "def call_remote(self, url, args):\n"
        "    import httpx\n"
        "    return httpx.post(url, json=args)\n"
    )
    assert _executing_functions(bypass) == {"invoke", "execute_now", "call_remote"}


def test_second_caller_dynamic_mcp_is_gated(tmp_path):
    """另一个真实调用方（McpDynamicService.invoke_tool）同样被拦，不存在旁路。"""
    from find_yourself.services.mcp_dynamic import McpDynamicService

    reg = ToolRegistryService(persist_dir=tmp_path / "tool_registry")
    reg.register(
        name="demo.add",
        description="MCP 加法",
        parameters={"type": "object", "properties": {}},
        entry={"type": "mcp", "server": "demo", "remote_tool": "add"},
    )
    install_into(reg, ToolGate(deny_rules=("demo.*",)))
    dyn = McpDynamicService(registry=reg)
    with pytest.raises(ValidationFailed) as exc:
        dyn.invoke_tool("demo", "add", {"a": 1})
    assert exc.value.code == "tool_gate_denied"


def test_gate_decision_is_recorded_for_denied_calls(registry, monkeypatch):
    """被拦的调用也必须进证据链（executed=False + gate 判定），否则无法审计。"""
    monkeypatch.setenv(DENY_ENV, "add")
    install_default_gate(registry, replace=True)
    with pytest.raises(ValidationFailed):
        registry.invoke("add", {"a": 1, "b": 2})
    log = registry.recent_calls()
    assert len(log) == 1
    assert log[0]["executed"] is False
    assert log[0]["gate"]["action"] == "deny"
    assert log[0]["gate"]["matched_rule"] == "^add$"
