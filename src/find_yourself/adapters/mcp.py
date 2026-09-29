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
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
