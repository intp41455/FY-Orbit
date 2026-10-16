"""CLI Runner 适配器单测：argv 无 shell / 硬超时 / 输出上限 / 可注入执行器。

**单测绝不真起外部进程**：所有执行都走注入的假执行器（``executor=``），因此
超时、超限、非零退出码都能确定性断言。真起进程的端到端用例放在文件末尾，
由 :data:`FY_CLI_RUNNER_E2E` 环境变量显式打开，默认 skip。

覆盖的硬边界（与 ``src/find_yourself/adapters/cli_runner.py`` 的文档串一一对应）：

1. argv 是列表；选项与取值严格分隔；位置参数排在 ``--`` 之后。
2. 超时 → ``cli_runner_timeout``；输出超限 → ``cli_runner_output_limit``（两个码不同）。
3. ``executor=`` 注入即完全替身，不真起进程。
4. 退出码非 0 → ``ok=False`` + stderr 原样回传；空输出不做成功依据。
"""

from __future__ import annotations

import os
import sys

import pytest

from find_yourself.adapters.cli_runner import (
    ERR_BAD_CWD,
    ERR_EXECUTABLE_NOT_FOUND,
    ERR_EXECUTION_ERROR,
    ERR_EXIT_NONZERO,
    ERR_INVALID_PARAM,
    ERR_MISSING_PARAM,
    ERR_OUTPUT_LIMIT,
    ERR_PARAM_TOO_LONG,
    ERR_SPEC_INVALID,
    ERR_TIMEOUT,
    ERR_UNKNOWN_COMMAND,
    ERR_UNKNOWN_PARAM,
    MAX_OUTPUT_BYTES,
    MAX_PARAM_CHARS,
    MAX_TIMEOUT_SECONDS,
    CliCommandSpec,
    CliRunnerAdapter,
    ExecOutcome,
    ExecRequest,
    build_argv,
    popen_executor,
    spec_json_schema,
)
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.tool_registry import ToolRegistryService

# --------------------------------------------------------------------------- #
# 假执行器：记录请求、返回预设回执——单测全靠它，不起进程
# --------------------------------------------------------------------------- #


class FakeExecutor:
    """替身执行器：把 ``ExecRequest`` 存下来，回放预设的 ``ExecOutcome``。"""

    def __init__(self, outcome: ExecOutcome | None = None, *, raises: BaseException | None = None):
        self.outcome = outcome or ExecOutcome(returncode=0, stdout="ok")
        self.raises = raises
        self.requests: list[ExecRequest] = []

    def __call__(self, request: ExecRequest) -> ExecOutcome:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        return self.outcome

    @property
    def last(self) -> ExecRequest:
        assert self.requests, "执行器一次都没被调用——测试想断言的路径没走到"
        return self.requests[-1]


def _spec(**kw) -> CliCommandSpec:
    base = {
        "name": "notes.search",
        "executable": "/usr/bin/notes",
        "option_params": {"query": "--query", "limit": "--limit"},
        "positional_params": ("path",),
    }
    base.update(kw)
    return CliCommandSpec(**base)


def _runner(outcome: ExecOutcome | None = None, *,
            raises: BaseException | None = None,
            **spec_kw) -> tuple[CliRunnerAdapter, FakeExecutor]:
    fake = FakeExecutor(outcome, raises=raises)
    return CliRunnerAdapter([_spec(**spec_kw)], executor=fake), fake


# --------------------------------------------------------------------------- #
# 1) spec 注册期校验：命令形态、选项字面量、预算上下限
# --------------------------------------------------------------------------- #


def test_spec_rejects_unknown_name_shape():
    with pytest.raises(ValidationFailed) as err:
        CliCommandSpec(name="Notes Search", executable="/usr/bin/notes")
    assert err.value.code == ERR_SPEC_INVALID


def test_spec_rejects_option_flag_that_is_not_an_option_literal():
    """选项槽必须是一个 '-' 开头的**独立**字面量——否则就成了字符串拼接。"""
    with pytest.raises(ValidationFailed) as err:
        _spec(option_params={"query": "--query={value}"})
    assert err.value.code == ERR_SPEC_INVALID


def test_spec_rejects_param_declared_as_both_option_and_positional():
    with pytest.raises(ValidationFailed) as err:
        _spec(option_params={"query": "--query"}, positional_params=("query",))
    assert err.value.code == ERR_SPEC_INVALID


