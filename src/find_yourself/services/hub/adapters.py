"""统一万能适配层：一个协议，四类对象全归一（W6 增补 A）。

:class:`HubAdapter` 是**唯一**的接入契约：:

    kind -> 这类连接是什么
    health(timeout)      -> HealthReport    真实探测，绝不假设可用
    capabilities()       -> [Capability]    真实发现（MCP 列工具 / 模型列 /models）
    invoke(call)         -> InvokeResult    真实调用，失败即失败

六个 kind 的落地方式：

* ``openai_chat`` / ``anthropic`` —— **只 import 不改动** ``runtime.providers``
  （W4 产物），经 ``build_provider`` / ``probe_provider`` 复用其重试与计费安全边界。
* ``mcp_server`` —— 经 ``adapters.mcp.McpClient`` 列工具并真实调用；工具以
  ``hub.<server>.<tool>`` 注册进 ``services.tool_registry``（**只走其公开 API**）。
* ``http_webhook`` —— 万能 HTTP（方法/头/体模板/超时/重试/响应路径提取），
  默认禁内网地址（SSRF，见 :func:`guard_endpoint`）。
* ``knowledge_source`` —— 桥接 W3 的 ``KnowledgeSource`` 协议（ima / 百度网盘）。
* ``tool_plugin`` —— 用户自定义工具的 manifest 声明式形态，本质是 webhook +
  JSON Schema 参数，注册进 tool_registry 的 ``http`` entry。

诚实原则（总纲铁律 3）：探测不到就 ``ok=False``；能力拿不到就抛
:class:`~find_yourself.services.errors.DomainError` 或返回 ``ok=False`` 的
InvokeResult；**绝不返回构造出来的假条目**。

命名空间的边界说明：hub 的 Capability.name（如 ``tool:ping``）是 **hub 局部**
命名，只落在 ``hub_connections.capabilities`` 这一列里，**从不**喂给
``skills/harness.py`` 的 ``tool:<name>`` 校验器（后者校验的是技能声明），
因此不会破坏启动闸门。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from ..errors import ValidationFailed
from . import (
    DEFAULT_MARKET_SOURCE_TYPE,
    KIND_ANTHROPIC,
    KIND_HTTP_WEBHOOK,
    KIND_KNOWLEDGE_SOURCE,
    KIND_MARKET_SOURCE,
    KIND_MCP_SERVER,
    KIND_OPENAI_CHAT,
    KIND_TOOL_PLUGIN,
    MARKET_SOURCE_TYPES,
)

# --------------------------------------------------------------------------- #
# Value types
# --------------------------------------------------------------------------- #


#: ``chat`` 能力的默认中文别名。分三组：
#: - 通用动作：写、算、查、改、译、总结…
#: - 常见领域：技术、成本、风险、评估、方案、设计…
#: - 产出形态：报告、方案、草稿、清单、总结…
#: 单字别名是刻意的——「查」「写」「算」在中文里单字就能表达意图，
#: 而 ``_matches`` 对别名走子串匹配，允许长度 1。
_CHAT_ALIASES: tuple[str, ...] = (
    # 通用动作
    "对话", "聊天", "问答", "答疑", "写", "算", "查", "改", "译", "翻译",
    "润色", "改写", "总结", "分析", "评估", "评价", "比较", "对比",
    "解释", "说明", "描述", "设计", "规划", "计划", "生成", "起草",
    # 领域名词
    "技术", "工程", "架构", "代码", "实现", "方案", "成本", "价格", "预算",
    "风险", "隐患", "合规", "法律", "市场", "需求", "功能", "性能", "优化",
    "文档", "资料", "报告", "清单", "步骤", "教程", "建议", "决策",
    # 常见提问式
    "什么是", "为什么", "怎么", "如何", "哪些", "区别",
)


@dataclass(frozen=True)
class Capability:
    """One thing a connection can do.

    ``name`` uses a ``<domain>.<action>`` shape inside the hub namespace
    (``chat`` / ``tool:<tool>`` / ``knowledge.search`` ...).

    ``tags`` drive v1 routing (router.py) — they are matched against the task hint.
    ``tags`` 用英文品类词，因为英文标签不会作为子串出现在中文句子里。

    ``aliases`` 是**显式声明的中文别名/同义词**，专治中文路由：``tags=["math"]`` 的
    能力匹配不到「帮我算一下数学加法」，但 ``aliases=("加法", "求和")`` 能。
    别名走子串匹配（中文「数学」确实出现在句中），因此允许单字；``tags`` 仍要求
    长度 >= 2，避免英文短词（"a"、"go"）在长句里子串误命中。
    """

    name: str
    tags: tuple[str, ...] = ()
    description: str = ""
    aliases: tuple[str, ...] = ()

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tags": list(self.tags),
            "description": self.description,
            "aliases": list(self.aliases),
        }

    @classmethod
    def from_public(cls, raw: Any) -> "Capability":
        if isinstance(raw, Capability):
            return raw
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ValidationFailed("hub_invalid_capability", "能力条目缺少 name")
        tags = raw.get("tags") or []
        aliases = raw.get("aliases") or []
        return cls(
            name=str(raw["name"]),
            tags=tuple(str(t) for t in tags if str(t).strip()),
            description=str(raw.get("description") or ""),
            aliases=tuple(str(a) for a in aliases if str(a).strip()),
        )


@dataclass
class HealthReport:
    ok: bool
    latency_ms: int | None = None
    detail: str = ""
    checked_at: str = ""
    endpoint_ref: str = ""
    capabilities: list[Capability] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "endpoint_ref": self.endpoint_ref,
            "capabilities": [c.to_public() for c in self.capabilities],
        }


@dataclass
class InvokeCall:
    action: str = "invoke"
    params: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 15.0


@dataclass
class InvokeResult:
    ok: bool
    output: Any = None
    error: str = ""
    latency_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "meta": self.meta,
        }


class HubAdapter(Protocol):
    """The one protocol every pluggable object satisfies."""

    kind: str

    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport: ...

    def capabilities(self) -> list[Capability]: ...

    def invoke(self, call: InvokeCall) -> InvokeResult: ...


# --------------------------------------------------------------------------- #
# SSRF guard (任务书 §3)
# --------------------------------------------------------------------------- #

_ENV_ALLOW_PRIVATE = "FY_HUB_ALLOW_PRIVATE"

_PRIVATE_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".localdomain")
_PRIVATE_HOST_EXACT = {"localhost", "host.docker.internal", "metadata.google.internal"}


def allow_private_endpoints() -> bool:
    """Opt-out switch for the SSRF guard (tests / deliberately local targets)."""
    return os.environ.get(_ENV_ALLOW_PRIVATE, "").strip().lower() in {"1", "true", "yes", "on"}


def is_private_host(host: str) -> bool:
    """True for loopback / private / link-local / reserved addresses.

    DNS rebinding is **not** defended here: we do not resolve names, so a name
    that points at 127.0.0.1 only at request time is out of scope for v1
    (documented honestly instead of pretending otherwise).
    """
    raw = (host or "").strip().lower().strip("[]")
    if not raw:
        return True
    if raw in _PRIVATE_HOST_EXACT:
        return True
    if raw.endswith(_PRIVATE_HOST_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def guard_endpoint(url: str, *, allow_private: bool | None = None) -> str:
    """Validate an outbound HTTP endpoint; raise on private/loopback targets.

    Returns the url unchanged. ``allow_private`` overrides the environment for
    the call (used by tests); when omitted the ``FY_HUB_ALLOW_PRIVATE`` env wins.
    The guard applies to **generic outbound HTTP** (webhook / tool_plugin).
    Model-provider endpoints (Ollama on localhost, for instance) are a different
    trust shape — the user names them explicitly — so they are not guarded here.
    """
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationFailed(
            "hub_invalid_endpoint", "端点必须是完整的 http(s) URL（含主机名）"
        )
    permitted = allow_private_endpoints() if allow_private is None else allow_private
    if not permitted and is_private_host(parsed.hostname):
        raise ValidationFailed(
            "hub_ssrf_blocked",
            "出于 SSRF 防护，默认禁止访问内网/本机地址；"
            "确为本机服务时设置 FY_HUB_ALLOW_PRIVATE=1 后重试",
        )
    return text


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

_TEMPLATE_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_.]*)\}")


def _lookup(ctx: dict[str, Any], dotted: str) -> Any:
    cur: Any = ctx
    for part in dotted.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise KeyError(dotted)
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.isdigit():
            cur = cur[int(part)]
        else:
            raise KeyError(dotted)
    return cur


def render_template(value: Any, ctx: dict[str, Any]) -> Any:
    """Substitute ``{param.x}`` / ``{credential.y}`` placeholders.

    A missing key raises :class:`ValidationFailed` instead of silently producing
    an empty string — an unsubstituted credential would fail far away from its
    cause and is indistinguishable from "the endpoint accepted a blank key".
    """
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            try:
                found = _lookup(ctx, match.group(1))
            except (KeyError, IndexError, TypeError):
                raise ValidationFailed(
                    "hub_template_missing",
                    f"模板占位符 {{{match.group(1)}}} 没有对应的值（参数或凭证缺失）",
                ) from None
            return "" if found is None else str(found)

        return _TEMPLATE_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: render_template(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render_template(v, ctx) for v in value]
    return value


def extract_path(payload: Any, path: str) -> Any:
    """Dotted-path extraction with numeric list indices (``data.items[0].text``)."""
    dotted = (path or "").strip()
    if not dotted:
        return payload
    cur = payload
    for part in dotted.replace("[", ".").replace("]", "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.isdigit():
            idx = int(part)
            if idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            return None
    return cur


def _stamp() -> str:
    from ...db.types import utcnow

    return utcnow().isoformat()


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _endpoint_ref(url: str) -> str:
    """Host + path only: query strings and userinfo never reach the UI."""
    if not url:
        return ""
    return url.split("?", 1)[0].rstrip("/")


# --------------------------------------------------------------------------- #
# 1) openai_chat / anthropic — reuse the W4 provider factory (import only)
# --------------------------------------------------------------------------- #

_PROVIDER_FOR_KIND = {KIND_OPENAI_CHAT: "openai_compat", KIND_ANTHROPIC: "anthropic"}


class ChatModelAdapter:
    """OpenAI-compatible / Anthropic chat endpoint (W4 provider, reused)."""

    def __init__(self, config: dict[str, Any], *, transport: Any | None = None):
        self.kind = str(config.get("kind") or KIND_OPENAI_CHAT)
        self.config = dict(config)
        self.transport = transport

    # -- internals ---------------------------------------------------------- #
    def _spec(self):
        from ...runtime.providers import ProviderEndpoint

        provider_id = str(self.config.get("provider_id") or _PROVIDER_FOR_KIND.get(self.kind, "openai_compat"))
        return ProviderEndpoint(
            provider_id=provider_id,
            base_url=str(self.config.get("base_url") or ""),
            api_key=str(self.config.get("api_key") or ""),
            model=str(self.config.get("model") or ""),
            transport=self.transport,
        )

    def model(self) -> str:
        return str(self.config.get("model") or "")

    def is_configured(self) -> bool:
        from ...runtime.providers import LOCAL_INFERENCE_PROVIDERS

        provider_id = str(self.config.get("provider_id") or _PROVIDER_FOR_KIND.get(self.kind, "openai_compat"))
        if provider_id in LOCAL_INFERENCE_PROVIDERS:
            return bool(self.config.get("base_url"))
        return bool(self.config.get("api_key"))

    # -- HubAdapter ---------------------------------------------------------- #
    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
        from ...runtime.providers import probe_provider

        if not self.is_configured():
            return HealthReport(
                ok=False,
                detail="未接入：缺少 API Key（本地推理至少要有 Base URL）",
                checked_at=_stamp(),
                endpoint_ref=_endpoint_ref(str(self.config.get("base_url") or "")),
            )
        _ = time.perf_counter()
        report = probe_provider(self._spec(), timeout_seconds=timeout_seconds)
        return HealthReport(
            ok=bool(report.ok),
            latency_ms=report.latency_ms,
            detail=report.error or (f"模型可用：{len(report.models)} 个" if report.ok else "探测失败"),
            checked_at=_stamp(),
            endpoint_ref=report.endpoint_ref,
            capabilities=self.capabilities() if report.ok else [],
        )

    def capabilities(self) -> list[Capability]:
        if not self.is_configured():
            return []
        provider_id = str(self.config.get("provider_id") or _PROVIDER_FOR_KIND.get(self.kind, "openai_compat"))
        local = provider_id == "ollama"
        tags = ("chat", "llm", "text")
        tags = tags + ("local", "free") if local else tags + ("cloud",)
        declared = [Capability.from_public(c) for c in (self.config.get("capabilities") or [])]
        # 别名不是随手写的：实测中文路由漏匹配的主因就是这张表太窄
        # （'技术可行性'、'成本'、'风险'、'评估' 一个都没命中，hint 越长候选越少）。
        # 所以按「任务动词 + 常见领域名词」两类铺开，而不是只收泛化词。
        base = [Capability(name="chat", tags=tags, description=f"对话补全（{provider_id}）",
                            aliases=_CHAT_ALIASES)]
        return _merge_capabilities(base, declared)

    def invoke(self, call: InvokeCall) -> InvokeResult:
        from ...runtime.providers import ProviderError, build_provider

        # 延迟导入同本文件其它 runtime.providers 引用：services.hub <-> runtime.providers
        # 存在循环依赖，模块顶部导入会炸。名字在调用时才解析，所以漏导入不会在
        # 导入期暴露 —— 只有真正调用时才发现（这正是它一度藏住的原因）。
        from ...runtime.providers.base import normalize_messages

        started = time.perf_counter()
        # action 别名：调用方必须先知道 kind 才能猜对action 名（openai_chat 是
        # complete、mcp_server 是 call_tool），这让「统一接入层」名不副实。
        # 这里只加别名不改名，既有调用继续工作。
        action = (call.action or "complete").strip().lower()
        action = {"invoke": "complete", "chat": "complete"}.get(action, action)
        try:
            if not self.is_configured():
                raise ValidationFailed("hub_not_configured", "连接未配置凭证，拒绝调用")
            provider = build_provider(self._spec())
            if action == "models":
                models = provider.probe_models(timeout_seconds=call.timeout_seconds)
                return InvokeResult(ok=True, output={"models": models}, latency_ms=_elapsed_ms(started))
            if action != "complete":
                raise ValidationFailed(
                    "hub_unsupported_action",
                    f"chat 适配器不支持 action='{action}'（支持 complete / models）",
                )
            # 多轮优先：协同编排需要把上文传给 Agent，而单轮 prompt 只能表达
            # 一句话。两者都空才报错 —— normalize_messages 负责校验并给出清晰错误。
            raw_messages = call.params.get("messages")
            prompt = str(call.params.get("prompt") or "")
            try:
                conversation = normalize_messages(raw_messages, prompt)
            except ValueError as exc:
                raise ValidationFailed("hub_missing_prompt", str(exc)) from exc
            result = provider.complete(
                model=str(call.params.get("model") or self.model()),
                prompt=prompt,
                max_tokens=int(call.params.get("max_tokens") or 1024),
                timeout_seconds=call.timeout_seconds,
                messages=conversation,
            )
        except ValidationFailed as exc:
            return InvokeResult(ok=False, error=f"{exc.code}: {exc.message}", latency_ms=_elapsed_ms(started))
        except ProviderError as exc:
            return InvokeResult(ok=False, error=exc.describe(), latency_ms=_elapsed_ms(started),
                                meta={"kind": exc.kind, "retryable": exc.retryable})
        except Exception as exc:  # noqa: BLE001 — never leak a stack to the client
            return InvokeResult(ok=False, error=f"{type(exc).__name__}: {exc}",
                                latency_ms=_elapsed_ms(started))
        return InvokeResult(
            ok=True,
            output={"text": result.text, "model": result.model or self.model()},
            latency_ms=_elapsed_ms(started),
            meta={"provider_id": result.provider_id, "usage": result.usage,
                  "degraded_from": result.degraded_from},
        )


# --------------------------------------------------------------------------- #
# 2) mcp_server
# --------------------------------------------------------------------------- #


class McpServerAdapter:
    """MCP server: real ``tools/list`` discovery and ``tools/call`` execution.

    传输（A-统一接入-02）：``config.command``（stdio 子进程，原有语义）或
    ``config.url`` + ``config.transport``（``http`` / ``sse`` / ``ws``）。
    信任分级（A-统一接入-09）：``config.trust`` ∈ trusted / remote / untrusted，
    或构造时直接传 ``trust_policy``（携带 confirm 回调）。
    """

    def __init__(self, config: dict[str, Any], *, client: Any | None = None,
                 trust_policy: Any | None = None):
        self.kind = KIND_MCP_SERVER
        self.config = dict(config)
        self._client = client
        self._trust_policy = trust_policy
        self._tools: list[dict[str, Any]] | None = None
        #: 上一次 register_tools 被跳过的名字（命名不合规 / 与他人注册冲突）。
        self.last_skipped: list[str] = []

    def server_key(self) -> str:
        return str(self.config.get("server") or "mcp").strip()

    # -- connection ---------------------------------------------------------- #
    def _trust(self) -> Any:
        if self._trust_policy is not None:
            return self._trust_policy
        from ...adapters.mcp import TRUST_LEVELS, TRUST_TRUSTED, McpTrustPolicy

        level = str(self.config.get("trust") or TRUST_TRUSTED).strip().lower()
        if level not in TRUST_LEVELS:
            raise ValidationFailed(
                "hub_mcp_invalid_trust",
                f"未知 MCP 信任级 '{level}'；可用：{', '.join(TRUST_LEVELS)}",
            )
        return McpTrustPolicy(level=level)

    #: 不能当作「凭证值」的平铺键 —— 它们是连接的结构字段或模板本身。
    _CONFIG_RESERVED_KEYS = frozenset({
        "kind", "server", "url", "transport", "trust", "headers", "command",
        "env", "params", "credentials", "capabilities", "method", "timeout_seconds",
        "retries", "response_path", "provider_id", "base_url", "model",
        "source_id", "api_key_", "prompt", "system",
    })

    def _credential_ctx(self) -> dict[str, Any]:
        """汇总凭证：``credentials`` 子字典 + 平铺的凭证键（平铺优先）。

        两条注入路径必须都支持，否则用户「填了 token 却报模板缺值」：
        * ``ConnectionService.adapter_config()``（connections.py:170-175）把解密后的
          凭证**平铺**进 config（``cfg["api_token"]``），没有 credentials 子字典
        * ``build_adapter(credentials=...)``（adapters.py:912-913）把凭证并进 cfg，
          manifest 导入路径可能显式带 credentials 子字典

        平铺键优先：它是用户刚填的实时值；子字典作为兜底。
        """
        creds: dict[str, Any] = {}
        nested = self.config.get("credentials")
        if isinstance(nested, dict):
            creds.update({str(k): v for k, v in nested.items() if v not in (None, "")})
        for key, value in self.config.items():
            if key in self._CONFIG_RESERVED_KEYS:
                continue
            if value in (None, "", {}, []):
                continue
            creds[str(key)] = value
        return creds

    def _render_headers(self) -> dict[str, str]:
        """渲染 ``config.headers``，支持 ``{credential.<key>}`` / ``{param.<key>}``。

        与 WebhookAdapter 同一套模板语法，保证用户在两类连接里写凭证占位符
        的方式一致；空 headers 直接返回空 dict，调用方据此走无认证分支。
        """
        raw = self.config.get("headers")
        if not isinstance(raw, dict) or not raw:
            return {}
        ctx = {"param": dict(self.config.get("params") or {}),
               "credential": self._credential_ctx()}
        return {str(k): str(v) for k, v in render_template(dict(raw), ctx).items()}

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        from ...adapters.mcp import McpClient

        cmd = self.config.get("command")
        if isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd):
            env = self.config.get("env") if isinstance(self.config.get("env"), dict) else None
            self._client = McpClient.from_subprocess(cmd, env=env, trust=self._trust())
            return self._client
        url = str(self.config.get("url") or "").strip()
        if url:
            transport = str(self.config.get("transport") or "http").strip().lower()
            if transport not in {"http", "sse", "ws"}:
                raise ValidationFailed(
                    "hub_mcp_invalid_transport",
                    f"未知 MCP 远端传输 '{transport}'；可用：http / sse / ws",
                )
            # 远程 MCP 认证头：此前完全没透传，导致任何需要 Bearer token 的
            # 远程 MCP Server 都固定 401 —— 凭证填了也用不上。
            # headers 支持 {credential.<key>} 模板（与 WebhookAdapter 同一套
            # render_template，同在本模块），空则不传该参数以保持向后兼容。
            headers = self._render_headers()
            if headers:
                self._client = McpClient.from_url(
                    url, transport=transport, trust=self._trust(), headers=headers)
            else:
                self._client = McpClient.from_url(url, transport=transport, trust=self._trust())
            return self._client
        raise ValidationFailed(
            "hub_mcp_invalid_command",
            "MCP 连接需要可执行的 command（字符串数组，stdio）或 url（http/sse/ws）",
        )

    def list_tools(self) -> list[dict[str, Any]]:
        if self._tools is None:
            client = self._connect()
            client.initialize()
            tools = client.list_tools()
            self._tools = tools if isinstance(tools, list) else []
        return self._tools

    def refresh(self) -> list[dict[str, Any]]:
        """动态刷新（A-统一接入-09）：丢弃缓存重新发现工具清单。"""
        self._tools = None
        return self.list_tools()

    def tool_names(self) -> list[str]:
        return [str(t.get("name")) for t in list(self.list_tools()) if isinstance(t, dict) and t.get("name")]

    # -- tool_registry bridge (public API only) ------------------------------ #
    def register_tools(self, registry: Any | None = None) -> list[str]:
        """Register remote tools as ``hub.<server>.<tool>`` in the tool registry.

        §八 B1 收敛：本方法只是 :func:`adapters.mcp.assemble_mcp_tools`（**唯一**
        装配入口）在 ``prefix="hub."`` 命名空间下的委托调用，不再自养一套装配
        逻辑（重复/漂移风险就此消除）。Names that would clash with a *different*
        existing entry are skipped (logged in ``last_skipped``), never overwritten.
        """
        from ...adapters.mcp import assemble_mcp_tools

        if registry is None:
            from ..tool_registry import tool_registry as registry
        server = self.server_key()
        client = self._connect()
        status = assemble_mcp_tools(
            registry=registry, clients={server: client}, prefix="hub.",
        )[0]
        self.last_skipped = [
            str(s.get("tool") or s.get("reason") or "") for s in status.get("skipped", [])
        ]
        # 装配后丢弃本适配器缓存，下次 list_tools 从远端重新发现。
        self._tools = None
        return list(status.get("registered", []))

    # -- HubAdapter ---------------------------------------------------------- #
    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
        started = time.perf_counter()
        try:
            tools = self.list_tools()
        except ValidationFailed as exc:
            return HealthReport(ok=False, latency_ms=_elapsed_ms(started),
                                detail=f"{exc.code}: {exc.message}", checked_at=_stamp())
        except Exception as exc:  # noqa: BLE001
            return HealthReport(ok=False, latency_ms=_elapsed_ms(started),
                                detail=f"{type(exc).__name__}: {exc}", checked_at=_stamp())
        names = [t.get("name") for t in tools if isinstance(t, dict)]
        return HealthReport(
            ok=True,
            latency_ms=_elapsed_ms(started),
            detail=f"MCP 已连接：{len(names)} 个工具",
            checked_at=_stamp(),
            endpoint_ref=f"mcp:{self.server_key()}",
            capabilities=self.capabilities(),
        )

    def capabilities(self) -> list[Capability]:
        server = self.server_key()
        # MCP 工具名五花八门（add/echo/summarize/...），别名无法内置猜测，
        # 所以 discovered 不带 aliases —— 中文别名由 config.capabilities 里
        # 用户显式声明的条目通过 _merge_capabilities 补进来。
        discovered = [
            Capability(name=f"tool:{name}", tags=("tool", "mcp", server),
                       description=f"MCP 工具 {name}（{server}）")
            for name in self.tool_names()
        ]
        declared = [Capability.from_public(c) for c in (self.config.get("capabilities") or [])]
        return _merge_capabilities(discovered, declared)

    def invoke(self, call: InvokeCall) -> InvokeResult:
        started = time.perf_counter()
        # 与 openai_chat 对称：调用方不该需要先知道 kind 才能猜对 action 名。
        # `invoke` 作为通用动作名映射到 call_tool；既有 call_tool/tools 不变。
        action = (call.action or "call_tool").strip().lower()
        action = {"invoke": "call_tool", "call": "call_tool",
                  "tool_call": "call_tool"}.get(action, action)
        if action not in {"call_tool", "tools"}:
            return InvokeResult(
                ok=False, latency_ms=_elapsed_ms(started),
                error=f"hub_unsupported_action: mcp 适配器不支持 action='{action}'"
                      "（支持 call_tool / tools / invoke / call）",
            )
        name = str(call.params.get("name") or "")
        if not name:
            return InvokeResult(ok=False, latency_ms=_elapsed_ms(started),
                                error="hub_missing_tool: 缺少 name 参数")
        try:
            client = self._connect()
            if self._tools is None:
                client.initialize()
            output = client.call_tool(name, dict(call.params.get("arguments") or {}))
        except Exception as exc:  # noqa: BLE001
            return InvokeResult(ok=False, latency_ms=_elapsed_ms(started),
                                error=f"{type(exc).__name__}: {exc}")
        return InvokeResult(ok=True, output=output, latency_ms=_elapsed_ms(started),
                            meta={"server": self.server_key(), "tool": name})

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# 3) http_webhook / tool_plugin
# --------------------------------------------------------------------------- #


class WebhookAdapter:
    """Generic HTTP call: method / headers / body template / retries / extract."""

    def __init__(self, config: dict[str, Any], *, credentials: dict[str, str] | None = None,
                 transport: Any | None = None, sleep: Any | None = None):
        self.kind = str(config.get("kind") or KIND_HTTP_WEBHOOK)
        self.config = dict(config)
        self.credentials = dict(credentials or {})
        self.transport = transport
        self._sleep = sleep or time.sleep

    # -- internals ----------------------------------------------------------- #
    def _ctx(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"param": dict(params or {}), "credential": dict(self.credentials),
                "config": {k: v for k, v in self.config.items() if isinstance(v, (str, int, float))}}

    def _method(self, fallback: str = "POST") -> str:
        return str(self.config.get("method") or fallback).upper()

    def _url(self) -> str:
        return str(self.config.get("url") or "")

    def _request(self, *, method: str, url: str, body: Any, timeout: float,
                 headers: dict[str, str] | None = None):
        import httpx

        kwargs: dict[str, Any] = {"timeout": timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        with httpx.Client(**kwargs) as client:
            return client.request(method, url, headers=headers or {}, json=body)

    def _decoded(self, resp: Any) -> Any:
        if not getattr(resp, "content", None):
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text[:2000]}

    def _call_once(self, ctx: dict[str, Any], *, method: str, url: str, timeout: float) -> Any:
        headers = render_template(dict(self.config.get("headers") or {}), ctx)
        headers = {str(k): str(v) for k, v in headers.items()}
        raw_body = self.config.get("body_template")
        if raw_body is None:
            body: Any = dict(ctx.get("param") or {})
        else:
            body = render_template(raw_body, ctx)
            if isinstance(body, str):
                stripped = body.strip()
                if stripped.startswith(("{", "[")):
                    import json

                    try:
                        body = json.loads(stripped)
                    except ValueError as exc:
                        raise ValidationFailed(
                            "hub_invalid_body_template", f"body_template 不是合法 JSON：{exc}"
                        ) from None
        return self._request(method=method, url=url, body=body, timeout=timeout, headers=headers)

    # -- HubAdapter ---------------------------------------------------------- #
    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
        started = time.perf_counter()
        try:
            url = guard_endpoint(self._url())
        except ValidationFailed as exc:
            return HealthReport(ok=False, latency_ms=_elapsed_ms(started),
                                detail=f"{exc.code}: {exc.message}", checked_at=_stamp())
        health_cfg = self.config.get("health") if isinstance(self.config.get("health"), dict) else {}
        method = str(health_cfg.get("method") or "GET").upper()
        expect = {int(s) for s in (health_cfg.get("expect_status") or [200, 204, 401, 403])}
        try:
            resp = self._request(method=method, url=url, body=None,
                                 timeout=timeout_seconds, headers=None)
        except Exception as exc:  # noqa: BLE001
            return HealthReport(ok=False, latency_ms=_elapsed_ms(started),
                                detail=f"{type(exc).__name__}: {exc}",
                                checked_at=_stamp(), endpoint_ref=_endpoint_ref(url))
        ok = resp.status_code in expect
        return HealthReport(
            ok=ok,
            latency_ms=_elapsed_ms(started),
            detail=("可达" if ok else f"响应 HTTP {resp.status_code}") + f"（{method} 探测）",
            checked_at=_stamp(),
            endpoint_ref=_endpoint_ref(url),
            capabilities=self.capabilities() if ok else [],
        )

    def capabilities(self) -> list[Capability]:
        declared = [Capability.from_public(c) for c in (self.config.get("capabilities") or [])]
        if declared:
            return _merge_capabilities([], declared)
        return [Capability(name="http.call", tags=("http", "webhook"),
                           description="通用 HTTP 调用",
                           aliases=("调用接口", "请求接口", "发请求"))]

    def invoke(self, call: InvokeCall) -> InvokeResult:
        started = time.perf_counter()
        try:
            url = guard_endpoint(self._url())
        except ValidationFailed as exc:
            return InvokeResult(ok=False, latency_ms=_elapsed_ms(started),
                                error=f"{exc.code}: {exc.message}")
        ctx = self._ctx(call.params)
        method = self._method()
        retries = max(0, int(self.config.get("retries") or 0))
        timeout = float(call.timeout_seconds or self.config.get("timeout_seconds") or 10.0)
        last = ""
        for attempt in range(retries + 1):
            try:
                resp = self._call_once(ctx, method=method, url=url, timeout=timeout)
            except ValidationFailed as exc:
                return InvokeResult(ok=False, latency_ms=_elapsed_ms(started),
                                    error=f"{exc.code}: {exc.message}")
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    self._sleep(0.2 * (2 ** attempt))
                    continue
                return InvokeResult(ok=False, latency_ms=_elapsed_ms(started), error=last,
                                    meta={"attempts": attempt + 1})
            status = int(resp.status_code)
            if status in {408, 409, 429} or 500 <= status <= 599:
                last = f"HTTP {status}"
                if attempt < retries:
                    self._sleep(0.2 * (2 ** attempt))
                    continue
                return InvokeResult(ok=False, latency_ms=_elapsed_ms(started),
                                    error=f"上游返回 {last}", meta={"attempts": attempt + 1})
            payload = self._decoded(resp)
            if status >= 400:
                return InvokeResult(ok=False, latency_ms=_elapsed_ms(started),
                                    error=f"HTTP {status}",
                                    meta={"attempts": attempt + 1, "body": payload})
            output = extract_path(payload, str(self.config.get("response_path") or ""))
            return InvokeResult(ok=True, output=output, latency_ms=_elapsed_ms(started),
                                meta={"status": status, "attempts": attempt + 1})
        return InvokeResult(ok=False, latency_ms=_elapsed_ms(started), error=last or "调用失败")


# --------------------------------------------------------------------------- #
# 4) knowledge_source (W3 bridge)
# --------------------------------------------------------------------------- #

_KNOWLEDGE_SOURCE_IDS = ("ima", "baidu_pan")


class KnowledgeSourceAdapter:
    """Wrap a W3 ``KnowledgeSource`` so a knowledge source is a hub connection."""

    def __init__(self, config: dict[str, Any]):
        self.kind = KIND_KNOWLEDGE_SOURCE
        self.config = dict(config)

    def _source(self):
        from ...services.knowledge.sources import BaiduPanSource, ImaSource

        source_id = str(self.config.get("source_id") or "").strip()
        if source_id == "ima":
            return ImaSource(
                api_key=str(self.config.get("api_key") or ""),
                base_url=str(self.config.get("base_url") or ""),
            )
        if source_id == "baidu_pan":
            return BaiduPanSource(
                app_key=str(self.config.get("app_key") or ""),
                app_secret=str(self.config.get("app_secret") or ""),
                redirect_uri=str(self.config.get("redirect_uri") or ""),
            )
        raise ValidationFailed(
            "hub_unknown_knowledge_source",
            f"未知知识源 '{source_id}'；内置可用：{', '.join(_KNOWLEDGE_SOURCE_IDS)}",
        )

    # -- HubAdapter ---------------------------------------------------------- #
    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
        started = time.perf_counter()
        try:
            source = self._source()
            info = source.health_check(probe=True)
        except ValidationFailed as exc:
            return HealthReport(ok=False, latency_ms=_elapsed_ms(started),
                                detail=f"{exc.code}: {exc.message}", checked_at=_stamp())
        except Exception as exc:  # noqa: BLE001
            return HealthReport(ok=False, latency_ms=_elapsed_ms(started),
                                detail=f"{type(exc).__name__}: {exc}", checked_at=_stamp())
        ok = bool(info.get("available"))
        return HealthReport(
            ok=ok,
            latency_ms=info.get("latency_ms"),
            detail=str(info.get("detail") or ""),
            checked_at=_stamp(),
            endpoint_ref=str(self.config.get("base_url") or self.config.get("source_id") or ""),
            capabilities=self.capabilities() if ok else [],
        )

    def capabilities(self) -> list[Capability]:
        try:
            source = self._source()
        except ValidationFailed:
            return []
        caps = getattr(source, "capabilities", None)
        out: list[Capability] = [
            Capability(name="knowledge.list", tags=("knowledge", "list"),
                       description=f"列出 {source.source_id} 的知识库/目录",
                       aliases=("知识库", "目录", "清单", "有哪些文档"))
        ]
        if getattr(caps, "searchable", False):
            out.append(Capability(name="knowledge.search", tags=("knowledge", "search"),
                                  description=f"服务端检索 {source.source_id}",
                                  aliases=("搜索", "检索", "查找", "查一下", "找资料")))
        if getattr(caps, "full_text", False):
            out.append(Capability(name="knowledge.fetch", tags=("knowledge", "document"),
                                  description=f"抓取 {source.source_id} 文档正文",
                                  aliases=("读文档", "正文", "原文", "打开文件")))
        declared = [Capability.from_public(c) for c in (self.config.get("capabilities") or [])]
        return _merge_capabilities(out, declared)

    def invoke(self, call: InvokeCall) -> InvokeResult:
        from ...services.knowledge.sources import SourceRef

        started = time.perf_counter()
        action = (call.action or "list").strip().lower()
        try:
            source = self._source()
            if action in {"list", "list_sources"}:
                refs = source.list_sources()
                return InvokeResult(
                    ok=True,
                    output={"sources": [{"source_id": r.source_id, "external_id": r.external_id,
                                         "name": r.name, "kind": r.kind} for r in refs]},
                    latency_ms=_elapsed_ms(started),
                )
            if action in {"search", "search_metadata"}:
                ref = SourceRef(
                    source_id=str(call.params.get("source_id") or source.source_id),
                    external_id=str(call.params.get("external_id") or ""),
                    name=str(call.params.get("name") or ""),
                )
                hits = source.search_metadata(
                    ref, str(call.params.get("query") or ""), int(call.params.get("limit") or 10)
                )
                return InvokeResult(ok=True, output={"hits": hits}, latency_ms=_elapsed_ms(started))
            if action in {"fetch", "fetch_document"}:
                ref = SourceRef(
                    source_id=str(call.params.get("source_id") or source.source_id),
                    external_id=str(call.params.get("external_id") or ""),
                    name=str(call.params.get("name") or ""),
                )
                docs = list(source.fetch_document(ref))
                return InvokeResult(
                    ok=True,
                    output={"documents": [{"external_id": d.external_id, "name": d.name,
                                           "text": d.text} for d in docs]},
                    latency_ms=_elapsed_ms(started),
                )
            raise ValidationFailed(
                "hub_unsupported_action",
                f"knowledge 适配器不支持 action='{action}'（支持 list / search / fetch）",
            )
        except ValidationFailed as exc:
            return InvokeResult(ok=False, latency_ms=_elapsed_ms(started),
                                error=f"{exc.code}: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            return InvokeResult(ok=False, latency_ms=_elapsed_ms(started),
                                error=f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #


def _merge_capabilities(discovered: list[Capability], declared: list[Capability]) -> list[Capability]:
    """Discovered capabilities first; declared ones fill gaps / add tags & aliases."""
    out: list[Capability] = list(discovered)
    by_name = {c.name: i for i, c in enumerate(out)}
    for cap in declared:
        if cap.name in by_name:
            idx = by_name[cap.name]
            merged_tags = tuple(dict.fromkeys(tuple(out[idx].tags) + tuple(cap.tags)))
            # 别名同样并集合并：用户声明的中文别名不能被自动发现的空别名覆盖掉。
            merged_aliases = tuple(dict.fromkeys(tuple(out[idx].aliases) + tuple(cap.aliases)))
            out[idx] = Capability(name=cap.name, tags=merged_tags, aliases=merged_aliases,
                                  description=cap.description or out[idx].description)
        else:
            out.append(cap)
    return out


# --------------------------------------------------------------------------- #
# 5) market_source · Git 形态（source_type="git"）
# --------------------------------------------------------------------------- #
#
# 手册承诺远程来源支持 URL / Git / 本地目录，此前只有 http(s) 与 file://，
# Git 仓库 URL 会走进 ``_get_json`` 拿 JSON 而必然失败。这里补的是**运输**，
# 信任判定仍在 PROMOTION_GATE_POLICY（见 MarketSourceAdapter 文档串）。

#: git 执行器签名：``(argv, timeout) -> (returncode, stdout, stderr)``。
#: 可注入是为了测试不真调 git（单测用假件铺文件），生产走 :func:`_run_git`。
GitRunner = Callable[[list[str], float], tuple[int, str, str]]

#: 浅克隆深度：市场源只要清单，不要历史。
GIT_DEFAULT_DEPTH = 1
#: 单次 clone 超时（秒）。
GIT_DEFAULT_TIMEOUT = 60.0
#: 探活（health_check）下 Git 源的专用预算：clone 真跑一遍，3s 的 HTTP 预算没有意义。
GIT_HEALTH_TIMEOUT = 15.0
#: clone 落地后允许的工作区体积上限（不含 .git）与文件数上限。
#: 浅克隆挡不住「一个巨大 blob / 几十万小文件」，那会把本机磁盘打满。
GIT_DEFAULT_MAX_BYTES = 32 * 1024 * 1024
GIT_DEFAULT_MAX_FILES = 2000
#: 仓库内清单文件候选名（按序探测；``config.git_manifest`` 可显式指定）。
GIT_MANIFEST_CANDIDATES = ("fy-market.json", "market.json", "index.json")
#: 分支/tag 白名单：``git check-ref-format`` 的保守子集（拒绝 ``..``、空格、``-`` 开头）。
_GIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
#: scp 式地址（``git@host:org/repo.git``）的主机名提取。
_GIT_SCP_RE = re.compile(r"^[^@/\s]+@([^:/\s]+):")
#: Windows 盘符路径（``C:\repos\src``）——本机路径，与 file:// 同一信任面。
_GIT_WIN_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _run_git(argv: list[str], timeout: float) -> tuple[int, str, str]:
    """默认 git 执行器：``subprocess.run(list)``，**永不使用 shell**。

    与 :mod:`find_yourself.services.git_repo_service` 同一条边界。额外关闭一切
    交互式提示，否则导入会卡在凭据输入上（服务进程里没人能敲键盘）。
    超时与「本机没装 git」在这里就转成带码错误，不裸抛 subprocess 异常。
    """
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_LFS_SKIP_SMUDGE": "1"})
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False, env=env,
        )
    except FileNotFoundError:
        raise ValidationFailed(
            "hub_market_source_git_unavailable",
            "本机没有可用的 git 命令，无法导入 Git 来源（请安装 Git 或改用 HTTP/file 源）",
        ) from None
    except subprocess.TimeoutExpired:
        raise ValidationFailed(
            "hub_market_source_git_timeout",
            f"git 命令超时（>{int(timeout)}s）",
        ) from None
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _git_host(url: str) -> str:
    """取 git 地址的主机名：标准 URL 走 urlparse，scp 式地址走正则。"""
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname
    matched = _GIT_SCP_RE.match(url)
    return matched.group(1) if matched else ""


def _normalize_git_url(url: str) -> str:
    """scp 式地址（``git@host:org/repo.git``）归一成 ``ssh://host/org/repo.git``。

    归一后过同一套协议白名单即可——无需在本模块另写一份 scp 解析规则。
    """
    matched = _GIT_SCP_RE.match(url)
    if not matched:
        return url
    return f"ssh://{matched.group(1)}/{url.split(':', 1)[1].lstrip('/')}"


def guard_git_url(url: str) -> str:
    """校验 Git 仓库地址并做 SSRF 守卫，返回原串。

    抽成模块函数是因为有两个调用方：登记连接时（``connections._validate_endpoint``，
    让用户在表单阶段就看到原因）与真正 clone 前（适配器）——同一套规则只写一次。

    SSRF 这里不复用 :func:`guard_endpoint`：那个只认 http(s)，而 ``ssh://`` /
    ``git://`` / ``git@host:path`` 是 git 的正常写法。因此改为「先按 scheme 白名单
    收窄协议，再按主机名判内网」，判定口径与 ``is_private_host`` 保持一致。
    """
    text = str(url or "").strip()
    if not text:
        raise ValidationFailed("hub_market_source_url_required", "Git 来源未配置仓库地址")
    # 复用 GitRepoService 的地址白名单（协议 + 拒绝 `-` 开头的参数注入形态），
    # 不在本模块另立一套 scheme 规则——两处漂移就会变成两种信任面。
    from ..git_repo_service import GitRepoService

    try:
        GitRepoService._validate_url(_normalize_git_url(text))
    except ValidationFailed as exc:
        raise ValidationFailed(
            "hub_market_source_git_bad_url", f"Git 地址不合法：{exc.message}"
        ) from None
    # file:// 与本机路径读的是本机磁盘，没有 SSRF 面（同 _validate_endpoint 的既有判断）。
    if text.startswith("file://") or _GIT_WIN_PATH_RE.match(text):
        return text
    host = _git_host(text)
    if not host:
        raise ValidationFailed("hub_market_source_git_bad_url", "Git 地址缺少主机名")
    if not allow_private_endpoints() and is_private_host(host):
        raise ValidationFailed(
            "hub_ssrf_blocked",
            "出于 SSRF 防护，默认禁止访问内网/本机地址；"
            "确为本机仓库时设置 FY_HUB_ALLOW_PRIVATE=1 后重试",
        )
    return text


def _resolve_in_repo(root: Path, raw: str) -> Path:
    """把清单声明的相对路径解析到仓库内，并**逐层校验不越界**。

    仓库内容是远程不可信输入：``..``、绝对路径、Windows 盘符都要在读文件**之前**
    拒绝；解析（``resolve()``）之后再判一次归属，顺带把「符号链接指向仓库外」也挡住
    ——规范化只防字符串里的 ``../``，防不住 ``packages/x.json -> C:/secrets``。
    """
    rel = str(raw or "").strip().replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or ":" in rel or rel.startswith("/"):
        raise ValidationFailed(
            "hub_market_source_git_path_escape",
            f"清单声明的路径越界或非法：{raw!r}（只允许仓库内相对路径）",
        )
    base = root.resolve()
    target = (root / Path(*parts)).resolve()
    if target != base and base not in target.parents:
        raise ValidationFailed(
            "hub_market_source_git_path_escape",
            f"清单声明的路径越界或非法：{raw!r}（只允许仓库内相对路径）",
        )
    return target


def _enforce_repo_limits(root: Path, config: dict[str, Any]) -> None:
    """clone 之后按体积/文件数兜一道闸，超限即拒绝导入（不留半截仓库）。"""
    max_bytes = _positive_int(config.get("git_max_bytes"), GIT_DEFAULT_MAX_BYTES)
    max_files = _positive_int(config.get("git_max_files"), GIT_DEFAULT_MAX_FILES)
    total = 0
    files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # .git 是 clone 的开销不是包内容，且要避免 walk 进去（浅克隆也是几 MB）。
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            files += 1
            try:
                total += path.stat().st_size
            except OSError:
                continue
            if total > max_bytes or files > max_files:
                raise ValidationFailed(
                    "hub_market_source_git_repo_too_large",
                    f"仓库体积或文件数超限（>{max_bytes} 字节 / >{max_files} 文件）"
                    "：市场源只应是清单仓库，请缩小仓库或调大 git_max_bytes",
                )


def _positive_int(raw: Any, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def resolve_source_type(config: dict[str, Any]) -> str:
    """读出 market_source 的来源形态；未知值当场报错，不静默落回 http。"""
    raw = str((config or {}).get("source_type") or "").strip().lower()
    if not raw:
        return DEFAULT_MARKET_SOURCE_TYPE
    if raw not in MARKET_SOURCE_TYPES:
        raise ValidationFailed(
            "hub_market_source_bad_type",
            f"未知来源形态 '{raw}'；可用：{' / '.join(MARKET_SOURCE_TYPES)}"
            "（或留空 = HTTP 索引源）",
        )
    return raw


class MarketSourceAdapter:
    """市场远程源适配器（I-12）：只做“运输通道”，不做信任判定。

    源索引格式（JSON）：``{"packages": [{"id", "name", "version",
    "description", "download_url", ...}]}``。URL 可以是 http(s)（走 SSRF
    守卫 + 可选 Authorization 头）或 ``file://`` 本地目录（读 index.json，
    供自建源与测试）。包的签名/扫描走既有 ``PROMOTION_GATE_POLICY``，
    本适配器**永不**自行判定可装。

    ``source_type="git"``（见 :data:`MARKET_SOURCE_TYPES`）：地址是 Git 仓库，
    改走 ``git clone`` 到临时目录后读同一份清单格式（每条目的
    ``manifest_path`` 是仓库内相对路径）。clone 出的临时目录由 :meth:`close`
    清理，调用方必须在用完后调用。Git 与 HTTP/file 对上层完全同构——导入、
    陈列、安装门禁都复用 ``MarketplaceService`` 的既有逻辑。
    """

    def __init__(self, config: dict[str, Any], *,
                 credentials: dict[str, str] | None = None,
                 transport: Any | None = None,
                 runner: GitRunner | None = None,
                 workdir: str | None = None):
        self.kind = str(config.get("kind") or KIND_MARKET_SOURCE)
        self.config = dict(config)
        self.credentials = dict(credentials or {})
        self.transport = transport
        self._git_runner: GitRunner | None = runner
        self._workdir = workdir
        self._repo_root: Path | None = None

    def _url(self) -> str:
        return str(self.config.get("url") or "").strip()

    def source_type(self) -> str:
        """来源形态：``http``（默认，含 file://）或 ``git``。留空 = http（老连接）。"""
        return resolve_source_type(self.config)

    def is_git(self) -> bool:
        return self.source_type() == "git"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        # connections.build_adapter 把解密后的凭证并进 config（见 adapter_config），
        # 因此 token 三处都找：显式 credentials → 合并后 config → config.headers 模板。
        token = (
            self.credentials.get("api_token")
            or self.credentials.get("authorization")
            or self.config.get("api_token")
            or self.config.get("authorization")
            or ""
        )
        if token:
            headers["Authorization"] = token if token.lower().startswith("bearer ") \
                else f"Bearer {token}"
        for k, v in (self.config.get("headers") or {}).items():
            headers[str(k)] = str(v)
        return headers

    def fetch_index(self, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
        """拉取源索引（dict）。file:// 读本地，其余走 HTTP。"""
        url = self._url()
        if not url:
            from ..errors import ValidationFailed as _VF
            raise _VF("hub_market_source_url_required", "市场源未配置源链接")
        data = self._get_json(url, timeout_seconds=timeout_seconds)
        if not isinstance(data, dict):
            from ..errors import ValidationFailed as _VF
            raise _VF("hub_market_source_bad_index", "源索引不是 JSON 对象")
        return data

    def fetch_package(self, download_url: str, *,
                      timeout_seconds: float = 20.0) -> dict[str, Any]:
        """拉取单个包的 manifest（dict）。每条下载链接都要过 SSRF 守卫，
        不能因为索引可信就放行包链接（索引内容是远程的、不可信的）。"""
        data = self._get_json(download_url, timeout_seconds=timeout_seconds)
        if not isinstance(data, dict):
            from ..errors import ValidationFailed as _VF
            raise _VF("hub_market_source_bad_package", "包 manifest 不是 JSON 对象")
        return data

    def _get_json(self, url: str, *, timeout_seconds: float) -> Any:
        import json as _json

        parsed = urlparse(url)
        if parsed.scheme == "file":
            import re as _re
            from pathlib import Path as _Path
            # Windows：file:///C:/x → path 为 /C:/x，需去首斜杠。
            fs_path = parsed.path
            if _re.match(r"^/[A-Za-z]:", fs_path):
                fs_path = fs_path[1:]
            target = _Path(fs_path)
            if target.is_dir():
                target = target / "index.json"
            try:
                return _json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                from ..errors import ValidationFailed as _VF
                raise _VF("hub_market_source_unreadable",
                          f"本地源不可读：{exc}") from None
        # 远程链接逐条过 SSRF 守卫（含索引里带来的 download_url）。
        safe_url = guard_endpoint(url)
        import httpx

        kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        try:
            with httpx.Client(**kwargs) as client:
                resp = client.get(safe_url, headers=self._headers())
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:  # noqa: BLE001 — 转译为带码错误，不裸抛
            from ..errors import ValidationFailed as _VF
            raise _VF("hub_market_source_unreachable",
                      f"源不可达：{type(exc).__name__}") from None

    def list_packages(self, *, timeout_seconds: float = 10.0) -> list[dict[str, Any]]:
        """索引归一化为包条目（缺字段的条目丢弃并计数，不伪造）。"""
        if self.is_git():
            return self._list_git_packages(timeout_seconds=self._git_import_timeout(timeout_seconds))
        index = self.fetch_index(timeout_seconds=timeout_seconds)
        raw = index.get("packages")
        if not isinstance(raw, list):
            from ..errors import ValidationFailed as _VF
            raise _VF("hub_market_source_bad_index", "源索引缺少 packages 数组")
        out: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("id") or "").strip()
            durl = str(entry.get("download_url") or "").strip()
            if not pid or not durl:
                continue
            out.append({
                "id": pid,
                "name": str(entry.get("name") or pid),
                "version": str(entry.get("version") or ""),
                "description": str(entry.get("description") or ""),
                "download_url": durl,
                "license": str(entry.get("license") or ""),
            })
        return out

    def load_package_manifest(self, entry: dict[str, Any], *,
                              timeout_seconds: float = 20.0) -> dict[str, Any]:
        """按索引条目取包 manifest。

        HTTP/file 源走 ``download_url``；Git 源的包就在仓库里（``manifest_path``
        相对路径），不再单独下载——两种来源共用同一个入口，调用方无需分支。
        """
        if self.is_git():
            root = self._repo_root or self._clone(timeout_seconds=timeout_seconds)
            target = _resolve_in_repo(root, str(entry.get("manifest_path") or ""))
            data = self._read_repo_json(target)
            if not isinstance(data, dict):
                raise ValidationFailed("hub_market_source_bad_package", "包 manifest 不是 JSON 对象")
            return data
        return self.fetch_package(str(entry.get("download_url") or ""),
                                  timeout_seconds=timeout_seconds)

    # -- git ------------------------------------------------------------- #
    def _list_git_packages(self, *, timeout_seconds: float) -> list[dict[str, Any]]:
        root = self._clone(timeout_seconds=timeout_seconds)
        index_rel, index = self._read_repo_index(root)
        raw = index.get("packages")
        if not isinstance(raw, list):
            # 单包仓库：清单文件本身就是包 manifest（没有 packages 数组时唯一合理的形态）
            name = str(index.get("name") or "").strip()
            if name:
                return [{
                    "id": name,
                    "name": name,
                    "version": str(index.get("semantic_version") or index.get("version") or ""),
                    "description": str(index.get("description") or ""),
                    # Git 源的包不落在 URL 上；download_url 置空只为保持条目形状一致。
                    "download_url": "",
                    "manifest_path": index_rel,
                    "license": str(index.get("license") or ""),
                }]
            raise ValidationFailed(
                "hub_market_source_git_bad_index",
                "Git 源清单既没有 packages 数组，也不是单包 manifest（缺 name）",
            )
        out: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("id") or "").strip()
            # Git 源没有 download_url，取包靠仓库内相对路径；缺路径的条目丢弃（同 HTTP 源缺链接丢弃）。
            rel = str(entry.get("manifest_path") or entry.get("path") or "").strip()
            if not pid or not rel:
                continue
            out.append({
                "id": pid,
                "name": str(entry.get("name") or pid),
                "version": str(entry.get("version") or ""),
                "description": str(entry.get("description") or ""),
                "download_url": "",
                "manifest_path": rel,
                "license": str(entry.get("license") or ""),
            })
        return out

    def _clone(self, *, timeout_seconds: float) -> Path:
        """clone 到临时目录并返回仓库根；同一次导入内复用，:meth:`close` 负责清理。"""
        if self._repo_root is not None:
            return self._repo_root
        url = guard_git_url(self._url())
        workdir = Path(self._workdir) if self._workdir else Path(tempfile.gettempdir())
        workdir.mkdir(parents=True, exist_ok=True)
        dest = Path(tempfile.mkdtemp(prefix="fy-market-git-", dir=str(workdir)))
        argv = [
            "git", "-c", "core.hooksPath=", "-c", "credential.helper=",
            "clone", "--quiet", "--no-tags", "--single-branch",
            "--depth", str(_positive_int(self.config.get("git_depth"), GIT_DEFAULT_DEPTH)),
        ]
        ref = str(self.config.get("git_ref") or "").strip()
        if ref:
            if not _GIT_REF_RE.match(ref):
                raise ValidationFailed(
                    "hub_market_source_git_bad_ref", f"非法分支/标签名 '{ref}'"
                )
            argv += ["--branch", ref]
        # `--` 之后才是位置参数：仓库地址绝不会被当成 git 选项（参数注入）。
        argv += ["--", url, str(dest)]
        code, _out, err = (self._git_runner or _run_git)(argv, self._timeout(timeout_seconds))
        if code != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise ValidationFailed(
                "hub_market_source_git_clone_failed",
                f"git clone 失败（退出码 {code}）：{err.strip()[:200] or '无输出'}",
            )
        try:
            _enforce_repo_limits(dest, self.config)
        except ValidationFailed:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        self._repo_root = dest
        return dest

    def _timeout(self, fallback: float) -> float:
        """clone 超时：显式 ``config.git_timeout_seconds`` 优先，否则用给定默认。"""
        try:
            raw = float(self.config.get("git_timeout_seconds") or fallback)
        except (TypeError, ValueError):
            raw = fallback
        return max(1.0, min(raw, 600.0))

    def _git_import_timeout(self, requested: float) -> float:
        """导入路径的 clone 预算下限 :data:`GIT_DEFAULT_TIMEOUT`。

        调用方传来的 10s/20s 是「拉一个 JSON 索引 / 一个包」的量级，clone 整个仓库
        不是一个量级——照搬会让大仓库在稳定网络下也必然超时。
        """
        return self._timeout(max(requested, GIT_DEFAULT_TIMEOUT))

    def _read_repo_index(self, root: Path) -> tuple[str, dict[str, Any]]:
        configured = str(self.config.get("git_manifest") or "").strip()
        candidates = ([configured] if configured else []) + [
            c for c in GIT_MANIFEST_CANDIDATES if c != configured
        ]
        for rel in candidates:
            target = _resolve_in_repo(root, rel)
            if target.is_file():
                data = self._read_repo_json(target)
                if not isinstance(data, dict):
                    raise ValidationFailed(
                        "hub_market_source_git_bad_index", "Git 源清单不是 JSON 对象"
                    )
                return rel, data
        raise ValidationFailed(
            "hub_market_source_git_manifest_missing",
            f"仓库里找不到清单文件（已试：{', '.join(candidates)}）"
            "——可用 config.git_manifest 指定仓库内的清单相对路径",
        )

    @staticmethod
    def _read_repo_json(target: Path) -> Any:
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValidationFailed(
                "hub_market_source_git_bad_json",
                f"仓库内 JSON 不可读：{target.name}（{type(exc).__name__}）",
            ) from None

    def close(self) -> None:
        """清掉 clone 出的临时仓库（导入结束必须调用，否则磁盘留垃圾）。"""
        root, self._repo_root = self._repo_root, None
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)

    def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
        started = time.perf_counter()
        try:
            if self.is_git():
                # Git 源判活必须真 clone 一遍，所以给它自己的小预算：既不用 HTTP 的
                # 3s（clone 根本跑不完，会把可用源误报成不可用），也不给导入那套 60s
                # （一个慢仓库能把 health-check-all 整批拖死）。
                packages = self._list_git_packages(
                    timeout_seconds=self._timeout(max(timeout_seconds, GIT_HEALTH_TIMEOUT)))
            else:
                packages = self.list_packages(timeout_seconds=timeout_seconds)
        except Exception as exc:  # noqa: BLE001 — 健康检查只报告，不抛
            return HealthReport(
                ok=False, latency_ms=_elapsed_ms(started),
                detail=f"源不可用：{exc}",
                checked_at=_stamp(),
                endpoint_ref=_endpoint_ref(self._url()),
            )
        return HealthReport(
            ok=True, latency_ms=_elapsed_ms(started),
            detail=f"源可达：{len(packages)} 个包",
            checked_at=_stamp(),
            endpoint_ref=_endpoint_ref(self._url()),
        )


