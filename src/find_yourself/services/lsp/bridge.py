"""LSP JSON-RPC 协议桥（A-代码智能-01 自研主体 1/2）。

LSP = JSON-RPC 2.0 over stdio，``Content-Length`` 分帧。本模块实现：

* :func:`encode_message` / :func:`decode_messages` —— 分帧编解码（纯函数，
  处理粘包/半包，可独立测试）；
* :class:`MemoryConnection` —— 传输抽象：字节读写回调注入，测试用内存流，
  生产用子进程 stdin/stdout（见 manager.py）；
* :class:`LSPConnection` —— 锁步式 JSON-RPC 客户端：request（带 id 关联）、
  notify、通知收集（publishDiagnostics 等）、initialize 握手。

设计取舍（诚实边界）：**锁步**而非后台读线程+future——LSP 客户端场景里
我们只关心「我发的请求」的响应，服务器主动推送的诊断在下次读取时顺带
入队即可；锁步天然线程安全，规模小可全测。
"""

from __future__ import annotations

import itertools
import json
import uuid
from typing import Any, Callable

#: 一次 read 回调最多拉取的字节数（防止无界内存）
_READ_CHUNK = 65536


def encode_message(payload: dict[str, Any]) -> bytes:
    """JSON-RPC 报文 → LSP 分帧（ASCII 头 + JSON 体）。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def decode_messages(buffer: bytes) -> tuple[list[dict[str, Any]], bytes]:
    """从字节缓冲解出完整报文列表，返回 (报文列表, 剩余半包字节)。

    头部残缺/长度字段缺失 → 视为半包原样退回（不抛错——流式读取的正常态）。
    """
    messages: list[dict[str, Any]] = []
    rest = buffer
    while True:
        header_end = rest.find(b"\r\n\r\n")
        if header_end == -1:
            return messages, rest
        header_blob = rest[:header_end].decode("ascii", errors="replace")
        length: int | None = None
        for line in header_blob.split("\r\n"):
            name, _, value = line.partition(":")
            if name.strip().lower() == "content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    length = None
        if length is None:
            # 无 Content-Length 的坏帧：丢弃到下一个分隔符（防卡死）
            rest = rest[header_end + 4:]
            continue
        body_start = header_end + 4
        if len(rest) < body_start + length:
            return messages, rest                    # 半包：等下一次读取
        body = rest[body_start:body_start + length]
        try:
            messages.append(json.loads(body.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            pass                                     # 坏体：跳过，不毒化整流
        rest = rest[body_start + length:]


class MemoryConnection:
    """测试用传输：写进内存缓冲，读回调从预置字节流取数。"""

    def __init__(self, script: bytes = b""):
        self.written = b""
        self._script = script

    def write(self, data: bytes) -> None:
        self.written += data

    def read(self, n: int = _READ_CHUNK) -> bytes:
        out, self._script = self._script[:n], self._script[n:]
        return out


class LSPConnection:
    """锁步式 LSP JSON-RPC 客户端（传输可注入）。"""

    def __init__(
        self,
        *,
        write_fn: Callable[[bytes], None],
        read_fn: Callable[[int], bytes],
    ):
        self._write_fn = write_fn
        self._read_fn = read_fn
        self._buffer = b""
        self.notifications: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    # -- 底层 ---------------------------------------------------------------

    def _send(self, payload: dict[str, Any]) -> None:
        self._write_fn(encode_message(payload))

    def _pump(self) -> list[dict[str, Any]]:
        """读尽当前可读字节并解帧。返回其中的**响应**（带 id+result/error）；
        其余（服务器主动通知/请求）归入通知队列。"""
        while True:
            chunk = self._read_fn(_READ_CHUNK)
            if not chunk:
                break
            self._buffer += chunk
        messages, self._buffer = decode_messages(self._buffer)
        responses: list[dict[str, Any]] = []
        for m in messages:
            if "id" in m and "method" not in m and ("result" in m or "error" in m):
                responses.append(m)
            else:
                self.notifications.append(m)
        return responses

    def _wait_response(self, msg_id: Any, *, max_rounds: int = 8) -> dict[str, Any]:
        """锁步等待指定 id 的响应（每轮先读尽再匹配；读空即止——诚实超时）。"""
        for _ in range(max_rounds):
            for m in self._pump():
                if m.get("id") == msg_id:
                    return m
            if not self._read_fn(_READ_CHUNK):
                # 拉空仍无响应：最后一轮解帧兜底后判超时
                for m in self._pump():
                    if m.get("id") == msg_id:
                        return m
                break
        raise TimeoutError(f"LSP response for id={msg_id!r} not received")

    # -- 协议 ---------------------------------------------------------------

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """发请求并锁步等响应；error 响应抛 LSPError。"""
        msg_id = next(self._ids)
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method,
                    "params": params or {}})
        resp = self._wait_response(msg_id)
        if "error" in resp:
            err = resp["error"]
            raise LSPError(err.get("code", -32603), str(err.get("message", "LSP error")))
        return resp.get("result") or {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self, *, root_uri: str) -> dict[str, Any]:
        """LSP 握手：initialize 请求 → initialized 通知。"""
        result = self.request("initialize", {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "documentSymbol": {"dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": False},
                },
            },
        })
        self.notify("initialized", {})
        return result

    def did_open(self, *, path: str, language_id: str, text: str, version: int = 1) -> None:
        self.notify("textDocument/didOpen", {
            "textDocument": {"uri": path, "languageId": language_id,
                             "version": version, "text": text},
        })

    def drain_diagnostics(self) -> list[dict[str, Any]]:
        """取回累积的 publishDiagnostics（取后清空）。"""
        diags = [n for n in self.notifications
                 if n.get("method") == "textDocument/publishDiagnostics"]
        self.notifications = [n for n in self.notifications
                              if n.get("method") != "textDocument/publishDiagnostics"]
        return diags


class LSPError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]