@pytest.mark.parametrize("timeout", [0, -1, MAX_TIMEOUT_SECONDS + 1])
def test_spec_rejects_out_of_range_timeout(timeout):
    with pytest.raises(ValidationFailed) as err:
        _spec(timeout_seconds=timeout)
    assert err.value.code == ERR_SPEC_INVALID


@pytest.mark.parametrize("cap", [0, -5, MAX_OUTPUT_BYTES + 1])
def test_spec_rejects_out_of_range_output_cap(cap):
    with pytest.raises(ValidationFailed) as err:
        _spec(max_output_bytes=cap)
    assert err.value.code == ERR_SPEC_INVALID


def test_adapter_refuses_silent_overwrite_of_a_registered_command():
    """同名覆盖会让「已授权的可执行文件」被悄悄换掉——那是提权面，必须拒绝。"""
    adapter = CliRunnerAdapter([_spec()], executor=FakeExecutor())
    with pytest.raises(ValidationFailed) as err:
        adapter.register(_spec(executable="/tmp/evil"))
    assert err.value.code == ERR_SPEC_INVALID


# --------------------------------------------------------------------------- #
# 2) argv 组装：列表 / 严格分隔 / `--` 终止符 / 未知参数拒绝
# --------------------------------------------------------------------------- #


def test_build_argv_returns_a_list_in_declared_order():
    argv = build_argv(_spec(), {"query": "hello", "limit": 10, "path": "notes.md"})
    assert argv == [
        "/usr/bin/notes",
        "--query", "hello",
        "--limit", "10",
        "--", "notes.md",
    ]
    assert all(isinstance(part, str) for part in argv)


def test_option_value_is_its_own_argv_element_so_spaces_cannot_resplit():
    argv = build_argv(_spec(), {"query": "a b; rm -rf /", "limit": 1, "path": "n.md"})
    assert argv[1:5] == ["--query", "a b; rm -rf /", "--limit", "1"]
    # 危险串整体作为一个元素存在，从未被拆开——这正是无 shell 的意义。
    assert "a b; rm -rf /" in argv
    assert not any(";" == part for part in argv)


def test_positional_value_after_terminator_cannot_be_read_as_an_option():
    argv = build_argv(_spec(), {"query": "q", "limit": 1, "path": "--dangerous-flag"})
    assert argv.index("--") < argv.index("--dangerous-flag")
    assert argv[-1] == "--dangerous-flag"


def test_unknown_param_is_rejected_not_silently_dropped():
    with pytest.raises(ValidationFailed) as err:
        build_argv(_spec(), {"query": "q", "limit": 1, "path": "n.md", "extra": "boom"})
    assert err.value.code == ERR_UNKNOWN_PARAM


def test_missing_param_is_rejected():
    with pytest.raises(ValidationFailed) as err:
        build_argv(_spec(), {"query": "q", "limit": 1})
    assert err.value.code == ERR_MISSING_PARAM


def test_bool_param_is_normalised_not_str_of_python_repr():
    argv = build_argv(_spec(option_params={"verbose": "--verbose"}, positional_params=()),
                      {"verbose": True})
    assert argv == ["/usr/bin/notes", "--verbose", "true"]


@pytest.mark.parametrize("bad", [["a", "b"], {"k": "v"}, None, object()])
def test_non_scalar_param_is_rejected(bad):
    with pytest.raises(ValidationFailed) as err:
        build_argv(_spec(option_params={"query": "--query"}, positional_params=()),
                   {"query": bad})
    assert err.value.code == ERR_INVALID_PARAM


@pytest.mark.parametrize("bad", ["a\x00b", "a\nb", "a\rb"])
def test_control_chars_in_param_value_are_rejected(bad):
    with pytest.raises(ValidationFailed) as err:
        build_argv(_spec(option_params={"query": "--query"}, positional_params=()),
                   {"query": bad})
    assert err.value.code == ERR_INVALID_PARAM


def test_oversized_param_is_rejected_before_it_reaches_argv():
    with pytest.raises(ValidationFailed) as err:
        build_argv(_spec(option_params={"query": "--query"}, positional_params=()),
                   {"query": "x" * (MAX_PARAM_CHARS + 1)})
    assert err.value.code == ERR_PARAM_TOO_LONG


def test_spec_json_schema_declares_every_slot_and_forbids_extras():
    schema = spec_json_schema(_spec())
    assert schema["type"] == "object"
    assert sorted(schema["properties"]) == ["limit", "path", "query"]
    assert sorted(schema["required"]) == ["limit", "path", "query"]
    assert schema["additionalProperties"] is False


