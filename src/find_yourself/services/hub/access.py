"""统一接入抽象层（A-统一接入-01 / 04 · 补齐包3）。

在六类 hub 连接（``hub.adapters``）之上，为 **MCP / A2A / CLI / 进程内插件**
四路接入源提供**同构**抽象：对上层只暴露一套 API（``health`` / ``capabilities``
/ ``invoke`` / ``describe``），由 :class:`AccessRegistry` 统一注册与调用。
新增接入源 = 实现一个满足 :class:`AccessChannel` 形状的适配器 +
``registry.register(channel_id, adapter)``，无需改动任何调用方。

::

    channel_id  形如 ``mcp:demo`` / ``a2a:research`` / ``cli:peri`` / ``plugin:echo``
                （``<channel>:<source_id>``，进程内注册表键）

四路实现：

* :class:`McpAccessChannel`   —— 复用 :class:`hub.adapters.McpServerAdapter`
  （stdio / http / sse / ws + 信任分级 + 动态刷新），见 ``adapters/mcp.py``。
* :class:`A2AAccessChannel`   —— 出站 A2A（``adapters/a2a.A2AClient``），
  **必须**命中服务端可信端点注册表（TrustedEndpointRegistry），空注册表即拒绝
  ——SSRF 防线沿用既有实现，本层不另起炉灶。
* :class:`CliChannelAdapter`  —— 通用 CLI 契约（A-统一接入-04）：统一
  超时（deadline 后杀进程树并验证回收）、统一退出码语义（0 成功 / 124 超时 /
  其余失败）、统一输出回传；实现体 :func:`run_cli_process` 由社区 harness
  （``community_harness_adapter._peri_execute``）同源复用。
* :class:`PluginChannelAdapter` —— 进程内插件：action -> 处理函数表。

诚实原则与 hub 一致：探测不到就是 ``ok=False``，调不到就是失败
``InvokeResult``；绝不构造占位数据。
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .adapters import (
    Capability,
    HealthReport,
    InvokeCall,
    InvokeResult,
)

# --------------------------------------------------------------------------- #
# 通道常量
# --------------------------------------------------------------------------- #

CHANNEL_MCP = "mcp"
CHANNEL_A2A = "a2a"
CHANNEL_CLI = "cli"
CHANNEL_PLUGIN = "plugin"

#: 四路接入通道（A-统一接入-01）。新增接入源在此登记常量即可。
ACCESS_CHANNELS: tuple[str, ...] = (CHANNEL_MCP, CHANNEL_A2A, CHANNEL_CLI, CHANNEL_PLUGIN)


class AccessChannel(Protocol):
    """四路接入源的同构契约（与 HubAdapter 的 health/capabilities/invoke 同形）。

    新增接入源只需实现本形状 + :meth:`AccessRegistry.register`。
    """

    channel: str
    source_id: str

    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport: ...

    def capabilities(self) -> list[Capability]: ...

    def invoke(self, call: InvokeCall) -> InvokeResult: ...


def _elapsed(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _fail(started: float, error: str) -> InvokeResult:
    return InvokeResult(ok=False, error=error, latency_ms=_elapsed(started))


# --------------------------------------------------------------------------- #
# 1) MCP 通道
# --------------------------------------------------------------------------- #


class McpAccessChannel:
    """MCP 接入通道：包装 :class:`hub.adapters.McpServerAdapter`。"""

    channel = CHANNEL_MCP

    def __init__(self, config: dict[str, Any], *, client: Any | None = None,
                 trust_policy: Any | None = None):
        from .adapters import KIND_MCP_SERVER, McpServerAdapter

        cfg = dict(config or {})
        cfg["kind"] = KIND_MCP_SERVER
        self._adapter = McpServerAdapter(cfg, client=client, trust_policy=trust_policy)
        self.kind = CHANNEL_MCP
        self.source_id = self._adapter.server_key()

    # -- HubAdapter 同构面 ----------------------------------------------------- #
    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
        return self._adapter.health(timeout_seconds=timeout_seconds)

    def capabilities(self) -> list[Capability]:
        caps = list(self._adapter.capabilities())
        caps.append(Capability(name="mcp.refresh", tags=("mcp", "refresh"),
                               description="重新发现 MCP 工具清单（list_changed / refreshTools）"))
        return caps

    def invoke(self, call: InvokeCall) -> InvokeResult:
        started = time.perf_counter()
        action = (call.action or "call_tool").strip().lower()
        try:
            if action in {"call_tool", "tools"}:
                return self._adapter.invoke(call)
            client = self._adapter._connect()
            if action == "refresh":
                tools = self.refresh()
                return InvokeResult(ok=True, output={"tools": tools},
                                    latency_ms=_elapsed(started))
            if action == "list_resources":
                return InvokeResult(ok=True, output={"resources": client.list_resources()},
                                    latency_ms=_elapsed(started))
            if action == "read_resource":
                uri = str(call.params.get("uri") or "")
                if not uri:
                    return _fail(started, "hub_missing_uri: 缺少 uri 参数")
                return InvokeResult(ok=True, output=client.read_resource(uri),
                                    latency_ms=_elapsed(started))
            if action == "list_prompts":
                return InvokeResult(ok=True, output={"prompts": client.list_prompts()},
                                    latency_ms=_elapsed(started))
            if action == "get_prompt":
                name = str(call.params.get("name") or "")
                if not name:
                    return _fail(started, "hub_missing_prompt: 缺少 name 参数")
                return InvokeResult(
                    ok=True,
                    output=client.get_prompt(name, call.params.get("arguments") or {}),
                    latency_ms=_elapsed(started))
            return _fail(started, f"hub_unsupported_action: mcp 通道不支持 action='{action}'")
        except Exception as exc:  # noqa: BLE001 — 失败即失败
            return _fail(started, f"{type(exc).__name__}: {exc}")

    # -- MCP 专有 ------------------------------------------------------------ #
    def refresh(self) -> list[dict[str, Any]]:
        return self._adapter.refresh()

    def register_tools(self, registry: Any | None = None) -> list[str]:
        return self._adapter.register_tools(registry)

    def describe(self) -> dict[str, Any]:
        return {"channel": self.channel, "source_id": self.source_id,
                "kind": self.kind, "description": "MCP 服务器（stdio/http/sse/ws）"}


# --------------------------------------------------------------------------- #
# 2) A2A 通道（出站；SSRF 防线 = TrustedEndpointRegistry）
# --------------------------------------------------------------------------- #


class A2AAccessChannel:
    """出站 A2A 接入通道：指向可信注册表里的一个外部成品 agent。"""

    channel = CHANNEL_A2A

    def __init__(self, config: dict[str, Any], *, client: Any | None = None):
        from ...adapters.a2a import A2AClient

        self.config = dict(config or {})
        self.kind = CHANNEL_A2A
        self.source_id = str(self.config.get("endpoint_key") or "").strip()
        if not self.source_id:
            from ..errors import ValidationFailed

            raise ValidationFailed(
                "hub_a2a_missing_endpoint",
                "A2A 通道需要 endpoint_key（可信注册表中的端点键或精确 base_url）",
            )
        # 默认空注册表：任何目标都会被 UntrustedEndpointError 拒绝——
        # 调用方必须显式注册可信端点，本层不提供绕过。
        self._client = client or A2AClient()

    def _endpoint(self) -> str:
        return self.source_id

    def _message(self, params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message")
        if isinstance(message, dict) and message.get("role") and message.get("parts"):
            return message
        text = str(params.get("text") or "").strip()
        if not text:
            from ..errors import ValidationFailed

            raise ValidationFailed(
                "hub_a2a_missing_message",
                "A2A 调用需要 message（role+parts）或 text 参数",
            )
        return {"role": "user", "parts": [{"kind": "text", "text": text}]}

    # -- HubAdapter 同构面 ----------------------------------------------------- #
    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
        started = time.perf_counter()
        try:
            card = self._client.get_agent_card(self._endpoint())
        except Exception as exc:  # noqa: BLE001 — 不可达即不可用
            return HealthReport(ok=False, latency_ms=_elapsed(started),
                                detail=f"{type(exc).__name__}: {exc}",
                                checked_at=self._stamp(), endpoint_ref=self.source_id)
        return HealthReport(
            ok=True, latency_ms=_elapsed(started),
            detail=f"A2A 已连接：{card.get('name', '?')} v{card.get('version', '?')}",
            checked_at=self._stamp(), endpoint_ref=self.source_id,
            capabilities=self.capabilities(),
        )

    def capabilities(self) -> list[Capability]:
        return [
            Capability(name="a2a.send_message", tags=("a2a", "agent", self.source_id),
                       description=f"向 {self.source_id} 提交 A2A 任务（message/send）"),
            Capability(name="a2a.get_task", tags=("a2a", "agent", self.source_id),
                       description="轮询 A2A 任务状态（tasks/get）"),
            Capability(name="a2a.cancel_task", tags=("a2a", "agent", self.source_id),
                       description="取消 A2A 任务（tasks/cancel）"),
        ]

    def invoke(self, call: InvokeCall) -> InvokeResult:
        started = time.perf_counter()
        action = (call.action or "send_message").strip().lower()
        try:
            if action in {"send_message", "message_send", "message/send"}:
                result = self._client.send_message(
                    self._endpoint(), self._message(dict(call.params)),
                    timeout=float(call.timeout_seconds or 10.0),
                )
                return InvokeResult(ok=True, output=result, latency_ms=_elapsed(started),
                                    meta={"endpoint": self.source_id})
            if action in {"get_task", "tasks_get", "tasks/get"}:
                task_id = str(call.params.get("task_id") or call.params.get("id") or "")
                if not task_id:
                    return _fail(started, "hub_missing_task_id: 缺少 task_id 参数")
                result = self._client.get_task(self._endpoint(), task_id)
                return InvokeResult(ok=True, output=result, latency_ms=_elapsed(started))
            if action in {"cancel_task", "tasks_cancel", "tasks/cancel"}:
                task_id = str(call.params.get("task_id") or call.params.get("id") or "")
                if not task_id:
                    return _fail(started, "hub_missing_task_id: 缺少 task_id 参数")
                result = self._client.cancel_task(self._endpoint(), task_id)
                return InvokeResult(ok=True, output=result, latency_ms=_elapsed(started))
            return _fail(started, f"hub_unsupported_action: a2a 通道不支持 action='{action}'")
        except Exception as exc:  # noqa: BLE001
            return _fail(started, f"{type(exc).__name__}: {exc}")

    def describe(self) -> dict[str, Any]:
        return {"channel": self.channel, "source_id": self.source_id,
                "kind": self.kind, "description": "外部成品 agent（A2A 可信端点）"}

    def _stamp(self) -> str:
        from ...db.types import utcnow

        return utcnow().isoformat()


# --------------------------------------------------------------------------- #
# 3) CLI 通道 —— 通用 CLI 契约（A-统一接入-04）
# --------------------------------------------------------------------------- #

#: 统一退出码语义：0 成功；124 超时（与社区 harness 契约一致）；其余失败。
CLI_EXIT_OK = 0
CLI_EXIT_TIMEOUT = 124

_CLI_SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


@dataclass(frozen=True)
class CliContract:
    """CLI 接入通道契约（从 community_harness_adapter 的 subprocess 用法提炼）。

    * ``command`` 必须是字符串数组（argv 形式）；契约**禁用** ``shell=True``。
    * ``timeout_seconds`` 到期后杀整棵进程树并验证回收，退出码记 124。
    * ``env_whitelist`` 指名透传的宿主环境变量（默认不透传任何含敏感词的键）。
    """

    command: tuple[str, ...]
    timeout_seconds: float = 30.0
    cwd: str = ""
    env_whitelist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.command, tuple) or not self.command \
                or not all(isinstance(c, str) and c.strip() for c in self.command):
            from ..errors import ValidationFailed

            raise ValidationFailed(
                "cli_invalid_command", "CLI 契约需要非空字符串数组 command（argv 形式，禁 shell=True）"
            )
        if self.timeout_seconds <= 0:
            from ..errors import ValidationFailed

            raise ValidationFailed("cli_invalid_timeout", "CLI 契约 timeout_seconds 必须为正数")


def build_child_env(env_whitelist: tuple[str, ...] = (),
                    env: dict[str, str] | None = None) -> dict[str, str]:
    """按白名单构造子进程环境：默认剥掉全部宿主变量；敏感词键永不透传。"""
    import os

    child: dict[str, str] = {}
    for key in env_whitelist:
        upper = key.upper()
        if any(hint in upper for hint in _CLI_SECRET_ENV_HINTS):
            continue  # 硬规则：敏感词键即使点名也不透传
        if key in os.environ:
            child[key] = os.environ[key]
    if env:
        child.update({str(k): str(v) for k, v in env.items()})
    return child


def run_cli_process(
    command: list[str] | tuple[str, ...],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
    on_spawn: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """统一 CLI 执行（A-统一接入-04 实现体）。

    Popen(argv) + communicate(deadline) + 超时杀整树并验证回收（复用社区
    harness 的 ``kill_process_tree``，单一事实源）+ 统一退出码语义。

    Returns ``{ok, exit_code, stdout, stderr, timed_out, reaped, duration_ms}``。
    """
    from ...adapters.community_harness_adapter import kill_process_tree

    import os

    started = time.perf_counter()
    popen_kw: dict[str, Any] = {}
    if os.name != "nt":
        # POSIX 用进程组隔离，便于整树回收；Windows 走 taskkill /T（见下）。
        popen_kw["start_new_session"] = True
    proc = subprocess.Popen(  # noqa: S603 — argv 形式，契约禁 shell=True
        list(command),
        cwd=cwd or None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **popen_kw,
    )
    if on_spawn is not None:
        on_spawn(proc)
    timed_out = False
    reaped: bool | None = None
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        evidence = kill_process_tree(proc.pid)
        reaped = bool(evidence.get("reaped"))
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except Exception:  # noqa: BLE001 — 树已死，输出拿不到就如实置空
            stdout, stderr = "", ""
        exit_code = CLI_EXIT_TIMEOUT
    duration_ms = int(round((time.perf_counter() - started) * 1000))
    return {
        "ok": exit_code == CLI_EXIT_OK,
        "exit_code": exit_code,
        "stdout": stdout or "",
        "stderr": stderr or "",
        "timed_out": timed_out,
        "reaped": reaped,
        "duration_ms": duration_ms,
    }


class CliChannelAdapter:
    """CLI 接入通道：把一个外部命令行工具接入统一接入层。"""

    channel = CHANNEL_CLI

    def __init__(self, config: dict[str, Any], *, runner: Any | None = None):
        from ..errors import ValidationFailed

        cfg = dict(config or {})
        self.kind = CHANNEL_CLI
        self.source_id = str(cfg.get("source_id") or (cfg.get("command") or ["cli"])[0])
        command = cfg.get("command")
        if not (isinstance(command, list) and command
                and all(isinstance(c, str) for c in command)):
            raise ValidationFailed(
                "cli_invalid_command", "CLI 通道需要非空字符串数组 command（argv 形式）")
        self._contract = CliContract(
            command=tuple(command),
            timeout_seconds=float(cfg.get("timeout_seconds") or 30.0),
            cwd=str(cfg.get("cwd") or ""),
            env_whitelist=tuple(cfg.get("env_whitelist") or ()),
        )
        # runner 供测试注入；生产 = run_cli_process。
        self._runner = runner or run_cli_process

    # -- HubAdapter 同构面 ----------------------------------------------------- #
    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
        """CLI 契约的健康探测是**静态**的：校验 argv 与可执行文件存在。

        不执行用户命令（副作用不可控）；执行情况由 invoke 的退出码如实回传。
        """
        from ...db.types import utcnow

        argv0 = self._contract.command[0]
        resolved = shutil.which(argv0)
        ok = resolved is not None
        return HealthReport(
            ok=ok,
            detail=(f"可执行文件已找到：{resolved}" if ok
                    else f"可执行文件未找到：{argv0}（PATH 探测；未执行该命令）"),
            checked_at=utcnow().isoformat(),
            endpoint_ref=f"cli:{argv0}",
            capabilities=self.capabilities() if ok else [],
        )

    def capabilities(self) -> list[Capability]:
        return [
            Capability(name="cli.run", tags=("cli", self.source_id),
                       description=f"执行 CLI 工具 {self.source_id}（统一超时/退出码）"),
        ]

    def invoke(self, call: InvokeCall) -> InvokeResult:
        started = time.perf_counter()
        action = (call.action or "run").strip().lower()
        if action != "run":
            return _fail(started, f"hub_unsupported_action: cli 通道不支持 action='{action}'")
        params = dict(call.params or {})
        extra_args = [str(a) for a in (params.get("args") or [])]
        command = list(self._contract.command) + extra_args
        env = build_child_env(self._contract.env_whitelist,
                              params.get("env") if isinstance(params.get("env"), dict) else None)
        timeout = float(params.get("timeout_seconds") or self._contract.timeout_seconds)
        try:
            result = self._runner(
                command, cwd=self._contract.cwd or None, env=env,
                timeout_seconds=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(started, f"{type(exc).__name__}: {exc}")
        ok = bool(result.get("ok"))
        meta = {
            "exit_code": result.get("exit_code"),
            "timed_out": result.get("timed_out"),
            "reaped": result.get("reaped"),
            "duration_ms": result.get("duration_ms"),
        }
        error = ""
        if result.get("timed_out"):
            error = f"CLI 超时（deadline={timeout}s），进程树已回收（exit {CLI_EXIT_TIMEOUT}）"
        elif not ok:
            error = f"CLI 退出码 {result.get('exit_code')}：{(result.get('stderr') or result.get('stdout') or '').strip()[:500]}"
        return InvokeResult(
            ok=ok,
            output={"stdout": result.get("stdout", ""), "stderr": result.get("stderr", "")},
            error=error,
            latency_ms=_elapsed(started),
            meta=meta,
        )

    def describe(self) -> dict[str, Any]:
        return {"channel": self.channel, "source_id": self.source_id,
                "kind": self.kind, "description": "CLI 工具（统一超时/退出码契约）"}


# --------------------------------------------------------------------------- #
# 4) 进程内插件通道
# --------------------------------------------------------------------------- #


class PluginChannelAdapter:
    """进程内插件接入通道：``action -> handler`` 表，零网络、零子进程。

    新接入源实现一个 handler（``(action, params) -> Any``）即完成接入。
    """

    channel = CHANNEL_PLUGIN

    def __init__(self, config: dict[str, Any], *,
                 handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
                 default_handler: Callable[[str, dict[str, Any]], Any] | None = None):
        cfg = dict(config or {})
        self.kind = CHANNEL_PLUGIN
        self.source_id = str(cfg.get("plugin_id") or cfg.get("source_id") or "plugin")
        self._handlers = {
            str(k).strip().lower(): v for k, v in (handlers or {}).items()
        }
        self._default = default_handler

    def _has_any(self) -> bool:
        return bool(self._handlers) or self._default is not None

    # -- HubAdapter 同构面 ----------------------------------------------------- #
    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
        from ...db.types import utcnow

        return HealthReport(
            ok=self._has_any(),
            detail=("插件已装配（进程内）" if self._has_any() else "插件未装配任何 handler"),
            checked_at=utcnow().isoformat(),
            endpoint_ref=f"plugin:{self.source_id}",
            capabilities=self.capabilities(),
        )

    def capabilities(self) -> list[Capability]:
        actions = [Capability(name=f"plugin.{a}", tags=("plugin", self.source_id),
                              description=f"进程内插件动作 {a}（{self.source_id}）")
                   for a in sorted(self._handlers)]
        if not actions:
            actions = [Capability(name="plugin.run", tags=("plugin", self.source_id),
                                  description=f"进程内插件默认动作（{self.source_id}）")]
        return actions

    def invoke(self, call: InvokeCall) -> InvokeResult:
        started = time.perf_counter()
        action = (call.action or "run").strip().lower()
        handler = self._handlers.get(action, self._default if action == "run" else None)
        if handler is None:
            return _fail(started,
                         f"hub_unsupported_action: 插件 '{self.source_id}' 无 action='{action}' 的 handler")
        try:
            output = handler(action, dict(call.params or {}))
        except Exception as exc:  # noqa: BLE001
            return _fail(started, f"{type(exc).__name__}: {exc}")
        return InvokeResult(ok=True, output=output, latency_ms=_elapsed(started),
                            meta={"plugin": self.source_id, "action": action})

    def describe(self) -> dict[str, Any]:
        return {"channel": self.channel, "source_id": self.source_id,
                "kind": self.kind,
                "description": f"进程内插件（actions: {', '.join(sorted(self._handlers)) or 'default'}）"}


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #


class AccessRegistry:
    """四路接入源统一注册表：新增接入源 = 实现适配器 + register。"""

    def __init__(self) -> None:
        self._channels: dict[str, AccessChannel] = {}

    @staticmethod
    def make_key(channel: str, source_id: str) -> str:
        return f"{channel}:{source_id}"

    def register(self, channel_id: str, adapter: AccessChannel, *,
                 replace: bool = False) -> AccessChannel:
        if not channel_id or ":" not in channel_id:
            raise ValueError("channel_id 需形如 '<channel>:<source_id>'")
        if not hasattr(adapter, "channel") or not hasattr(adapter, "source_id"):
            raise ValueError("适配器必须暴露 channel / source_id / health / capabilities / invoke")
        for attr in ("health", "capabilities", "invoke"):
            if not callable(getattr(adapter, attr, None)):
                raise ValueError(f"适配器缺少同构 API：{attr}()")
        if channel_id in self._channels and not replace:
            raise ValueError(f"channel_id '{channel_id}' 已注册；replace=True 可覆盖")
        self._channels[channel_id] = adapter
        return adapter

    def unregister(self, channel_id: str) -> bool:
        return self._channels.pop(channel_id, None) is not None

    def get(self, channel_id: str) -> AccessChannel:
        adapter = self._channels.get(channel_id)
        if adapter is None:
            from ..errors import ValidationFailed

            raise ValidationFailed(
                "hub_access_unknown_channel",
                f"接入通道 '{channel_id}' 未注册；已注册：{', '.join(sorted(self._channels)) or '（无）'}",
            )
        return adapter

    def list_channels(self) -> list[dict[str, Any]]:
        out = []
        for key, adapter in sorted(self._channels.items()):
            d = adapter.describe() if hasattr(adapter, "describe") else {}
            d["channel_id"] = key
            out.append(d)
        return out

    def health_all(self, *, timeout_seconds: float = 3.0) -> dict[str, HealthReport]:
        return {
            key: adapter.health(timeout_seconds=timeout_seconds)
            for key, adapter in sorted(self._channels.items())
        }

    def capabilities(self) -> list[Capability]:
        caps: list[Capability] = []
        for adapter in self._channels.values():
            try:
                caps.extend(adapter.capabilities())
            except Exception:  # noqa: BLE001 — 单个通道发现失败不拖垮汇总
                continue
        return caps

    def invoke(self, channel_id: str, call: InvokeCall | None = None, *,
               action: str = "run", params: dict[str, Any] | None = None,
               timeout_seconds: float = 15.0) -> InvokeResult:
        """统一调用入口：不存在的通道/不存在的 action 都如实失败。"""
        adapter = self.get(channel_id)
        call = call or InvokeCall(action=action, params=dict(params or {}),
                                  timeout_seconds=timeout_seconds)
        return adapter.invoke(call)


#: 进程级单例：全应用共享的接入源注册表。
access_registry = AccessRegistry()
