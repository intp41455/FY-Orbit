"""通用 CLI 契约单测（A-统一接入-04 · 补齐包3）。

覆盖：统一超时（杀进程树 + 验证回收 + 退出码 124）、统一退出码语义
（0 成功 / 其余失败）、env 白名单（敏感词键永不透传）、契约禁 shell。
"""

from __future__ import annotations

import sys

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.hub.access import (
    CLI_EXIT_OK,
    CLI_EXIT_TIMEOUT,
    CliChannelAdapter,
    CliContract,
    build_child_env,
    run_cli_process,
)
from find_yourself.services.hub.adapters import InvokeCall


def test_cli_contract_rejects_shell_string_and_bad_timeout():
    with pytest.raises(ValidationFailed):
        CliContract(command=())  # 空
    with pytest.raises(ValidationFailed):
        CliContract(command=("python",), timeout_seconds=0)


def test_run_cli_process_success_exit_zero():
    res = run_cli_process([sys.executable, "-c", "print('hello-cli')"],
                          timeout_seconds=15)
    assert res["ok"] is True
    assert res["exit_code"] == CLI_EXIT_OK
    assert res["timed_out"] is False
    assert "hello-cli" in res["stdout"]
    assert res["duration_ms"] >= 0


def test_run_cli_process_nonzero_exit_failed():
    res = run_cli_process([sys.executable, "-c",
                           "import sys; sys.stderr.write('why\\n'); sys.exit(7)"],
                          timeout_seconds=15)
    assert res["ok"] is False
    assert res["exit_code"] == 7
    assert "why" in res["stderr"]


def test_run_cli_process_timeout_reaps_tree_and_maps_124():
    res = run_cli_process([sys.executable, "-c", "import time; time.sleep(30)"],
                          timeout_seconds=0.5)
    assert res["ok"] is False
    assert res["timed_out"] is True
    assert res["exit_code"] == CLI_EXIT_TIMEOUT
    assert res["reaped"] is True, "超时后整棵进程树必须被回收并验证"


def test_run_cli_process_on_spawn_callback_gets_proc():
    seen = []

    def on_spawn(proc):
        seen.append(proc)

    run_cli_process([sys.executable, "-c", "print('x')"], timeout_seconds=15,
                    on_spawn=on_spawn)
    assert len(seen) == 1 and hasattr(seen[0], "pid")


def test_build_child_env_strips_secrets_and_honours_whitelist(monkeypatch):
    monkeypatch.setenv("FY_SAFE_VAR", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("PATH", "kept-for-cli")
    env = build_child_env(("FY_SAFE_VAR", "ANTHROPIC_API_KEY", "PATH"))
    assert env.get("FY_SAFE_VAR") == "1"
    assert "ANTHROPIC_API_KEY" not in env, "敏感词键即使点名也不透传"
    assert env.get("PATH") == "kept-for-cli"


def test_cli_channel_invoke_timeout_meta():
    ch = CliChannelAdapter({"command": [sys.executable, "-c", "import time; time.sleep(30)"],
                            "timeout_seconds": 0.5})
    result = ch.invoke(InvokeCall(action="run"))
    assert result.ok is False
    assert result.meta["timed_out"] is True
    assert result.meta["exit_code"] == CLI_EXIT_TIMEOUT
    assert "超时" in result.error
    assert result.meta["reaped"] is True