# --------------------------------------------------------------------------- #
# 3) 执行：注入的假执行器接管，单测不起任何进程
# --------------------------------------------------------------------------- #


def test_run_passes_the_argv_list_and_declared_budgets_to_the_executor():
    adapter, fake = _runner(max_output_bytes=4096, timeout_seconds=12)
    result = adapter.run("notes.search", {"query": "q", "limit": 2, "path": "n.md"})
    assert result.ok is True
    assert fake.last.argv == ["/usr/bin/notes", "--query", "q", "--limit", "2", "--", "n.md"]
    assert fake.last.timeout_seconds == 12
    assert fake.last.max_output_bytes == 4096


def test_unregistered_command_is_refused_without_touching_the_executor():
    adapter, fake = _runner()
    result = adapter.run("definitely.not.registered", {})
    assert result.ok is False
    assert result.error_code == ERR_UNKNOWN_COMMAND
    assert fake.requests == []  # 一个进程都没起


def test_nonzero_exit_is_a_failure_with_stderr_preserved():
    adapter, _ = _runner(ExecOutcome(returncode=2, stdout="partial", stderr="fatal: no such note"))
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"})
    assert result.ok is False
    assert result.error_code == ERR_EXIT_NONZERO
    assert result.exit_code == 2
    assert result.stderr == "fatal: no such note"
    # stderr 也要出现在 error 文本里：异常/日志路径上只看得到 message。
    assert "fatal: no such note" in result.error


def test_exit_zero_with_empty_stdout_is_still_success():
    """空输出不是失败的证据：判定只看退出码。"""
    adapter, _ = _runner(ExecOutcome(returncode=0, stdout="", stderr=""))
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"})
    assert result.ok is True
    assert result.exit_code == 0
    assert result.stdout == ""


def test_empty_output_is_not_evidence_of_success_either():
    """反向同理：退出码非 0，即使一个字都没输出，也必须失败。"""
    adapter, _ = _runner(ExecOutcome(returncode=1, stdout="", stderr=""))
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"})
    assert result.ok is False
    assert result.error_code == ERR_EXIT_NONZERO
    assert result.exit_code == 1


def test_stderr_on_a_successful_exit_is_still_reported():
    adapter, _ = _runner(ExecOutcome(returncode=0, stdout="ok", stderr="warning: deprecated flag"))
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"})
    assert result.ok is True
    assert result.stderr == "warning: deprecated flag"


def test_timeout_is_its_own_error_code_and_not_a_success():
    adapter, fake = _runner(ExecOutcome(returncode=None, stdout="partial", timed_out=True))
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"},
                         timeout_seconds=0.5)
    assert result.ok is False
    assert result.error_code == ERR_TIMEOUT
    assert result.timed_out is True
    assert fake.last.timeout_seconds == 0.5


def test_output_limit_is_a_distinct_code_from_timeout():
    """超时与超限必须能分开看——都归成「执行失败」就等于没有上限。"""
    adapter, _ = _runner(ExecOutcome(returncode=0, stdout="x" * 100, output_exceeded=True))
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"})
    assert result.ok is False
    assert result.error_code == ERR_OUTPUT_LIMIT
    assert result.error_code != ERR_TIMEOUT
    assert result.output_truncated is True


def test_caller_cannot_lift_the_timeout_beyond_the_hard_ceiling():
    adapter, fake = _runner()
    adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"},
                timeout_seconds=10_000)
    assert fake.last.timeout_seconds == MAX_TIMEOUT_SECONDS


def test_missing_executable_is_reported_honestly_not_faked():
    adapter, _ = _runner(ExecOutcome(error_code=ERR_EXECUTABLE_NOT_FOUND,
                                     error="找不到可执行文件 '/usr/bin/notes'"))
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"})
    assert result.ok is False
    assert result.error_code == ERR_EXECUTABLE_NOT_FOUND


def test_executor_exception_becomes_a_failed_result_not_a_crash():
    adapter, _ = _runner(raises=RuntimeError("boom"))
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"})
    assert result.ok is False
    assert result.error_code == ERR_EXECUTION_ERROR
    assert "boom" in result.error


def test_executor_returning_a_non_outcome_is_rejected():
    adapter = CliRunnerAdapter([_spec()], executor=lambda _req: {"ok": True})
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"})
    assert result.ok is False
    assert result.error_code == ERR_EXECUTION_ERROR


