"""T-GATE HTTP 面回归：闸门在真实 ``POST /api/tools/{name}/invoke`` 上生效。

``tests/unit/test_tool_gate.py`` 只测闸门语义；这里证明**默认装配**之后，
配置经由 ``FY_TOOL_GATE_DENY`` / ``FY_TOOL_GATE_ASK`` 能挡住真实 HTTP 调用，
并且没有配置时不会误杀任何既有工具调用。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner

from find_yourself.services.tool_gate import (
    ASK_ENV,
    DENY_ENV,
    ToolGate,
    install_default_gate,
)
from find_yourself.services.tool_registry import tool_registry


@pytest.fixture()
def headers(client: TestClient) -> dict:
    return login_owner(client)


@pytest.fixture()
def registered(client: TestClient, headers: dict) -> dict:
    r = client.post("/api/tools/register", json={
        "name": "gateprobe.add",
        "description": "闸门探针：两数相加",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
        "entry": {"type": "builtin", "executor": "add"},
    }, headers=headers)
    assert r.status_code == 200, r.text
    return headers


@pytest.fixture()
def gated(monkeypatch):
    """把一个闸门装到进程级单例上，结束后自动还原（monkeypatch 负责）。"""
    def _install(gate: ToolGate) -> ToolGate:
        monkeypatch.setattr(tool_registry, "tool_gate", gate)
        return gate
    return _install


def _invoke(client: TestClient, headers: dict, tool: str, args: dict):
    return client.post(f"/api/tools/{tool}/invoke", json={"arguments": args}, headers=headers)


def test_invoke_works_with_no_policy_configured(client: TestClient, registered: dict):
    """默认不拦：没有配置任何 deny/ask 时，HTTP 调用照常执行。"""
    r = _invoke(client, registered, "gateprobe.add", {"a": 1, "b": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["executed"] is True
    assert body["result"] == {"sum": 3}
    # 默认装配的闸门已经在跑，只是判定为放行，并留在回执里
    assert body["gate"]["action"] == "allow"
    assert body["gate"]["source"] == "default"


def test_env_deny_returns_422_tool_gate_denied(
    client: TestClient, registered: dict, gated, monkeypatch
):
    """FY_TOOL_GATE_DENY 生效：被拒的调用返回结构化错误且**没有执行**。"""
    monkeypatch.setenv(DENY_ENV, "gateprobe.*")
    gated(install_default_gate(tool_registry, replace=True))
    r = _invoke(client, registered, "gateprobe.add", {"a": 1, "b": 2})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "tool_gate_denied"
    assert "denied by gate rule" in err["message"]
    # 证据链里有这次被拦的调用（executed=False），可审计
    calls = client.get("/api/tools/calls", headers=registered).json()["calls"]
    denied = [c for c in calls if c["tool"] == "gateprobe.add"]
    assert denied and denied[-1]["executed"] is False
    assert denied[-1]["gate"]["action"] == "deny"


def test_env_ask_denies_over_http_without_approval_channel(
    client: TestClient, registered: dict, gated, monkeypatch
):
    """FY_TOOL_GATE_ASK 生效：HTTP 调用没有审批通道 => 降级为 deny。"""
    monkeypatch.setenv(ASK_ENV, "gateprobe.add")
    gated(install_default_gate(tool_registry, replace=True))
    r = _invoke(client, registered, "gateprobe.add", {"a": 1, "b": 2})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "tool_gate_denied"
    assert "no approval channel" in err["message"]


def test_env_ask_executes_once_approval_channel_is_wired(
    client: TestClient, registered: dict, gated, monkeypatch
):
    """ask 语义另一半：接上审批通道后，同一份环境策略放行执行。"""
    monkeypatch.setenv(ASK_ENV, "gateprobe.add")
    gate = install_default_gate(tool_registry, replace=True)
    gate.ask_hook = lambda _n, _a, _d: {"approved": True, "reason": "owner ok"}
    gated(gate)
    r = _invoke(client, registered, "gateprobe.add", {"a": 20, "b": 22})
    assert r.status_code == 200, r.text
    assert r.json()["result"] == {"sum": 42}


def test_invalid_env_policy_is_not_silently_ignored(monkeypatch):
    """非法 pattern 必须响亮失败，而不是悄悄放行。"""
    monkeypatch.setenv(DENY_ENV, "gateprobe.*, NOT-VALID")
    with pytest.raises(ValueError) as exc:
        install_default_gate(tool_registry, replace=True)
    assert DENY_ENV in str(exc.value)
    assert "NOT-VALID" in str(exc.value)


def test_gate_deny_precedes_schema_validation(client: TestClient, registered: dict, gated):
    """闸门跑在 schema 校验之前：参数非法也不会被执行，只报闸门原因。"""
    gated(ToolGate(deny_rules=("gateprobe.*",)))
    r = _invoke(client, registered, "gateprobe.add", {"a": "not-a-number"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "tool_gate_denied"


def test_registry_singleton_gate_survives_api_boot(client: TestClient, registered: dict):
    """闸门不是靠某个请求顺带装上的：app 起来时单例就已经带闸门。"""
    assert isinstance(tool_registry.tool_gate, ToolGate)
    assert _invoke(client, registered, "gateprobe.add", {"a": 5, "b": 6}).status_code == 200


def test_get_services_assembly_keeps_the_installed_gate(
    client: TestClient, registered: dict, gated
):
    """DI 装配点必须幂等：重申闸门不能清掉宿主自己装的那一个。"""
    custom = ToolGate(deny_rules=("gateprobe.*",))
    gated(custom)
    from find_yourself.api import deps

    deps.install_default_gate()
    assert tool_registry.tool_gate is custom
    assert _invoke(client, registered, "gateprobe.add", {"a": 1, "b": 2}).status_code == 422
