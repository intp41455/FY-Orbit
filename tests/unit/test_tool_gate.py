"""T-GATE tool admission gate tests.

Covers: deny (exact + prefix wildcard), ask (hook approve / hook deny /
no hook fail-closed), env parsing, decision logging in the registry call
log, and the no-gate backward-compat path.
"""

from __future__ import annotations

import json

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.tool_gate import (
    ASK_ENV,
    DENY_ENV,
    GateDecision,
    ToolGate,
    gate_from_env,
    install_into,
)
from find_yourself.services.tool_registry import ToolRegistryService


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


def call_log(reg: ToolRegistryService) -> list[dict]:
    return json.loads((reg._dir / "call_log.json").read_text(encoding="utf-8"))


# --- pure gate logic -----------------------------------------------------------

def test_no_rules_allows_everything():
    gate = ToolGate()
    d = gate.check("add")
    assert (d.action, d.source) == ("allow", "default")


def test_deny_exact_name():
    gate = ToolGate(deny_rules=("shell.exec",))
    d = gate.check("shell.exec")
    assert d.action == "deny"
    assert "denied by gate rule" in d.reason
    assert d.matched_rule == "^shell\\.exec$"


def test_deny_prefix_wildcard_dots_not_wildcards():
    gate = ToolGate(deny_rules=("shell.*",))
    assert gate.check("shell.exec").action == "deny"
    assert gate.check("shell.rm.rf").action == "deny"
    # the dot is literal: 'shellx.exec' must NOT match 'shell.*'
    assert gate.check("shellx.exec").action == "allow"


def test_deny_wins_over_ask():
    gate = ToolGate(deny_rules=("kb.*",), ask_rules=("kb.search",))
    assert gate.check("kb.search").action == "deny"


def test_ask_with_hook_approves():
    seen: dict = {}

    def hook(tool_name, arguments, decision):
        seen["tool"] = tool_name
        seen["matched"] = decision.matched_rule
        return {"approved": True, "reason": "human said yes"}

    gate = ToolGate(ask_rules=("shell.*",), ask_hook=hook)
    d = gate.check("shell.exec")
    assert d.action == "allow"
    assert seen == {"tool": "shell.exec", "matched": "^shell\\..*$"}


def test_ask_with_hook_denies_with_reason():
    gate = ToolGate(
        ask_rules=("shell.exec",),
        ask_hook=lambda *_: {"approved": False, "reason": "not tonight"},
    )
    d = gate.check("shell.exec")
    assert d.action == "deny"
    assert d.reason == "not tonight"


def test_ask_without_hook_is_fail_closed():
    gate = ToolGate(ask_rules=("shell.exec",))
    d = gate.check("shell.exec")
    assert d.action == "deny"
    assert "no approval channel" in d.reason


def test_hook_returning_garbage_raises():
    gate = ToolGate(ask_rules=("xy",), ask_hook=lambda *_: "yes")
    with pytest.raises(ValueError):
        gate.check("xy")


def test_invalid_pattern_raises_loudly():
    with pytest.raises(ValueError):
        ToolGate(deny_rules=("SHELL.*",))  # names are lowercase by contract
    with pytest.raises(ValueError):
        ToolGate(ask_rules=("a**b",))


def test_garbage_tool_name_denied():
    assert ToolGate().check("").action == "deny"
    assert ToolGate().check(None).action == "deny"


# --- env source ------------------------------------------------------------------

def test_gate_from_env(monkeypatch):
    monkeypatch.setenv(DENY_ENV, "shell.*, http.raw")
    monkeypatch.setenv(ASK_ENV, "kb.search")
    gate = gate_from_env()
    assert gate.check("shell.exec").action == "deny"
    assert gate.check("http.raw").action == "deny"
    assert gate.check("kb.search").action == "deny"  # no hook wired
    assert gate.check("add").action == "allow"


def test_gate_from_env_empty(monkeypatch):
    monkeypatch.delenv(DENY_ENV, raising=False)
    monkeypatch.delenv(ASK_ENV, raising=False)
    gate = gate_from_env()
    assert gate.check("add").action == "allow"


def test_gate_from_env_bad_pattern_raises(monkeypatch):
    monkeypatch.setenv(DENY_ENV, "NOT-VALID")
    with pytest.raises(ValueError):
        gate_from_env()


# --- registry integration ----------------------------------------------------------

def test_registry_denied_call_raises_and_logs(registry):
    install_into(registry, ToolGate(deny_rules=("shell.*",)))
    with pytest.raises(ValidationFailed) as exc:
        registry.invoke("shell.exec", {"cmd": "rm -rf /"})
    assert exc.value.code == "tool_gate_denied"
    log = call_log(registry)
    assert len(log) == 1
    assert log[0]["executed"] is False
    assert log[0]["gate"]["action"] == "deny"


def test_registry_allowed_call_executes_and_logs_gate(registry):
    install_into(registry, ToolGate(deny_rules=("shell.*",)))
    receipt = registry.invoke("add", {"a": 1, "b": 2})
    assert receipt["executed"] is True
    assert receipt["gate"]["action"] == "allow"


def test_registry_ask_denied_without_hook(registry):
    install_into(registry, ToolGate(ask_rules=("add",)))
    with pytest.raises(ValidationFailed):
        registry.invoke("add", {"a": 1, "b": 2})


def test_registry_ask_routed_through_hook(registry):
    def hook(tool_name, arguments, decision):
        assert arguments == {"a": 1, "b": 2}
        return {"approved": True, "reason": "ok"}

    gate = install_into(registry, ToolGate(ask_rules=("add",), ask_hook=hook))
    receipt = registry.invoke("add", {"a": 1, "b": 2})
    assert receipt["executed"] is True
    assert receipt["gate"]["matched_rule"] == "^add$"


def test_registry_without_gate_unchanged(registry):
    # backward compat: no gate installed => behaves exactly as before
    assert registry.tool_gate is None
    receipt = registry.invoke("add", {"a": 1, "b": 2})
    assert receipt["executed"] is True
    assert "gate" not in receipt


def test_decision_is_frozen_dataclass():
    with pytest.raises(ValueError):
        GateDecision("nuke")  # not one of the three actions
