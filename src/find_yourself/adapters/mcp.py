"""Minimal MCP (Model Context Protocol) JSON-RPC/stdio adapter (G7/A02).

Implemented on the Python standard library only — no MCP SDK dependency.
Speaks newline-delimited JSON-RPC 2.0 over a byte stream (stdio).

Supported methods:
* ``initialize`` -> protocol version + server capabilities
* ``initialized`` notification (no response)
* ``tools/list`` -> server-injected tool catalogue
* ``tools/call`` -> executes a tool through a permission gate the server injects;
  a tool the caller may not use returns a JSON-RPC error, never a silent downgrade
* ``notifications/cancelled`` -> cancellation acknowledgement
* error/parse paths for malformed frames

Permissions are *server-side injected*: the caller cannot widen them. A tool
declared ``privileged=True`` requires the caller's injected scope to include it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_PERMISSION_DENIED = -32003
ERR_TOOL_NOT_FOUND = -32004
ERR_TOOL_EXECUTION = -32005
ERR_CANCELLED = -32000

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "find-yourself-mcp"
SERVER_VERSION = "0.1.0"


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    privileged: bool = False
    handler: Callable[[dict], Any] = field(default=None, repr=False)


class McpStdioServer:
    """Line-delimited JSON-RPC MCP server core, testable without a real process."""

    def __init__(self, tools: list[McpTool], allowed_privileged: set[str] | None = None):
        self._tools = {t.name: t for t in tools}
        # Scopes the SERVER injects for the caller; never caller-supplied.
        self._allowed_privileged = set(allowed_privileged or [])
        self.initialized = False
        self.cancelled: set[str] = set()

    # -- framing -------------------------------------------------------------
    def handle_line(self, line: str) -> dict | None:
        """Process one JSON-RPC line; return a response dict, or None (notification)."""
        line = line.strip()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except (ValueError, TypeError):
            return {"jsonrpc": "2.0", "id": None,
                    "error": {"code": ERR_PARSE, "message": "Parse error"}}
        return self.dispatch(msg)

    def dispatch(self, msg: dict) -> dict | None:
        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                self.initialized = True
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
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
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": ERR_METHOD_NOT_FOUND, "message": f"Unknown method: {method}"}}
        except _McpError as e:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": e.code, "message": e.message, "data": e.data}}
        except Exception as e:  # never leak stack
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": ERR_INTERNAL, "message": "Internal error",
                              "data": {"type": type(e).__name__}}}

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
            result = tool.handler(arguments)
        except _McpError:
            raise
        except Exception as e:
            raise _McpError(ERR_TOOL_EXECUTION, f"Tool failed: {type(e).__name__}")
        return {"jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result)}]}}


class _McpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


McpError = _McpError


class McpClient:
    """Stdio and in-process client for Model Context Protocol (MCP) servers."""

    def __init__(self, server: McpStdioServer | None = None,
                 proc: Any | None = None):
        self._server = server
        self._proc = proc
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.protocol_version: str = ""

    @classmethod
    def from_server(cls, server: McpStdioServer) -> "McpClient":
        return cls(server=server)

    @classmethod
    def from_subprocess(cls, cmd: list[str], env: dict | None = None) -> "McpClient":
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
        return cls(proc=proc)

    def close(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def _exchange(self, msg: dict) -> dict | None:
        line = json.dumps(msg) + "\n"
        if self._server is not None:
            return self._server.handle_line(line.strip())
        if self._proc is not None and self._proc.stdin is not None and self._proc.stdout is not None:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
            if msg.get("method", "").startswith("notifications/"):
                return None
            out_line = self._proc.stdout.readline()
            if not out_line:
                raise _McpError(ERR_INTERNAL, "MCP subprocess closed pipe prematurely")
            return json.loads(out_line.strip())
        raise _McpError(ERR_INTERNAL, "No server or process wired to McpClient")

    def initialize(self, client_name: str = "fy-mcp-client", client_version: str = "0.1.0") -> dict:
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": client_version},
            },
        }
        resp = self._exchange(req)
        if not resp:
            raise _McpError(ERR_INTERNAL, "Empty response from initialize")
        if "error" in resp:
            err = resp["error"]
            raise _McpError(err["code"], err["message"], err.get("data"))
        res = resp.get("result", {})
        self.protocol_version = res.get("protocolVersion", "")
        self.server_info = res.get("serverInfo", {})
        self.capabilities = res.get("capabilities", {})
        # Send initialized notification
        self._exchange({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def list_tools(self) -> list[dict]:
        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = self._exchange(req)
        if not resp:
            raise _McpError(ERR_INTERNAL, "Empty response from tools/list")
        if "error" in resp:
            err = resp["error"]
            raise _McpError(err["code"], err["message"], err.get("data"))
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict, request_id: int | str = 3) -> Any:
        req = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        resp = self._exchange(req)
        if not resp:
            raise _McpError(ERR_INTERNAL, "Empty response from tools/call")
        if "error" in resp:
            err = resp["error"]
            raise _McpError(err["code"], err["message"], err.get("data"))
        content = resp.get("result", {}).get("content", [])
        if content and isinstance(content, list) and "text" in content[0]:
            try:
                return json.loads(content[0]["text"])
            except Exception:
                return content[0]["text"]
        return resp.get("result", {})

    def cancel(self, request_id: int | str) -> None:
        req = {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": str(request_id)},
        }
        self._exchange(req)


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