def test_nonexistent_cwd_is_refused_before_execution():
    adapter, fake = _runner(cwd="/definitely/not/a/dir")
    result = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"})
    assert result.ok is False
    assert result.error_code == ERR_BAD_CWD
    assert fake.requests == []


def test_probe_says_not_available_when_binary_is_missing():
    adapter = CliRunnerAdapter([_spec(executable="definitely-not-a-real-binary-xyz")],
                               executor=FakeExecutor())
    report = adapter.probe("notes.search")
    assert report["available"] is False
    assert report["resolved"] == ""
    assert "definitely-not-a-real-binary-xyz" in report["detail"]


def test_to_public_carries_the_full_receipt_including_failure_fields():
    adapter, _ = _runner(ExecOutcome(returncode=3, stderr="nope"))
    payload = adapter.run("notes.search", {"query": "q", "limit": 1, "path": "n.md"}).to_public()
    assert payload["ok"] is False
    assert payload["exit_code"] == 3
    assert payload["error_code"] == ERR_EXIT_NONZERO
    assert payload["stderr"] == "nope"


# --------------------------------------------------------------------------- #
# 4) 接入 tool_registry：entry.type="cli"（不是新的 Hub kind）
# --------------------------------------------------------------------------- #


def _registry(tmp_path) -> ToolRegistryService:
    return ToolRegistryService(persist_dir=tmp_path)


def test_cli_entry_requires_a_runner_name(tmp_path):
    registry = _registry(tmp_path)
    with pytest.raises(ValidationFailed) as err:
        registry.register(name="cli.x", description="d", parameters={"type": "object"},
                          entry={"type": "cli"})
    assert err.value.code == "invalid_entry_cli"


def test_register_tools_wires_metadata_and_live_adapter(tmp_path):
    adapter, _ = _runner()
    registry = _registry(tmp_path)
    names = adapter.register_tools(registry)
    assert names == ["cli.notes.search"]
    meta = registry.get_tool("cli.notes.search")
    assert meta["entry"] == {"type": "cli", "runner": "notes.search"}
    assert registry.cli_runner("notes.search") is adapter


def test_invoking_a_cli_tool_returns_the_real_receipt(tmp_path):
    adapter, fake = _runner(ExecOutcome(returncode=0, stdout="found 3 notes"))
    registry = _registry(tmp_path)
    adapter.register_tools(registry)
    receipt = registry.invoke("cli.notes.search",
                              {"query": "budget", "limit": "3", "path": "n.md"})
    assert receipt["executed"] is True
    assert receipt["result"]["stdout"] == "found 3 notes"
    assert receipt["result"]["exit_code"] == 0
    assert fake.last.argv == ["/usr/bin/notes", "--query", "budget", "--limit", "3", "--", "n.md"]


def test_a_failing_cli_tool_raises_instead_of_logging_a_success_receipt(tmp_path):
    """绝不能把失败包装成成功：调用方拿到的必须是异常，不是 executed:True。"""
    adapter, _ = _runner(ExecOutcome(returncode=7, stderr="fatal: bad note index"))
    registry = _registry(tmp_path)
    adapter.register_tools(registry)
    with pytest.raises(ValidationFailed) as err:
        registry.invoke("cli.notes.search", {"query": "q", "limit": "1", "path": "n.md"})
    assert err.value.code == ERR_EXIT_NONZERO
    assert "fatal: bad note index" in err.value.message
    assert registry.recent_calls(5) == []  # 没有伪造的成功回执落盘


def test_cli_tool_without_a_live_adapter_fails_closed(tmp_path):
    """重启后活适配器没重新装配 → 诚实拒绝，不是「静默成功」。"""
    registry = _registry(tmp_path)
    registry.register(name="cli.notes.search", description="d",
                      parameters=spec_json_schema(_spec()),
                      entry={"type": "cli", "runner": "notes.search"})
    with pytest.raises(ValidationFailed) as err:
        registry.invoke("cli.notes.search", {"query": "q", "limit": "1", "path": "n.md"})
    assert err.value.code == "cli_runner_not_attached"


