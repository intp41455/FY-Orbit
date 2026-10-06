"""MCP (Model Context Protocol) adapter (G7/A02 · 补齐包3 A-统一接入-02/09)。

Python 标准库 + httpx 实现，不依赖官方 MCP SDK。核心是换行分隔的 JSON-RPC 2.0
帧（``_LineTransport`` 抽象），其上支持**四种传输**：

=================  ==================================================  ==========
传输               机制                                                信任默认
=================  ==================================================  ==========
stdio              子进程 stdin/stdout（``from_subprocess``）           trusted
in-process         进程内 :class:`McpStdioServer`（``from_server``）    trusted
HTTP               无状态 JSON-RPC over HTTP POST（``from_url``）       remote
SSE                HTTP+SSE：GET 事件流 + POST 消息（``from_url``）     remote
WS                 WebSocket 文本帧（``from_url``，需 ``websockets``）  remote
=================  ==================================================  ==========

方法覆盖（客户端）：``initialize`` / ``tools/list`` / ``tools/call`` /
``resources/list`` / ``resources/read`` / ``prompts/list`` / ``prompts/get``；
服务端（:class:`McpStdioServer`）对称支持以上发现与读取。

高级机制（A-统一接入-09）：

* **动态刷新** —— 服务端 ``notify_tools_changed()`` 推送
  ``notifications/tools/list_changed``；客户端 ``on_tools_changed`` 收到后调
  ``refresh_tools()`` 重新发现。无服务端推送的 stateless HTTP 传输不支持推送，
  调用方需显式 ``refresh_tools()``（诚实边界，不假装有推送）。
* **Elicitation** —— 服务端工具执行中经 ``ToolContext.elicit`` 向客户端发起
  ``elicitation/create`` 交互式补问；客户端 ``on_elicitation`` 注册应答器，
  未注册时诚实默认 ``decline``（绝不伪造 accept）。
* **信任分级** —— :class:`McpTrustPolicy`：``trusted``（本机 stdio/进程内）
  无门禁；``remote`` 每次 ``tools/call`` 需 :attr:`McpTrustPolicy.confirm`
  回调放行，否则 :class:`McpConfirmationRequired`；``untrusted`` 拒绝一切
  工具调用。remote/untrusted 一律**禁 inline shell**（按工具名启发式 +
  ``x-fy-inline-shell`` 标注），不可被 confirm 覆盖。

断线重连：:class:`ReconnectPolicy` —— 传输层失败后按有界退避重建传输并重新
``initialize``（stdio 重 spawn 子进程、SSE/WS 重新握手）。

装配（§八 B1 收敛）：:func:`assemble_mcp_tools` 是**唯一**装配入口（支持
``prefix`` 命名空间参数）；hub 的 ``McpServerAdapter.register_tools`` 只是它
``prefix="hub."`` 的委托调用，两处不再各养一套装配逻辑。

诚实原则：连不上就是异常/跳过并留 WARNING，绝不构造假响应。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_CANCELLED = -32000
ERR_RESOURCE_NOT_FOUND = -32002
ERR_PERMISSION_DENIED = -32003
ERR_TOOL_NOT_FOUND = -32004
ERR_TOOL_EXECUTION = -32005
ERR_UNTRUSTED_SERVER = -32008
ERR_CONFIRMATION_REQUIRED = -32009
ERR_INLINE_SHELL_BLOCKED = -32010
ERR_ELICITATION_UNSUPPORTED = -32011

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "find-yourself-mcp"
SERVER_VERSION = "0.2.0"

#: 支持的传输协议（A-统一接入-02：stdio / HTTP / SSE / WS 四协议 + 进程内）。
TRANSPORTS: tuple[str, ...] = ("stdio", "in_process", "http", "sse", "ws")


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    privileged: bool = False
    handler: Callable[..., Any] = field(default=None, repr=False)
    #: True 时 handler 以 ``handler(arguments, ctx)`` 调用，``ctx.elicit(...)``
    #: 可在执行中向客户端发起 elicitation 交互补问。
    wants_context: bool = False


@dataclass
class McpResource:
    uri: str
    name: str = ""
    description: str = ""
    text: str = ""


@dataclass
class McpPrompt:
    name: str
    description: str = ""
    template: str = ""


# --------------------------------------------------------------------------- #
# 信任分级（A-统一接入-09）
# --------------------------------------------------------------------------- #

TRUST_TRUSTED = "trusted"
TRUST_REMOTE = "remote"
TRUST_UNTRUSTED = "untrusted"
TRUST_LEVELS: tuple[str, ...] = (TRUST_TRUSTED, TRUST_REMOTE, TRUST_UNTRUSTED)

#: inline shell 启发式：这些工具名（小写）视为宿主机 shell 执行面。
INLINE_SHELL_TOOL_NAMES = frozenset(
    {"shell", "bash", "sh", "exec", "execute", "run_command", "terminal", "cmd"}
)
#: 显式标注：工具条目带 ``x-fy-inline-shell: true`` 即按 inline shell 处理。
INLINE_SHELL_KEY = "x-fy-inline-shell"


class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class McpUntrustedServer(McpError):
    """untrusted 服务器拒绝一切工具调用。"""


class McpConfirmationRequired(McpError):
    """remote 服务器缺 confirm 回调（或回调拒绝）——需显式信任确认。"""


class McpInlineShellBlocked(McpError):
    """remote/untrusted 服务器上的 inline shell 工具一律拒绝（硬规则）。"""


@dataclass(frozen=True)
class McpTrustPolicy:
    """MCP 服务器信任分级。

    * ``trusted``   —— 本机 stdio / 进程内；无门禁（同一信任域）。
    * ``remote``    —— 远端服务器；每次 ``tools/call`` 需 ``confirm`` 回调放行；
                      inline shell 工具无条件拒绝。
    * ``untrusted`` —— 拒绝一切 ``tools/call``（confirm 也不能放行）；
                      发现类只读方法（tools/list 等）仍可用。
    """

    level: str = TRUST_REMOTE
    confirm: Callable[[dict[str, Any]], bool] | None = None
    #: 仅供本地受信域内部复用场景显式放开 inline shell；remote/untrusted 下
    #: 本字段不生效（硬规则，不可被确认覆盖）。
    allow_inline_shell: bool = False

    def __post_init__(self) -> None:
        if self.level not in TRUST_LEVELS:
            raise McpError(ERR_INVALID_PARAMS,
                           f"unknown trust level '{self.level}'；TRUST_LEVELS: {TRUST_LEVELS}")

    @staticmethod
    def _is_inline_shell(name: str, tool_meta: dict[str, Any] | None) -> bool:
        if tool_meta and tool_meta.get(INLINE_SHELL_KEY) is True:
            return True
        return str(name or "").strip().lower() in INLINE_SHELL_TOOL_NAMES

    def gate_tool_call(self, name: str, tool_meta: dict[str, Any] | None = None) -> None:
        """工具调用前的信任门禁；违规即抛 :class:`McpError` 家族异常。"""
        if self.level == TRUST_TRUSTED:
            return
        if self._is_inline_shell(name, tool_meta):
            raise McpInlineShellBlocked(
                ERR_INLINE_SHELL_BLOCKED,
                f"inline shell tool '{name}' is blocked for {self.level} MCP servers",
            )
        if self.level == TRUST_UNTRUSTED:
            raise McpUntrustedServer(
                ERR_UNTRUSTED_SERVER,
                f"untrusted MCP server: tool call '{name}' refused",
            )
        # remote：需显式确认
        decision: dict[str, Any] = {"tool": name, "level": self.level}
        if self.confirm is None:
            raise McpConfirmationRequired(
                ERR_CONFIRMATION_REQUIRED,
                "remote MCP server requires confirmation; set McpTrustPolicy.confirm",
            )
        try:
            allowed = bool(self.confirm(decision))
        except Exception as exc:  # noqa: BLE001 — 确认器自身故障按拒绝处理
            raise McpConfirmationRequired(
                ERR_CONFIRMATION_REQUIRED, f"confirmation callback failed: {exc}"
            ) from exc
        if not allowed:
            raise McpConfirmationRequired(
                ERR_CONFIRMATION_REQUIRED, f"tool call '{name}' was not confirmed"
            )


# --------------------------------------------------------------------------- #
# 重连策略
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReconnectPolicy:
    """有界退避重连：重建传输并重新 ``initialize``。"""

    max_attempts: int = 3
    backoff_seconds: float = 0.1
    backoff_factor: float = 2.0
    sleep: Callable[[float], None] = time.sleep


class _TransportDead(Exception):
    """传输层断开（EOF / 连接失败 / 读超时），可触发重连。"""


# --------------------------------------------------------------------------- #
# 传输层
# --------------------------------------------------------------------------- #


class _LineTransport:
    """换行分隔 JSON-RPC 帧的传输原语（子类实现）。"""

    def send_line(self, text: str) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    def recv_line(self, timeout: float | None = None) -> str | None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - 接口
        raise NotImplementedError


class _StdioTransport(_LineTransport):
    """子进程 stdin/stdout。诚实边界：recv 阻塞读，不支持读超时。"""

    def __init__(self, proc: Any):
        self._proc = proc

    def send_line(self, text: str) -> None:
        stdin, stdout = self._proc.stdin, None
        if stdin is None:
            raise _TransportDead("stdio child has no stdin")
        try:
            stdin.write(text + "\n")
            stdin.flush()
        except (OSError, ValueError) as exc:
            raise _TransportDead(f"stdio write failed: {exc}") from exc

    def recv_line(self, timeout: float | None = None) -> str | None:
        if self._proc.stdout is None:
            raise _TransportDead("stdio child has no stdout")
        try:
            line = self._proc.stdout.readline()
        except (OSError, ValueError) as exc:
            raise _TransportDead(f"stdio read failed: {exc}") from exc
        if not line:
            raise _TransportDead("MCP subprocess closed pipe prematurely")
        return line

    def close(self) -> None:
        proc = self._proc
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


class _InProcessTransport(_LineTransport):
    """进程内 McpStdioServer；服务端→客户端请求（elicitation）经 respond 回调。"""

    def __init__(self, server: "McpStdioServer",
                 on_server_request: Callable[[dict], dict | None] | None = None):
        self._server = server
        self._on_server_request = on_server_request
        self._pending: str | None = None

    def send_line(self, text: str) -> None:
        resp = self._server.handle_line(text, respond=self._on_server_request)
        # 统一行协议：客户端侧一律收发 JSON 文本帧。
        self._pending = json.dumps(resp) if resp is not None else None

    def recv_line(self, timeout: float | None = None) -> str | None:
        line = self._pending
        self._pending = None
        if line is None:
            raise _TransportDead("in-process server produced no response")
        return line

    def close(self) -> None:
        self._pending = None


class _HttpTransport(_LineTransport):
    """无状态 JSON-RPC over HTTP POST。

    诚实边界：每 POST 恰好一响应，服务器推送（通知/elicitation）不可达；
    需要推送的部署用 SSE / WS。
    """

    def __init__(self, url: str, *, headers: dict[str, str] | None = None,
                 http: Any | None = None, timeout: float = 10.0):
        self._url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._http = http
        self._owns_client = http is None
        self._stashed: str | None = None

    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=self._timeout)
        elif not hasattr(self._http, "post"):
            # 注入的是 httpx transport（如 MockTransport）而非 client：包一层。
            import httpx

            self._http = httpx.Client(transport=self._http, timeout=self._timeout)
        return self._http

    def send_line(self, text: str) -> None:
        try:
            resp = self._client().post(self._url, content=text,
                                       headers={"Content-Type": "application/json",
                                                **self._headers})
        except Exception as exc:  # noqa: BLE001 — 网络错误归一为断线
            raise _TransportDead(f"http post failed: {exc}") from exc
        if resp.status_code in (202, 204) or not resp.content:
            self._stashed = None
            return
        self._stashed = resp.text

    def recv_line(self, timeout: float | None = None) -> str | None:
        line = self._stashed
        self._stashed = None
        if line is None:
            raise _TransportDead("http transport returned no payload")
        return line

    def close(self) -> None:
        if self._owns_client and self._http is not None:
            try:
                self._http.close()
            except Exception:  # noqa: BLE001
                pass
            self._http = None


def _parse_sse_event(lines: Iterator[str]) -> tuple[str, str] | None:
    """从 SSE 行迭代中取下一个 (event, data)；注释/空行跳过，多行 data 拼接。

    ``lines`` 的每个元素可含多行（iter_lines 语义）也可不含，这里统一
    ``splitlines`` 展开。
    """
    event = ""
    data_chunks: list[str] = []
    for raw in lines:
        # splitlines 保留结尾空行（事件终止符），且已处理 \r\n。
        for line in raw.splitlines():
            if not line:
                if event or data_chunks:
                    return event or "message", "\n".join(data_chunks)
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_chunks.append(line[len("data:"):].strip())
    if event or data_chunks:
        return event or "message", "\n".join(data_chunks)
    return None


class _SseHttp:
    """SSE 传输的 HTTP 抽象：开流 + POST 消息（测试可注入桩实现）。"""

    def open_stream(self) -> Iterator[str]:  # pragma: no cover - 接口
        raise NotImplementedError

    def post_message(self, url: str, text: str) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - 接口
        raise NotImplementedError


class _HttpxSseHttp(_SseHttp):
    """httpx 实现：GET ``text/event-stream`` 长连接 + POST 消息端点。"""

    def __init__(self, url: str, *, headers: dict[str, str] | None = None,
                 client: Any | None = None, timeout: float = 10.0):
        self._url = url.rstrip("/")
        self._headers = dict(headers or {})
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._stream_ctx = None
        self._stream = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=self._timeout)
        elif not hasattr(self._client, "stream"):
            # 注入的是 httpx transport（如 MockTransport）而非 client：包一层。
            import httpx

            self._client = httpx.Client(transport=self._client, timeout=self._timeout)
        return self._client

    def open_stream(self) -> Iterator[str]:
        client = self._ensure_client()
        self._stream_ctx = client.stream(
            "GET", self._url,
            headers={"Accept": "text/event-stream", **self._headers},
        )
        self._stream = self._stream_ctx.__enter__()
        if self._stream.status_code >= 400:
            self._stream_ctx.__exit__(None, None, None)
            raise _TransportDead(f"SSE handshake failed with HTTP {self._stream.status_code}")
        return self._stream.iter_lines()

    def post_message(self, url: str, text: str) -> None:
        try:
            resp = self._ensure_client().post(
                url, content=text,
                headers={"Content-Type": "application/json", **self._headers})
        except Exception as exc:  # noqa: BLE001
            raise _TransportDead(f"sse post failed: {exc}") from exc
        if resp.status_code >= 400:
            raise _TransportDead(f"sse post failed with HTTP {resp.status_code}")

    def close(self) -> None:
        if self._stream_ctx is not None:
            try:
                self._stream_ctx.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._stream_ctx = None
        if self._owns_client and self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


class _SseTransport(_LineTransport):
    """MCP HTTP+SSE 传输（legacy spec）：

    1. GET 服务端 SSE 流；首个 ``event: endpoint`` 帧给出消息 POST 端点；
    2. 请求经 POST 发往该端点（202 Accepted 即成功）；
    3. 响应经 ``event: message`` 帧从 SSE 流异步到达（后台 reader 线程收帧入队）。

    断线表现：POST 失败或流提前 EOF → :class:`_TransportDead` → 由
    :class:`ReconnectPolicy` 重新握手。
    """

    def __init__(self, url: str, *, headers: dict[str, str] | None = None,
                 http: _SseHttp | None = None):
        self._url = url.rstrip("/")
        self._headers = dict(headers or {})
        self._http = http or _HttpxSseHttp(self._url, headers=self._headers)
        self._endpoint_url: str | None = None
        self._lines: Iterator[str] | None = None
        self._queue: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._closed = threading.Event()

    # -- 握手 ---------------------------------------------------------------- #
    def is_connected(self) -> bool:
        return self._endpoint_url is not None

    def connect(self) -> None:
        try:
            lines = self._http.open_stream()
        except _TransportDead:
            raise
        except Exception as exc:  # noqa: BLE001 — 握手失败归一为断线
            raise _TransportDead(f"SSE handshake failed: {exc}") from exc
        first = _parse_sse_event(lines)
        if first is None or first[0] != "endpoint":
            raise _TransportDead("SSE handshake: missing 'endpoint' event")
        data = first[1].strip()
        if not data:
            raise _TransportDead("SSE handshake: empty endpoint payload")
        self._endpoint_url = self._resolve(data)
        self._lines = lines
        self._closed.clear()
        self._reader = threading.Thread(
            target=self._read_loop, name="fy-mcp-sse-reader", daemon=True)
        self._reader.start()

    def _resolve(self, endpoint: str) -> str:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        from urllib.parse import urljoin

        # urljoin 语义：以 "/" 开头 = 站点绝对路径；否则相对当前路径解析。
        return urljoin(self._url, endpoint)

    def _read_loop(self) -> None:
        try:
            assert self._lines is not None
            while not self._closed.is_set():
                evt = _parse_sse_event(self._lines)
                if evt is None:
                    break
                event, data = evt
                if event == "message" and data:
                    self._queue.put(data)
        except Exception:  # noqa: BLE001 — 流异常按 EOF 处理
            pass
        finally:
            if not self._closed.is_set():
                self._queue.put("")  # 哨兵：流已断

    # -- 帧收发 --------------------------------------------------------------- #
    def send_line(self, text: str) -> None:
        if self._endpoint_url is None:
            raise _TransportDead("SSE transport not connected")
        self._http.post_message(self._endpoint_url, text)

    def recv_line(self, timeout: float | None = None) -> str | None:
        try:
            data = self._queue.get(timeout=timeout if timeout is not None else 30.0)
        except queue.Empty as exc:
            raise _TransportDead("SSE recv timeout") from exc
        if data == "":
            raise _TransportDead("SSE stream closed by server")
        return data

    def close(self) -> None:
        self._closed.set()
        self._queue.put("")
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass


class _WsConnection:
    """WS 连接抽象（测试可注入桩实现）。"""

    def send(self, text: str) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    def recv(self, timeout: float | None = None) -> str:  # pragma: no cover - 接口
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - 接口
        raise NotImplementedError


def _default_ws_connect(url: str, headers: dict[str, str]) -> _WsConnection:
    """生产实现：websockets.sync 客户端（uvicorn[standard] 已带 websockets）。"""
    from websockets.sync.client import connect

    return connect(url, additional_headers=headers)  # type: ignore[return-value]


class _WsTransport(_LineTransport):
    """WebSocket 文本帧传输：双向同帧，天然支持服务端推送与 elicitation。"""

    def __init__(self, url: str, *, headers: dict[str, str] | None = None,
                 connect_factory: Callable[[str, dict[str, str]], _WsConnection] | None = None):
        self._url = url
        self._headers = dict(headers or {})
        self._factory = connect_factory or _default_ws_connect
        self._ws: _WsConnection | None = None

    def is_connected(self) -> bool:
        return self._ws is not None

    def connect(self) -> None:
        try:
            self._ws = self._factory(self._url, self._headers)
        except _TransportDead:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _TransportDead(f"ws connect failed: {exc}") from exc

    def send_line(self, text: str) -> None:
        if self._ws is None:
            raise _TransportDead("ws transport not connected")
        try:
            self._ws.send(text)
        except Exception as exc:  # noqa: BLE001
            raise _TransportDead(f"ws send failed: {exc}") from exc

    def recv_line(self, timeout: float | None = None) -> str | None:
        if self._ws is None:
            raise _TransportDead("ws transport not connected")
        try:
            return self._ws.recv(timeout)
        except _TransportDead:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _TransportDead(f"ws recv failed: {exc}") from exc

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None


# --------------------------------------------------------------------------- #
# 服务端核心
# --------------------------------------------------------------------------- #


class _McpError(McpError):
    """向后兼容别名（旧内部名）。"""


class ToolContext:
    """工具执行上下文：目前承载 elicitation 交互补问。"""

    def __init__(self, elicit_fn: Callable[[str, dict], dict]):
        self._elicit = elicit_fn

    def elicit(self, message: str, requested_schema: dict) -> dict:
        """向客户端发起 ``elicitation/create``；返回 ``{"action", "content"}``。

        客户端无应答器时诚实返回 ``{"action": "decline"}``——绝不伪造 accept。
        """
        return self._elicit(message, requested_schema)


class McpStdioServer:
    """Line-delimited JSON-RPC MCP server core, testable without a real process.

    除工具外还承载资源/提示词发现（A-统一接入-02）与 ``notify_tools_changed``
    动态刷新推送（A-统一接入-09）。
    """

    def __init__(
        self,
        tools: list[McpTool],
        allowed_privileged: set[str] | None = None,
        resources: list[McpResource] | None = None,
        prompts: list[McpPrompt] | None = None,
        notify_sink: Callable[[dict], None] | None = None,
    ):
        self._tools = {t.name: t for t in tools}
        # Scopes the SERVER injects for the caller; never caller-supplied.
        self._allowed_privileged = set(allowed_privileged or [])
        self._resources = {r.uri: r for r in (resources or [])}
        self._prompts = {p.name: p for p in (prompts or [])}
        self._notify_sink = notify_sink
        self.initialized = False
        self.cancelled: set[str] = set()
        self._respond: Callable[[dict], dict | None] | None = None
        self._elicit_seq = 0

    # -- framing -------------------------------------------------------------
    def handle_line(
        self,
        line: str,
        respond: Callable[[dict], dict | None] | None = None,
    ) -> dict | None:
        """Process one JSON-RPC line; return a response dict, or None (notification).

        ``respond`` 是服务端→客户端请求（elicitation）的下行通道：调用后返回
        客户端应答 dict。
        """
        self._respond = respond
        line = line.strip()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except (ValueError, TypeError):
            return {"jsonrpc": "2.0", "id": None,
                    "error": {"code": ERR_PARSE, "message": "Parse error"}}
        return self.dispatch(msg)

    def notify_tools_changed(self) -> None:
        """工具清单变化：向 notify_sink 推送 list_changed 通知（若已接）。"""
        if self._notify_sink is not None:
            self._notify_sink({
                "jsonrpc": "2.0",
                "method": "notifications/tools/list_changed",
            })

    def dispatch(self, msg: dict) -> dict | None:
        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                self.initialized = True
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": True},
                        "resources": {"listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }}
            if method == "notifications/initialized":
                self.initialized = True
                return None  # notification, no response
            if method == "notifications/cancelled":
                req = params.get("params", {}).get("requestId") or params.get("requestId")
                if req is not None:
                    self.cancelled.add(str(req))
                return None
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": [
                    {"name": t.name, "description": t.description,
                     "inputSchema": {"type": "object"}} for t in self._tools.values()
                ]}}
            if method == "tools/call":
                return self._tool_call(req_id, params)
            if method == "resources/list":
                return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": [
                    {"uri": r.uri, "name": r.name, "description": r.description}
                    for r in self._resources.values()
                ]}}
            if method == "resources/read":
                uri = params.get("uri")
                res = self._resources.get(uri)
                if res is None:
                    raise _McpError(ERR_RESOURCE_NOT_FOUND, f"Unknown resource: {uri}")
                return {"jsonrpc": "2.0", "id": req_id, "result": {"contents": [
                    {"uri": res.uri, "text": res.text}
                ]}}
            if method == "prompts/list":
                return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": [
                    {"name": p.name, "description": p.description}
                    for p in self._prompts.values()
                ]}}
            if method == "prompts/get":
                name = params.get("name")
                prompt = self._prompts.get(name)
                if prompt is None:
                    raise _McpError(ERR_RESOURCE_NOT_FOUND, f"Unknown prompt: {name}")
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "description": prompt.description,
                    "messages": [
                        {"role": "user", "content": {"type": "text", "text": prompt.template}}
                    ],
                }}
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": ERR_METHOD_NOT_FOUND, "message": f"Unknown method: {method}"}}
        except _McpError as e:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": e.code, "message": e.message, "data": e.data}}
        except Exception as e:  # never leak stack
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": ERR_INTERNAL, "message": "Internal error",
                              "data": {"type": type(e).__name__}}}

    # -- elicitation -----------------------------------------------------------
    def _elicit(self, message: str, requested_schema: dict) -> dict:
        """服务端→客户端的 elicitation 往返（同步应答）。"""
        if self._respond is None:
            raise _McpError(ERR_ELICITATION_UNSUPPORTED,
                            "server transport cannot elicit from this client")
        self._elicit_seq += 1
        request = {
            "jsonrpc": "2.0",
            "id": f"elicit-{self._elicit_seq}",
            "method": "elicitation/create",
            "params": {"message": message, "requestedSchema": requested_schema},
        }
        response = self._respond(request)
        if not isinstance(response, dict):
            return {"action": "decline"}
        if "error" in response:
            return {"action": "decline"}
        result = response.get("result")
        if not isinstance(result, dict) or result.get("action") != "accept":
            return {"action": "decline"}
        return {"action": "accept", "content": result.get("content") or {}}

    def _tool_call(self, req_id, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if req_id is not None and str(req_id) in self.cancelled:
            raise _McpError(ERR_CANCELLED, "Request cancelled")
        tool = self._tools.get(name)
        if tool is None:
            raise _McpError(ERR_TOOL_NOT_FOUND, f"Unknown tool: {name}")
        if tool.privileged and tool.name not in self._allowed_privileged:
            raise _McpError(ERR_PERMISSION_DENIED,
                            f"Caller not authorized for privileged tool: {name}")
        if tool.handler is None:
            raise _McpError(ERR_TOOL_EXECUTION, f"No handler wired for: {name}")
        try:
            if tool.wants_context:
                result = tool.handler(arguments, ToolContext(self._elicit))
            else:
                result = tool.handler(arguments)
        except _McpError:
            raise
        except Exception as e:
            raise _McpError(ERR_TOOL_EXECUTION, f"Tool failed: {type(e).__name__}")
        return {"jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result)}]}}


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #

DEFAULT_RECV_TIMEOUT = 30.0


class McpClient:
    """MCP 客户端：stdio / 进程内 / HTTP / SSE / WS 五种传输统一帧协议。

    兼容构造：

    * ``McpClient(server=...)`` —— 进程内服务器（旧签名，等价 from_server）。
    * ``McpClient(proc=...)``   —— 已有子进程（旧签名）。
    * ``McpClient(transport=...)`` —— 任意 :class:`_LineTransport`。

    断线重连：传 ``reconnect=ReconnectPolicy(...)`` 并确保 ``factory`` 可重建
    传输（from_subprocess / from_url 自带工厂）。
    """

    def __init__(self, server: McpStdioServer | None = None,
                 proc: Any | None = None,
                 transport: _LineTransport | None = None,
                 *,
                 factory: Callable[[], _LineTransport] | None = None,
                 reconnect: ReconnectPolicy | None = None,
                 trust: McpTrustPolicy | None = None,
                 recv_timeout: float = DEFAULT_RECV_TIMEOUT):
        if transport is not None:
            self._transport = transport
        elif server is not None:
            self._transport = _InProcessTransport(server,
                                                  on_server_request=self._handle_server_request)
        elif proc is not None:
            self._transport = _StdioTransport(proc)
        else:
            raise McpError(ERR_INTERNAL, "No server, process or transport wired to McpClient")
        self._factory = factory
        self._reconnect = reconnect
        self._trust = trust
        self._recv_timeout = recv_timeout
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.protocol_version: str = ""
        self._elicit_handler: Callable[[dict], dict] | None = None
        self._tools_changed_listeners: list[Callable[[], None]] = []
        self._notification_listeners: list[Callable[[dict], None]] = []
        self._tool_catalog: dict[str, dict] = {}
        self._initialized = False

    # -- 构造器 --------------------------------------------------------------- #

    @classmethod
    def from_server(cls, server: McpStdioServer, **kwargs: Any) -> "McpClient":
        return cls(server=server, **kwargs)

    @classmethod
    def from_subprocess(cls, cmd: list[str], env: dict | None = None,
                        *, reconnect: ReconnectPolicy | None = None,
                        trust: McpTrustPolicy | None = None) -> "McpClient":
        """本机 stdio 子进程；默认信任级 trusted（本机显式命令=同一信任域）。"""

        def _spawn() -> _StdioTransport:
            import os
            import subprocess

            proc_env = dict(os.environ)
            if env:
                proc_env.update(env)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=proc_env,
            )
            return _StdioTransport(proc)

        client = cls(
            transport=_spawn(), factory=_spawn,
            reconnect=reconnect,
            trust=trust or McpTrustPolicy(level=TRUST_TRUSTED),
        )
        return client

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        transport: str = "http",
        headers: dict[str, str] | None = None,
        trust: McpTrustPolicy | None = None,
        reconnect: ReconnectPolicy | None = None,
        http: Any | None = None,
        connect_factory: Callable[[str, dict[str, str]], _WsConnection] | None = None,
    ) -> "McpClient":
        """远端 MCP 服务器（HTTP / SSE / WS）；默认信任级 remote（需确认）。

        ``http`` / ``connect_factory`` 供测试注入桩实现；生产为 httpx /
        websockets.sync。
        """
        scheme = transport.strip().lower()
        if scheme == "http":
            build: Callable[[], _LineTransport] = (
                lambda: _HttpTransport(url, headers=headers, http=http))
        elif scheme == "sse":
            build = lambda: _SseTransport(url, headers=headers, http=http)  # noqa: E731
        elif scheme == "ws":
            build = lambda: _WsTransport(url, headers=headers,  # noqa: E731
                                         connect_factory=connect_factory)
        else:
            raise McpError(ERR_INVALID_PARAMS,
                           f"unknown remote transport '{transport}'；支持 http / sse / ws")
        client = cls(
            transport=build(), factory=build,
            reconnect=reconnect or ReconnectPolicy(),
            trust=trust or McpTrustPolicy(level=TRUST_REMOTE),
        )
        return client

    # -- 连接管理 -------------------------------------------------------------- #

    def _ensure_connected(self) -> None:
        """SSE / WS 有显式握手步骤；未连接时先连接（stdio/HTTP 幂等空操作）。"""
        transport = self._transport
        if isinstance(transport, (_SseTransport, _WsTransport)) and not transport.is_connected():
            transport.connect()

    def connect(self) -> "McpClient":
        """显式建立传输连接（SSE/WS 有握手；stdio/HTTP 为幂等空操作）。"""
        if isinstance(self._transport, (_SseTransport, _WsTransport)):
            self._transport.connect()
        return self

    def close(self) -> None:
        try:
            self._transport.close()
        except Exception:  # noqa: BLE001
            pass

    def _rebuild_transport(self) -> None:
        """有界重连第一步：重建传输。原请求由 _exchange 重发（幂等语义）。"""
        if self._factory is None:
            raise _TransportDead("no transport factory wired; cannot reconnect")
        try:
            self._transport.close()
        except Exception:  # noqa: BLE001
            pass
        self._transport = self._factory()
        if isinstance(self._transport, (_SseTransport, _WsTransport)):
            self._transport.connect()

    # -- 双向交互 --------------------------------------------------------------- #

    def on_elicitation(self, handler: Callable[[dict], dict]) -> None:
        """注册 elicitation 应答器：``handler(params) -> {"action", "content"}``。

        未注册时客户端对 ``elicitation/create`` 诚实默认 ``decline``。
        """
        self._elicit_handler = handler

    def on_tools_changed(self, listener: Callable[[], None]) -> None:
        self._tools_changed_listeners.append(listener)

    def on_notification(self, listener: Callable[[dict], None]) -> None:
        self._notification_listeners.append(listener)

    def _handle_server_request(self, msg: dict) -> dict | None:
        """服务端→客户端请求（目前 elicitation/create）。"""
        method = msg.get("method", "")
        if method == "elicitation/create":
            params = msg.get("params") or {}
            if self._elicit_handler is None:
                result: dict = {"action": "decline"}
            else:
                try:
                    result = self._elicit_handler(params)
                    if not isinstance(result, dict):
                        result = {"action": "decline"}
                except Exception:  # noqa: BLE001 — 应答器故障按 decline 处理
                    result = {"action": "decline"}
            return {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}
        return {"jsonrpc": "2.0", "id": msg.get("id"),
                "error": {"code": ERR_METHOD_NOT_FOUND, "message": f"Unknown method: {method}"}}

    def _handle_notification(self, payload: dict) -> None:
        method = payload.get("method", "")
        if method == "notifications/tools/list_changed":
            for fn in list(self._tools_changed_listeners):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
        for fn in list(self._notification_listeners):
            try:
                fn(payload)
            except Exception:  # noqa: BLE001
                pass

    def _exchange(self, msg: dict) -> dict | None:
        """发送一帧并等到匹配 id 的响应；沿途处理通知与服务端请求。

        传输断开时按 :class:`ReconnectPolicy` 有界重连：重建传输后**重发原请求**
        （initialize 幂等重握手；其余请求在服务端语义允许范围内重试）。
        """
        attempts = 0
        policy = self._reconnect
        while True:
            try:
                self._ensure_connected()
                self._transport.send_line(json.dumps(msg))
                if str(msg.get("method", "")).startswith("notifications/"):
                    return None
                return self._await_response(msg.get("id"))
            except _TransportDead:
                if policy is None or attempts >= policy.max_attempts:
                    raise McpError(
                        ERR_INTERNAL,
                        "MCP transport dead; reconnect exhausted"
                        if policy is not None else "MCP transport dead",
                    ) from None
                delay = policy.backoff_seconds * (policy.backoff_factor ** attempts)
                attempts += 1
                if delay > 0:
                    policy.sleep(delay)
                self._rebuild_transport()

    def _await_response(self, req_id: Any) -> dict:
        while True:
            line = self._transport.recv_line(self._recv_timeout)
            if line is None:
                raise _TransportDead("transport returned EOF")
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue  # 非 JSON 帧忽略（不假装是响应）
            if not isinstance(payload, dict):
                continue
            if "method" in payload and "result" not in payload and "error" not in payload:
                if "id" in payload:
                    resp = self._handle_server_request(payload)
                    if resp is not None:
                        self._transport.send_line(json.dumps(resp))
                else:
                    self._handle_notification(payload)
                continue
            if payload.get("id") == req_id or str(payload.get("id")) == str(req_id):
                return payload
            # 其它 id 的迟到响应：丢弃（上一轮超时残留）

    # -- 协议方法 --------------------------------------------------------------- #

    def initialize(self, client_name: str = "fy-mcp-client", client_version: str = "0.2.0") -> dict:
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"elicitation": {}},
                "clientInfo": {"name": client_name, "version": client_version},
            },
        }
        resp = self._exchange(req)
        if not resp:
            raise McpError(ERR_INTERNAL, "Empty response from initialize")
        if "error" in resp:
            err = resp["error"]
            raise McpError(err["code"], err["message"], err.get("data"))
        res = resp.get("result", {})
        self.protocol_version = res.get("protocolVersion", "")
        self.server_info = res.get("serverInfo", {})
        self.capabilities = res.get("capabilities", {})
        self._initialized = True
        # Send initialized notification
        self._exchange({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def _require_ok(self, resp: dict | None, *, what: str) -> dict:
        if not resp:
            raise McpError(ERR_INTERNAL, f"Empty response from {what}")
        if "error" in resp:
            err = resp["error"]
            raise McpError(err["code"], err["message"], err.get("data"))
        return resp.get("result", {})

    # -- tools ------------------------------------------------------------------ #

    def list_tools(self) -> list[dict]:
        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        result = self._require_ok(self._exchange(req), what="tools/list")
        tools = result.get("tools", [])
        if isinstance(tools, list):
            self._tool_catalog = {
                t["name"]: t for t in tools
                if isinstance(t, dict) and isinstance(t.get("name"), str)
            }
        return tools

    def refresh_tools(self) -> list[dict]:
        """动态刷新（A-统一接入-09）：收到 list_changed 或任意时刻重新发现。"""
        return self.list_tools()

    def supports_tool_list_changed(self) -> bool:
        caps = (self.capabilities or {}).get("tools") or {}
        return bool(caps.get("listChanged"))

    def call_tool(self, name: str, arguments: dict, request_id: int | str = 3) -> Any:
        # 信任分级门禁（A-统一接入-09）：untrusted 全拒、remote 需确认、
        # remote/untrusted 禁 inline shell（硬规则）。
        if self._trust is not None:
            meta = self._tool_catalog.get(str(name))
            self._trust.gate_tool_call(str(name), meta)
        req = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        resp = self._exchange(req)
        if not resp:
            raise McpError(ERR_INTERNAL, "Empty response from tools/call")
        if "error" in resp:
            err = resp["error"]
            raise McpError(err["code"], err["message"], err.get("data"))
        content = resp.get("result", {}).get("content", [])
        if content and isinstance(content, list) and "text" in content[0]:
            try:
                return json.loads(content[0]["text"])
            except Exception:
                return content[0]["text"]
        return resp.get("result", {})

    # -- resources / prompts（A-统一接入-02 发现补全） ---------------------------- #

    def list_resources(self) -> list[dict]:
        result = self._require_ok(
            self._exchange({"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}}),
            what="resources/list")
        return result.get("resources", [])

    def read_resource(self, uri: str, request_id: int | str = 5) -> dict:
        result = self._require_ok(
            self._exchange({"jsonrpc": "2.0", "id": request_id,
                            "method": "resources/read",
                            "params": {"uri": uri}}),
            what="resources/read")
        return result

    def list_prompts(self) -> list[dict]:
        result = self._require_ok(
            self._exchange({"jsonrpc": "2.0", "id": 6, "method": "prompts/list", "params": {}}),
            what="prompts/list")
        return result.get("prompts", [])

    def get_prompt(self, name: str, arguments: dict | None = None,
                   request_id: int | str = 7) -> dict:
        result = self._require_ok(
            self._exchange({"jsonrpc": "2.0", "id": request_id,
                            "method": "prompts/get",
                            "params": {"name": name, "arguments": arguments or {}}}),
            what="prompts/get")
        return result

    # -- 取消 --------------------------------------------------------------------- #

    def cancel(self, request_id: int | str) -> None:
        req = {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": str(request_id)},
        }
        self._exchange(req)


# --- tool-registry assembly (P2 MCP ecosystem integration · §八 B1 单一装配入口) --

logger = logging.getLogger(__name__)


def _assemble_client_tools(
    *,
    key: str,
    client: "McpClient",
    registry: Any,
    prefix: str,
    status: dict[str, Any],
) -> None:
    """把一个已连接 MCP 客户端的工具清单装配进 tool registry（B1 唯一实现体）。

    ``prefix`` 决定命名空间：``""`` → ``<server>.<tool>``（FY_MCP_SERVERS 启动
    装配）；``"hub."`` → ``hub.<server>.<tool>``（hub 连接装配）。
    """
    from ..services.errors import NotFound
    from ..services.tool_registry import TOOL_NAME_RE

    try:
        client.initialize()
        tools = client.list_tools()
    except Exception as exc:  # noqa: BLE001 — one flaky server must not block boot
        status["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("MCP server %r unreachable, skipping: %s", key, status["error"])
        try:
            client.close()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass
        return

    registry.attach_mcp_client(key, client)
    if not isinstance(tools, list):
        status["error"] = "tools/list returned a non-list payload"
        logger.warning("MCP server %r returned a malformed tool list, skipping", key)
        return

    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"]:
            status["skipped"].append({"tool": None, "reason": "malformed tool entry"})
            continue
        remote = tool["name"]
        name = f"{prefix}{key}.{remote}"
        if not TOOL_NAME_RE.fullmatch(name):
            reason = f"prefixed name '{name}' does not match {TOOL_NAME_RE.pattern}"
            status["skipped"].append({"tool": name, "reason": reason})
            logger.warning("MCP server %r tool skipped: %s", key, reason)
            continue
        entry = {"type": "mcp", "server": key, "remote_tool": remote}
        try:
            existing = registry.get_tool(name)
        except NotFound:
            existing = None
        if existing is not None and existing.get("entry") != entry:
            reason = f"name collision: '{name}' already registered with a different entry"
            status["skipped"].append({"tool": name, "reason": reason})
            logger.warning("MCP server %r tool skipped: %s", key, reason)
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object"}
        description = tool.get("description") or f"Remote MCP tool '{remote}' on server '{key}'"
        try:
            registry.register(
                name=name, description=description, parameters=schema, entry=entry,
            )
        except Exception as exc:  # noqa: BLE001 — skip the bad tool, keep the server
            status["skipped"].append({"tool": name, "reason": str(exc)})
            logger.warning(
                "MCP server %r tool %r failed registration, skipping: %s", key, remote, exc,
            )
            continue
        status["registered"].append(name)

    status["ok"] = True
    logger.info(
        "MCP server %r assembled: %d tool(s) registered, %d skipped",
        key, len(status["registered"]), len(status["skipped"]),
    )


def assemble_mcp_tools(
    *,
    registry: Any | None = None,
    settings: Any | None = None,
    clients: dict[str, "McpClient"] | None = None,
    prefix: str = "",
) -> list[dict[str, Any]]:
    """Wire configured MCP servers into the dynamic tool registry (P1-05).

    **唯一装配入口**（§八 B1）：hub 的 ``McpServerAdapter.register_tools`` 只是
    本函数 ``prefix="hub."`` 的委托调用。``prefix`` 控制命名空间：

    * ``""``（默认）→ ``<server_key>.<tool_name>``（FY_MCP_SERVERS 启动装配）
    * ``"hub."``    → ``hub.<server_key>.<tool_name>``（hub 连接装配）

    Server sources: ``settings.mcp_servers`` (``FY_MCP_SERVERS`` JSON, e.g.
    ``{"demo": {"command": ["python", "-m", "find_yourself.adapters.mcp"],
    "env": {}}}``) plus any pre-built clients passed via ``clients`` (in-process
    hosting / tests; a key present in both wins the injected client).

    Failure semantics — honest, never silently fake success:

    * an unreachable / uninitializable server is logged at WARNING and skipped;
    * a misconfigured server (no valid ``command``) is logged and skipped;
    * a remote tool whose prefixed name / metadata cannot be registered is
      logged and skipped;
    * a prefixed name that would overwrite a *different* existing registration
      is logged and skipped (idempotent re-assembly of the same entry is fine).

    Returns a per-server status report so callers keep evidence of what was
    actually registered.
    """
    if registry is None:
        from ..services.tool_registry import tool_registry as registry
    statuses: list[dict[str, Any]] = []
    configured: dict[str, dict[str, Any]] = {}
    if settings is not None:
        raw = getattr(settings, "mcp_servers", None) or {}
        if isinstance(raw, dict):
            configured.update(raw)
    injected = clients or {}

    for key in sorted(set(configured) | set(injected)):
        cfg = configured.get(key) or {}
        status: dict[str, Any] = {
            "server": key,
            "ok": False,
            "error": None,
            "mode": "injected" if key in injected else "subprocess",
            "prefix": prefix,
            "registered": [],
            "skipped": [],
        }
        statuses.append(status)

        client = injected.get(key)
        if client is None:
            cmd = cfg.get("command")
            if not (isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd)):
                status["error"] = "invalid config: 'command' must be a non-empty list of strings"
                logger.warning("MCP server %r misconfigured, skipping: %s", key, status["error"])
                continue
            env = cfg.get("env") if isinstance(cfg.get("env"), dict) else None
            client = McpClient.from_subprocess(cmd, env=env)

        _assemble_client_tools(key=key, client=client, registry=registry,
                               prefix=prefix, status=status)
    return statuses


def main():
    """Stdio entrypoint when running `python -m find_yourself.adapters.mcp`."""
    import hashlib
    import os
    import sys

    tools = [
        McpTool(name="ping", description="Ping health check", handler=lambda a: {"pong": True}),
        McpTool(
            name="sha256",
            description="Compute sha256 hash of text",
            handler=lambda a: {"hash": hashlib.sha256(a.get("text", "").encode()).hexdigest()},
        ),
        McpTool(
            name="system_audit",
            description="Privileged system audit tool",
            privileged=True,
            handler=lambda a: {"audit": "system_secure", "metrics": 100},
        ),
    ]

    allowed_raw = os.environ.get("FY_MCP_ALLOWED_PRIVILEGED", "")
    allowed = set(filter(None, [x.strip() for x in allowed_raw.split(",")]))
    server = McpStdioServer(tools=tools, allowed_privileged=allowed)

    for line in sys.stdin:
        resp = server.handle_line(line)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
