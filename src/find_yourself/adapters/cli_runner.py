"""CLI Runner 适配器：在本机命令行上跑一个**受控**的 CLI 任务。

与既有适配器同源的边界（见 ``services/hub/adapters.py`` 的 ``MarketSourceAdapter``
git 形态与 ``adapters/hermes_adapter.py``）：**只做运输，不做信任判定**。

四条硬边界，任何一条被绕过都等于给了一个远程可达的任意命令执行面：

1. **argv 列表，永不 shell。** 命令是**注册时声明**的 :class:`CliCommandSpec`，
   调用方只能选*已注册的命令名*并填充**已声明的参数槽**——可执行文件名、
   固定参数、选项名（``--flag``）全部来自注册方，不来自调用参数。因此不存在
   「用户输入拼进命令字符串」这条路：``subprocess`` 收到的是 ``list[str]``，
   ``shell=False``，参数与选项天然分隔；位置参数一律排在 ``--`` 之后，
   即使用户传了 ``--rm-rf`` 也只会被当成普通位置参数。
2. **硬超时。** 超时后 ``kill()`` 并等待回收，默认
   :data:`DEFAULT_TIMEOUT_SECONDS`，硬顶 :data:`MAX_TIMEOUT_SECONDS`，
   调用方无法把它调到无限。
3. **输出体积上限。** 默认执行器把 stdout/stderr 落到临时文件，先 ``stat`` 出
   体积再决定读多少——上限挡的是**内存**，不是磁盘。超限与超时给的是
   **可区分**错误码（:data:`ERR_OUTPUT_LIMIT` / :data:`ERR_TIMEOUT`）。
4. **可注入执行器。** ``executor=`` 与 ``WebhookAdapter(transport=...)`` /
   ``MarketSourceAdapter(runner=...)`` 是同一条约定：单测注入假执行器，
   **不真起进程、不打真网**，超时/超限/非零退出码都能确定性断言。

诚实失败（总纲铁律 3）：

* 成功与否**只看退出码**。退出码非 0 → ``ok=False`` + :data:`ERR_EXIT_NONZERO`，
  stderr 原样回传。
* **空输出不等于成功，也不等于失败**：``exit 0`` + 空 stdout 仍是成功，
  ``exit 3`` + 空 stdout 仍是失败。绝不拿「有输出」当成功理由。
* 输出超限**不静默截断成成功**：``ok=False`` + :data:`ERR_OUTPUT_LIMIT`。
* 可执行文件不存在 → :data:`ERR_EXECUTABLE_NOT_FOUND`，不假装跑过。

接入点选型（为什么不新开 Hub kind）：CLI Runner 是**工具执行器**而不是
「一种连接」——它没有可被探测的端点、没有远端能力发现、没有凭证面，
而 ``HubAdapter`` 协议要求的 ``health / capabilities / invoke`` 三件套对它
是虚的。因此它以 ``tool_registry`` 的 ``entry.type="cli"`` 形态接入既有
工具注册/执行体系（与 ``builtin`` / ``http`` / ``mcp`` 并列），
**不新增 Hub kind、不建迁移**。详见 :func:`register_cli_tools`。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..services.errors import ValidationFailed

# --------------------------------------------------------------------------- #
# 预算常量：默认值是「保守默认」，硬顶是「调用方也越不过去的天花板」
# --------------------------------------------------------------------------- #

#: 单次执行默认硬超时（秒）。
DEFAULT_TIMEOUT_SECONDS = 30.0
#: 超时硬顶（秒）。调用方给的 timeout 一律被夹在这个区间内。
MAX_TIMEOUT_SECONDS = 600.0
#: 单次执行的 stdout+stderr 合计上限（字节）。
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
#: 输出体积硬顶（字节）。
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
#: 单个参数值的字符上限（防把 argv 撑爆）。
MAX_PARAM_CHARS = 4096

# --------------------------------------------------------------------------- #
# 错误码：超时与超限必须能分开看，不能都归成「执行失败」
# --------------------------------------------------------------------------- #

ERR_SPEC_INVALID = "cli_runner_spec_invalid"
ERR_UNKNOWN_COMMAND = "cli_runner_unknown_command"
ERR_UNKNOWN_PARAM = "cli_runner_unknown_param"
ERR_MISSING_PARAM = "cli_runner_missing_param"
ERR_INVALID_PARAM = "cli_runner_invalid_param"
ERR_PARAM_TOO_LONG = "cli_runner_param_too_long"
ERR_BAD_CWD = "cli_runner_bad_cwd"
ERR_EXECUTABLE_NOT_FOUND = "cli_runner_executable_not_found"
ERR_SPAWN_FAILED = "cli_runner_spawn_failed"
ERR_EXECUTION_ERROR = "cli_runner_execution_error"
ERR_TIMEOUT = "cli_runner_timeout"
ERR_OUTPUT_LIMIT = "cli_runner_output_limit"
ERR_EXIT_NONZERO = "cli_runner_exit_nonzero"

#: 命令名形态：与 ``tool_registry.TOOL_NAME_RE`` 同约束，工具名可直接复用它。
_CMD_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
#: 参数名形态：只允许小写字母/数字/下划线，杜绝任何分隔符混进参数名。
_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: 出现在参数值里就必须拒绝的控制字符（纵深防御：argv 本身已不需要它）。
_CONTROL_CHARS = ("\x00", "\r", "\n")


# --------------------------------------------------------------------------- #
# 值类型
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CliCommandSpec:
    """一条**注册期**声明的受控 CLI 命令。

    调用方能影响的只有 ``option_params`` / ``positional_params`` 里声明过的
    槽位；``executable`` 与 ``fixed_args`` 是注册方（运维/代码）说了算。

    ``option_params`` 的值必须是 ``-`` 开头的选项字面量：它就是 argv 里的
    一个独立元素，与它后面的取值分开传递——不拼字符串，所以用户值里的
    空格、引号、分号都不可能被重新解释。
    """

    name: str
    executable: str
    fixed_args: tuple[str, ...] = ()
    option_params: Mapping[str, str] = field(default_factory=dict)
    positional_params: tuple[str, ...] = ()
    description: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    cwd: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _CMD_NAME_RE.fullmatch(self.name):
            raise ValidationFailed(
                ERR_SPEC_INVALID,
                f"命令名 '{self.name}' 不合法（需匹配 {_CMD_NAME_RE.pattern}）",
            )
        if not isinstance(self.executable, str) or not self.executable.strip():
            raise ValidationFailed(
                ERR_SPEC_INVALID, f"命令 '{self.name}' 缺少 executable"
            )
        if "\x00" in self.executable:
            raise ValidationFailed(ERR_SPEC_INVALID, "executable 含非法字符")
        if not isinstance(self.fixed_args, tuple) or any(
            not isinstance(a, str) or "\x00" in a for a in self.fixed_args
        ):
            raise ValidationFailed(
                ERR_SPEC_INVALID, f"命令 '{self.name}' 的 fixed_args 必须是字符串元组"
            )
        options = dict(self.option_params or {})
        for param, flag in options.items():
            if not isinstance(param, str) or not _PARAM_NAME_RE.fullmatch(param):
                raise ValidationFailed(
                    ERR_SPEC_INVALID, f"参数名 '{param}' 不合法（需匹配 {_PARAM_NAME_RE.pattern}）"
                )
            if (
                not isinstance(flag, str)
                or not flag.startswith("-")
                or flag == "--"
                or "\x00" in flag
                # 本适配器的契约是「选项与取值是两个独立元素」。允许 `--opt=value`
                # 形态就等于把这个契约让出一半（值里带 = 的语义交给 CLI 自己猜），
                # 因此在注册期就拒绝。
                or "=" in flag
                or any(ch.isspace() for ch in flag)
            ):
                raise ValidationFailed(
                    ERR_SPEC_INVALID,
                    f"参数 '{param}' 的选项 '{flag}' 必须是单个 '-' 开头的选项字面量"
                    "（取值作为独立的下一个 argv 元素，不支持 --opt=value 形态）",
                )
        positional = tuple(self.positional_params or ())
        for param in positional:
            if not isinstance(param, str) or not _PARAM_NAME_RE.fullmatch(param):
                raise ValidationFailed(
                    ERR_SPEC_INVALID, f"参数名 '{param}' 不合法（需匹配 {_PARAM_NAME_RE.pattern}）"
                )
        overlap = sorted(set(options) & set(positional))
        if overlap:
            raise ValidationFailed(
                ERR_SPEC_INVALID, f"命令 '{self.name}' 的参数同时被声明为选项与位置参数：{overlap}"
            )
        if not (0 < float(self.timeout_seconds) <= MAX_TIMEOUT_SECONDS):
            raise ValidationFailed(
                ERR_SPEC_INVALID,
                f"命令 '{self.name}' 的 timeout_seconds 必须落在 (0, {MAX_TIMEOUT_SECONDS}] 内",
            )
        if not (0 < int(self.max_output_bytes) <= MAX_OUTPUT_BYTES):
            raise ValidationFailed(
                ERR_SPEC_INVALID,
                f"命令 '{self.name}' 的 max_output_bytes 必须落在 (0, {MAX_OUTPUT_BYTES}] 内",
            )


@dataclass(frozen=True)
class ExecRequest:
    """一次执行的全部输入（执行器唯一需要知道的东西）。"""

    argv: list[str]
    timeout_seconds: float
    max_output_bytes: int
    cwd: str | None = None


#: 报错文本里附带的 stderr 摘录长度上限：够定位问题，又不至于把整段噪声
#: 塞进异常消息或日志。
STDERR_EXCERPT_CHARS = 500


def _stderr_excerpt(text: str) -> str:
    """取一段 stderr 摘录（单行化 + 截断），供错误消息使用。"""
    collapsed = " ".join(str(text or "").split())
    if not collapsed:
        return ""
    if len(collapsed) > STDERR_EXCERPT_CHARS:
        return collapsed[:STDERR_EXCERPT_CHARS] + "…（已截断）"
    return collapsed


@dataclass(frozen=True)
class ExecOutcome:
    """执行器的回执。**成功与否不在这里判定**——退出码由适配器裁决。"""

    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    output_exceeded: bool = False
    #: 仅在执行器**起不了进程**时填（如可执行文件不存在）；此时 returncode 无意义。
    error_code: str = ""
    error: str = ""


@dataclass(frozen=True)
class CliRunResult:
    """一次 CLI 任务的对外回执（tool_registry 收的就是它的 ``to_public()``）。"""

    ok: bool
    command: str
    argv: list[str] = field(default_factory=list)
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error_code: str = ""
    error: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    output_truncated: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error_code": self.error_code,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
        }


#: 执行器签名：``(ExecRequest) -> ExecOutcome``。可注入是为了单测不真起进程
#: （与 ``GitRunner`` 同一动机，只是多带了超时与体积预算）。
CliExecutor = Callable[[ExecRequest], ExecOutcome]


# --------------------------------------------------------------------------- #
# argv 组装：这是安全边界的所在地
# --------------------------------------------------------------------------- #


def _param_text(param: str, value: Any) -> str:
    """把调用参数转成 argv 里那**一个**元素。

    允许 str/int/float/bool（bool 归一成 ``true``/``false``）；容器类型一律拒绝
    ——多值参数应当在 spec 里拆成多个槽，而不是塞一个 list 进来。
    """
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ValidationFailed(
            ERR_INVALID_PARAM,
            f"参数 '{param}' 必须是字符串/数字/布尔值，收到 {type(value).__name__}",
        )
    if any(ch in text for ch in _CONTROL_CHARS):
        raise ValidationFailed(ERR_INVALID_PARAM, f"参数 '{param}' 含非法控制字符")
    if len(text) > MAX_PARAM_CHARS:
        raise ValidationFailed(
            ERR_PARAM_TOO_LONG,
            f"参数 '{param}' 长度 {len(text)} 超过上限 {MAX_PARAM_CHARS}",
        )
    return text


def build_argv(spec: CliCommandSpec, arguments: Mapping[str, Any] | None) -> list[str]:
    """按 spec 组装 argv 列表。

    * 未知参数 → :data:`ERR_UNKNOWN_PARAM`（绝不「顺手忽略」用户传进来的东西）。
    * 选项参数的取值与选项名是**两个独立元素**，中间不拼字符串。
    * 位置参数排在 ``--`` 之后：即使用户把 ``--dangerous-flag`` 填进位置参数槽，
      子进程也只会把它当成普通实参，而不是选项。
    """
    params = dict(arguments or {})
    allowed = set(spec.option_params) | set(spec.positional_params)
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValidationFailed(
            ERR_UNKNOWN_PARAM,
            f"命令 '{spec.name}' 不接受这些参数：{unknown}（已声明：{sorted(allowed)}）",
        )
    argv: list[str] = [spec.executable, *spec.fixed_args]
    for param, flag in spec.option_params.items():
        if param not in params:
            raise ValidationFailed(ERR_MISSING_PARAM, f"命令 '{spec.name}' 缺少参数 '{param}'")
        argv.append(flag)
        argv.append(_param_text(param, params[param]))
    if spec.positional_params:
        # 选项解析终止符：之后的实参绝不会被 CLI 当成选项（参数注入的最后一道闸）。
        argv.append("--")
        for param in spec.positional_params:
            if param not in params:
                raise ValidationFailed(ERR_MISSING_PARAM, f"命令 '{spec.name}' 缺少参数 '{param}'")
            argv.append(_param_text(param, params[param]))
    return argv


def spec_json_schema(spec: CliCommandSpec) -> dict[str, Any]:
    """spec → tool_registry 用的 JSON Schema（参数一律 string，required 全量）。"""
    names = [*spec.option_params, *spec.positional_params]
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in names},
        "required": names,
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------- #
# 默认执行器：真起进程的唯一地方
# --------------------------------------------------------------------------- #


def _file_size(handle: Any) -> int:
    handle.seek(0, os.SEEK_END)
    return int(handle.tell())


def _read_capped(handle: Any, limit: int) -> str:
    handle.seek(0)
    # errors="replace"：截断点落在多字节字符中间时也不抛异常，但字节会被
    # 替换符显式标出来，不会静默吞掉半个字。
    return (handle.read(limit) or b"").decode("utf-8", errors="replace")


def popen_executor(request: ExecRequest) -> ExecOutcome:
    """默认执行器：``subprocess.Popen(argv 列表)``，**shell=False**，无任何字符串拼接。

    关键取舍：stdout/stderr 落**临时文件**而不是 PIPE。PIPE 要靠额外线程抽干
    才不会死锁，而临时文件让我们可以先 ``stat`` 体积、再决定读多少——
    :data:`ExecRequest.max_output_bytes` 挡住的是内存，不是磁盘。

    ``stdin=DEVNULL``：CLI 若试图交互式提问，立即拿到 EOF 而不是挂到超时。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        out_file = tempfile.TemporaryFile()
        err_file = tempfile.TemporaryFile()
    except OSError as exc:  # 临时目录不可用
        return ExecOutcome(error_code=ERR_SPAWN_FAILED, error=f"无法创建输出缓冲：{exc}")
    try:
        try:
            proc = subprocess.Popen(
                request.argv,
                stdout=out_file,
                stderr=err_file,
                stdin=subprocess.DEVNULL,
                shell=False,
                cwd=request.cwd,
                env=env,
            )
        except FileNotFoundError as exc:
            return ExecOutcome(
                error_code=ERR_EXECUTABLE_NOT_FOUND,
                error=f"找不到可执行文件 '{request.argv[0]}'：{exc}",
            )
        except (OSError, ValueError) as exc:
            return ExecOutcome(
                error_code=ERR_SPAWN_FAILED,
                error=f"无法启动 CLI 进程：{type(exc).__name__}: {exc}",
            )
        timed_out = False
        try:
            proc.wait(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - kill 后极少不回收
                pass
        size = _file_size(out_file) + _file_size(err_file)
        exceeded = size > request.max_output_bytes
        budget = request.max_output_bytes
        return ExecOutcome(
            returncode=proc.returncode,
            stdout=_read_capped(out_file, budget),
            stderr=_read_capped(err_file, budget),
            timed_out=timed_out,
            output_exceeded=exceeded,
        )
    finally:
        for handle in (out_file, err_file):
            try:
                handle.close()
            except OSError:  # pragma: no cover - 关闭失败不该影响回执
                pass


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #


class CliRunnerAdapter:
    """受控 CLI 任务的执行器。注册哪些命令，就只能跑哪些命令。"""

    def __init__(self, specs: list[CliCommandSpec] | None = None, *,
                 executor: CliExecutor | None = None):
        self._specs: dict[str, CliCommandSpec] = {}
        for spec in specs or []:
            self.register(spec)
        self._executor: CliExecutor = executor or popen_executor

    # -- 注册 --------------------------------------------------------------- #

    def register(self, spec: CliCommandSpec) -> CliCommandSpec:
        """登记一条命令。同名命令**拒绝覆盖**：静默改写已授权的可执行文件是提权面。"""
        if spec.name in self._specs:
            raise ValidationFailed(
                ERR_SPEC_INVALID, f"命令 '{spec.name}' 已注册，拒绝被静默覆盖"
            )
        self._specs[spec.name] = spec
        return spec

    def names(self) -> list[str]:
        return sorted(self._specs)

    def specs(self) -> list[CliCommandSpec]:
        return [self._specs[n] for n in self.names()]

    def spec(self, name: str) -> CliCommandSpec:
        spec = self._specs.get(str(name or ""))
        if spec is None:
            raise ValidationFailed(
                ERR_UNKNOWN_COMMAND,
                f"未注册的命令 '{name}'（已注册：{self.names()}）；"
                "CLI Runner 只执行注册期声明过的命令",
            )
        return spec

    # -- 探测 --------------------------------------------------------------- #

    def probe(self, name: str) -> dict[str, Any]:
        """如实报告可执行文件在不在。不在就说不在，不猜。"""
        spec = self.spec(name)
        raw = spec.executable
        if os.path.isabs(raw):
            found = raw if (os.path.isfile(raw) and os.access(raw, os.X_OK)) else None
        else:
            found = shutil.which(raw)
        return {
            "name": spec.name,
            "executable": raw,
            "resolved": found or "",
            "available": bool(found),
            "detail": "" if found else f"PATH 中找不到可执行文件 '{raw}'",
        }

    # -- 执行 --------------------------------------------------------------- #

    def run(self, name: str, arguments: Mapping[str, Any] | None = None, *,
            timeout_seconds: float | None = None) -> CliRunResult:
        """执行一条已注册命令。**永不抛异常**——失败一律进回执的 ``ok=False``。"""
        started = time.perf_counter()
        command = str(name or "")

        def elapsed() -> int:
            return int(round((time.perf_counter() - started) * 1000))

        def fail(code: str, message: str, **extra: Any) -> CliRunResult:
            return CliRunResult(
                ok=False, command=command, error_code=code, error=message,
                duration_ms=elapsed(), **extra,
            )

        try:
            spec = self.spec(command)
        except ValidationFailed as exc:
            return fail(exc.code, exc.message)

        try:
            argv = build_argv(spec, arguments)
        except ValidationFailed as exc:
            return fail(exc.code, exc.message)

        cwd = spec.cwd
        if cwd is not None and not os.path.isdir(cwd):
            return fail(ERR_BAD_CWD, f"命令 '{command}' 的工作目录不存在：{cwd}")

        budget = spec.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            budget = float(budget)
        except (TypeError, ValueError):
            budget = spec.timeout_seconds
        budget = max(0.001, min(budget, MAX_TIMEOUT_SECONDS))

        request = ExecRequest(
            argv=argv,
            timeout_seconds=budget,
            max_output_bytes=spec.max_output_bytes,
            cwd=cwd,
        )
        try:
            outcome = self._executor(request)
        except Exception as exc:  # noqa: BLE001 — 执行器炸了也必须诚实失败，不假装跑过
            return fail(ERR_EXECUTION_ERROR, f"{type(exc).__name__}: {exc}", argv=argv)

        if not isinstance(outcome, ExecOutcome):  # 注入的假执行器也要守契约
            return fail(ERR_EXECUTION_ERROR, "执行器未返回 ExecOutcome", argv=argv)
        if outcome.error_code:
            return fail(
                outcome.error_code, outcome.error or "CLI 进程未能启动", argv=argv,
                exit_code=outcome.returncode,
            )

        # 超时与超限：两个可区分的错误码，都不是成功。
        if outcome.timed_out:
            return CliRunResult(
                ok=False, command=command, argv=argv, exit_code=outcome.returncode,
                stdout=outcome.stdout, stderr=outcome.stderr,
                error_code=ERR_TIMEOUT,
                error=f"CLI 任务超时（>{budget:.3g}s）已被终止",
                duration_ms=elapsed(), timed_out=True,
            )
        if outcome.output_exceeded:
            excerpt = _stderr_excerpt(outcome.stderr)
            return CliRunResult(
                ok=False, command=command, argv=argv, exit_code=outcome.returncode,
                stdout=outcome.stdout, stderr=outcome.stderr,
                error_code=ERR_OUTPUT_LIMIT,
                error=f"CLI 输出超过上限（>{spec.max_output_bytes} 字节），"
                      "拒绝把截断内容当成功结果"
                      + (f"；stderr：{excerpt}" if excerpt else ""),
                duration_ms=elapsed(), output_truncated=True,
            )
        # 成功与否只看退出码：非 0 一律失败，stderr 原样带出。
        # 摘录也写进 error 文本——异常/日志路径上只有 message 可见，
        # 只把 stderr 放在结构化字段里等于「回传了但没人看得见」。
        if outcome.returncode != 0:
            excerpt = _stderr_excerpt(outcome.stderr)
            return CliRunResult(
                ok=False, command=command, argv=argv, exit_code=outcome.returncode,
                stdout=outcome.stdout, stderr=outcome.stderr,
                error_code=ERR_EXIT_NONZERO,
                error=f"CLI 退出码 {outcome.returncode}"
                      + (f"：{excerpt}" if excerpt else "（无 stderr 输出）"),
                duration_ms=elapsed(),
            )
        return CliRunResult(
            ok=True, command=command, argv=argv, exit_code=0,
            stdout=outcome.stdout, stderr=outcome.stderr,
            duration_ms=elapsed(),
        )

    # -- 接入工具注册体系 --------------------------------------------------- #

    def register_tools(self, registry: Any | None = None, *,
                       prefix: str = "cli.") -> list[str]:
        """把每条已注册命令注册成 ``tool_registry`` 里的一个工具。

        形态是 ``entry={"type": "cli", "runner": <命令名>}``，与
        ``builtin`` / ``http`` / ``mcp`` 并列——**不是**新的 Hub kind：
        CLI Runner 没有可探测的端点与能力发现面，硬塞进 ``HubAdapter`` 只会
        造出一个 ``health()`` 永远说不清的三件套，同时逼出一份只为它服务的
        Hub kind 迁移（0050 已占位），得不偿失。真正的风险面——「谁被允许
        跑什么命令」——由本类的注册表回答，不该由 kind 枚举回答。

        活执行器经 ``attach_cli_runner`` 挂进 registry（与
        ``attach_mcp_client`` 同一约定：元数据落盘、活对象不落盘，重启后
        由宿主代码重新装配）。
        """
        if registry is None:
            from ..services.tool_registry import tool_registry as registry

        registered: list[str] = []
        for spec in self.specs():
            tool_name = f"{prefix}{spec.name}"
            registry.register(
                name=tool_name,
                description=spec.description or f"本机 CLI 任务 {spec.name}",
                parameters=spec_json_schema(spec),
                entry={"type": "cli", "runner": spec.name},
            )
            registry.attach_cli_runner(spec.name, self)
            registered.append(tool_name)
        return registered


def register_cli_tools(
    adapter: CliRunnerAdapter,
    registry: Any | None = None,
    *,
    prefix: str = "cli.",
) -> list[str]:
    """模块级快捷入口，语义同 :meth:`CliRunnerAdapter.register_tools`。"""
    return adapter.register_tools(registry, prefix=prefix)