def test_cli_tool_rejects_params_the_spec_never_declared_before_executing(tmp_path):
    """未声明参数必须在**起进程之前**被挡住。

    两层都在，断言的是实际生效的那层：``spec_json_schema`` 声明了
    ``additionalProperties: False``，但 registry 的最小 JSON Schema 子集
    （``tool_registry.validate_arguments``）本来就不校验这个键——真正的闸门是
    ``build_argv`` 的 :data:`ERR_UNKNOWN_PARAM`，它保证执行器一次都没被调用。
    """
    adapter, fake = _runner()
    registry = _registry(tmp_path)
    adapter.register_tools(registry)
    with pytest.raises(ValidationFailed) as err:
        registry.invoke("cli.notes.search",
                        {"query": "q", "limit": "1", "path": "n.md", "smuggled": "x"})
    assert err.value.code == ERR_UNKNOWN_PARAM
    assert fake.requests == []


# --------------------------------------------------------------------------- #
# 5) 默认执行器：形状检查（不起进程）
# --------------------------------------------------------------------------- #


def test_default_executor_is_used_when_none_is_injected():
    adapter = CliRunnerAdapter([_spec()])
    assert adapter._executor is popen_executor


def test_default_executor_reports_a_missing_binary_instead_of_raising():
    outcome = popen_executor(ExecRequest(
        argv=["definitely-not-a-real-binary-xyz", "--flag", "v"],
        timeout_seconds=1.0,
        max_output_bytes=1024,
    ))
    assert outcome.error_code == ERR_EXECUTABLE_NOT_FOUND
    assert outcome.returncode is None


def test_exec_request_carries_argv_as_a_list_not_a_string():
    """回归护栏：一旦有人把 argv 改成字符串命令，这条会红。"""
    request = ExecRequest(argv=["/bin/echo", "hi"], timeout_seconds=1.0, max_output_bytes=8)
    assert isinstance(request.argv, list)
    assert all(isinstance(part, str) for part in request.argv)


# --------------------------------------------------------------------------- #
# 6) 真起进程端到端（默认不跑）
# --------------------------------------------------------------------------- #
#
# 唯一需要真 subprocess 的用例：验证默认执行器在真实进程上确实拿到退出码/
# 超时/输出上限。用 `sys.executable -c` 起本机解释器，不依赖任何外部 CLI。
# 默认 skip，由 FY_CLI_RUNNER_E2E=1 显式打开——与仓库既有的 opt-in 集成门
# （见 tests/integration/test_api_temporal_http.py 的 FY_RUN_HTTP_TEMPORAL）
# 同一形态。本仓库 pyproject 未注册 `markers`，故不挂未注册的 mark 免得刷警告。


def _skip_unless_e2e() -> None:
    if os.environ.get("FY_CLI_RUNNER_E2E") != "1":
        pytest.skip("FY_CLI_RUNNER_E2E!=1; real-subprocess CLI runner E2E skipped")


def test_e2e_real_process_exit_code_and_output_are_reported_truthfully():
    _skip_unless_e2e()
    spec = CliCommandSpec(
        name="py.echo",
        executable=sys.executable,
        option_params={"text": "-c"},
        description="python -c <text>",
    )
    adapter = CliRunnerAdapter([spec])
    ok = adapter.run("py.echo", {"text": "print('hello')"})
    assert ok.ok is True and ok.stdout.strip() == "hello"

    bad = adapter.run("py.echo", {"text": "import sys; sys.stderr.write('boom'); sys.exit(4)"})
    assert bad.ok is False
    assert bad.error_code == ERR_EXIT_NONZERO
    assert bad.exit_code == 4
    assert "boom" in bad.stderr


def test_e2e_real_process_timeout_is_killed_and_reported():
    _skip_unless_e2e()
    spec = CliCommandSpec(
        name="py.sleep",
        executable=sys.executable,
        option_params={"seconds": "-c"},
        timeout_seconds=1.0,
        max_output_bytes=64 * 1024,
    )
    adapter = CliRunnerAdapter([spec])
    result = adapter.run("py.sleep", {"seconds": "import time; time.sleep(30)"},
                         timeout_seconds=0.5)
    assert result.ok is False
    assert result.error_code == ERR_TIMEOUT
    assert result.timed_out is True


def test_e2e_real_process_output_cap_is_enforced():
    _skip_unless_e2e()
    spec = CliCommandSpec(
        name="py.flood",
        executable=sys.executable,
        option_params={"script": "-c"},
        max_output_bytes=1024,
        timeout_seconds=30.0,
    )
    adapter = CliRunnerAdapter([spec])
    result = adapter.run("py.flood", {"script": "print('x' * 200000)"})
    assert result.ok is False
    assert result.error_code == ERR_OUTPUT_LIMIT