def build_adapter(    kind: str,
    config: dict[str, Any],
    *,
    credentials: dict[str, str] | None = None,
    transport: Any | None = None,
    mcp_client: Any | None = None,
    sleep: Any | None = None,
):
    """Build the adapter for ``kind``. Unknown kinds raise (no silent fallback)."""
    from ..errors import ValidationFailed as _VF

    cfg = dict(config or {})
    cfg["kind"] = kind
    if credentials:
        cfg.update({k: v for k, v in credentials.items() if v})
    if kind in {KIND_OPENAI_CHAT, KIND_ANTHROPIC}:
        return ChatModelAdapter(cfg, transport=transport)
    if kind == KIND_MCP_SERVER:
        return McpServerAdapter(cfg, client=mcp_client)
    if kind in {KIND_HTTP_WEBHOOK, KIND_TOOL_PLUGIN}:
        return WebhookAdapter(cfg, credentials=credentials, transport=transport, sleep=sleep)
    if kind == KIND_KNOWLEDGE_SOURCE:
        return KnowledgeSourceAdapter(cfg)
    if kind == KIND_MARKET_SOURCE:
        return MarketSourceAdapter(cfg, credentials=credentials, transport=transport)
    raise _VF("hub_unknown_kind", f"未知适配器类型 '{kind}'；支持："
                                  "openai_chat / anthropic / mcp_server / http_webhook / "
                                  "knowledge_source / tool_plugin / market_source")
