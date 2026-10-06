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
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from ..errors import ValidationFailed
from . import (
    KIND_ANTHROPIC,
    KIND_HTTP_WEBHOOK,
    KIND_KNOWLEDGE_SOURCE,
    KIND_MCP_SERVER,
    KIND_OPENAI_CHAT,
    KIND_TOOL_PLUGIN,
)

# --------------------------------------------------------------------------- #
# Value types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Capability:
    """One thing a connection can do.

    ``name`` uses a ``<domain>.<action>`` shape inside the hub namespace
    (``chat`` / ``tool:<tool>`` / ``knowledge.search`` ...).
    ``tags`` drive v1 routing (router.py) — they are matched against the task hint.
    """

    name: str
    tags: tuple[str, ...] = ()
    description: str = ""

    def to_public(self) -> dict[str, Any]:
        return {"name": self.name, "tags": list(self.tags), "description": self.description}

    @classmethod
    def from_public(cls, raw: Any) -> "Capability":
        if isinstance(raw, Capability):
            return raw
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ValidationFailed("hub_invalid_capability", "能力条目缺少 name")
        tags = raw.get("tags") or []
        return cls(
            name=str(raw["name"]),
            tags=tuple(str(t) for t in tags if str(t).strip()),
            description=str(raw.get("description") or ""),
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
        started = time.perf_counter()
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
        base = [Capability(name="chat", tags=tags, description=f"对话补全（{provider_id}）")]
        return _merge_capabilities(base, declared)

    def invoke(self, call: InvokeCall) -> InvokeResult:
        from ...runtime.providers import ProviderError, build_provider

        started = time.perf_counter()
        action = (call.action or "complete").strip().lower()
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
            prompt = str(call.params.get("prompt") or "")
            if not prompt.strip():
                raise ValidationFailed("hub_missing_prompt", "缺少 prompt 参数")
            result = provider.complete(
                model=str(call.params.get("model") or self.model()),
                prompt=prompt,
                max_tokens=int(call.params.get("max_tokens") or 1024),
                timeout_seconds=call.timeout_seconds,
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
        discovered = [
            Capability(name=f"tool:{name}", tags=("tool", "mcp", server),
                       description=f"MCP 工具 {name}（{server}）")
            for name in self.tool_names()
        ]
        declared = [Capability.from_public(c) for c in (self.config.get("capabilities") or [])]
        return _merge_capabilities(discovered, declared)

    def invoke(self, call: InvokeCall) -> InvokeResult:
        started = time.perf_counter()
        action = (call.action or "call_tool").strip().lower()
        if action not in {"call_tool", "tools"}:
            return InvokeResult(
                ok=False, latency_ms=_elapsed_ms(started),
                error=f"hub_unsupported_action: mcp 适配器不支持 action='{action}'"
                      "（支持 call_tool）",
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
                           description="通用 HTTP 调用")]

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
                       description=f"列出 {source.source_id} 的知识库/目录")
        ]
        if getattr(caps, "searchable", False):
            out.append(Capability(name="knowledge.search", tags=("knowledge", "search"),
                                  description=f"服务端检索 {source.source_id}"))
        if getattr(caps, "full_text", False):
            out.append(Capability(name="knowledge.fetch", tags=("knowledge", "document"),
                                  description=f"抓取 {source.source_id} 文档正文"))
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
    """Discovered capabilities first; declared ones fill gaps / add tags."""
    out: list[Capability] = list(discovered)
    by_name = {c.name: i for i, c in enumerate(out)}
    for cap in declared:
        if cap.name in by_name:
            idx = by_name[cap.name]
            merged = tuple(dict.fromkeys(tuple(out[idx].tags) + tuple(cap.tags)))
            out[idx] = Capability(name=cap.name, tags=merged,
                                  description=cap.description or out[idx].description)
        else:
            out.append(cap)
    return out


def build_adapter(
    kind: str,
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
    raise _VF("hub_unknown_kind", f"未知适配器类型 '{kind}'；支持："
                                  "openai_chat / anthropic / mcp_server / http_webhook / "
                                  "knowledge_source / tool_plugin")
