"""Minimal A2A (Agent2Agent) compatibility layer (FROZEN_CONTRACT G7/A01).

Reference: official A2A protocol publishes an **Agent Card** at
``/.well-known/agent.json`` and speaks JSON-RPC 2.0 over an endpoint
(``/a2a/v1/jsonrpc``). Methods: ``message/send``, ``message/stream``,
``tasks/get``, ``tasks/cancel``, ``tasks/list``. Errors are JSON-RPC error
objects ``{code, message, data}``.

This is a *compatibility* surface only: it advertises a correct card and
handles the JSON-RPC envelope, but it never invokes a remote agent. When no
upstream endpoint/credential is configured it returns a structured
``-32001 upstream_not_configured`` error instead of a fabricated task result
(A12). All inputs are synthetic; no private content or secrets are echoed.
"""

from __future__ import annotations

import time
from typing import Any

PROTOCOL_VERSION = "0.3.0"
AGENT_CARD_PATH = "/.well-known/agent.json"
JSONRPC_PATH = "/a2a/v1/jsonrpc"

# JSON-RPC 2.0 reserved error codes.
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
# Application-defined.
ERR_UPSTREAM_NOT_CONFIGURED = -32001
ERR_AGENT_DRAINING = -32002
ERR_UNAUTHORIZED = -32003


def build_agent_card(*, public_url: str, agent_name: str, version: str,
                      skills: list[dict], description: str) -> dict:
    """Build a spec-shaped public Agent Card (no secrets)."""
    return {
        "name": agent_name,
        "description": description,
        "version": version,
        "url": public_url.rstrip("/") + JSONRPC_PATH,
        "protocolVersion": PROTOCOL_VERSION,
        "preferredTransport": "JSONRPC",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": skills,
        "provider": {"organization": "Find Yourself", "url": public_url},
        "securitySchemes": {
            "fy_session": {
                "type": "apiKey",
                "in": "cookie",
                "name": "fy_session",
                "description": "Owner OIDC session; service identities use Bearer.",
            }
        },
        "supportsAuthenticatedExtendedCard": True,
    }


class A2ADispatcher:
    """JSON-RPC 2.0 dispatcher for the local A2A surface.

    ``upstream_configured`` decides whether ``message/send`` can actually target
    a remote agent. The local agent itself answers ``tasks/get``/``tasks/cancel``
    against in-process task state; it never fabricates a remote result.
    """

    def __init__(self, *, upstream_configured: bool, draining: bool = False,
                 task_lookup=None, cancel_task=None):
        self.upstream_configured = upstream_configured
        self.draining = draining
        self._task_lookup = task_lookup or (lambda task_id: None)
        self._cancel_task = cancel_task or (lambda task_id: False)

    def dispatch(self, body: Any) -> dict:
        if not isinstance(body, dict):
            return _error(None, ERR_INVALID_REQUEST, "Request must be a JSON object")
        req_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}
        if not isinstance(method, str):
            return _error(req_id, ERR_INVALID_REQUEST, "Missing method")

        try:
            if method == "message/send":
                return self._message_send(req_id, params)
            if method == "tasks/get":
                return self._tasks_get(req_id, params)
            if method == "tasks/cancel":
                return self._tasks_cancel(req_id, params)
            if method == "tasks/list":
                return _ok(req_id, {"tasks": []})
            if method == "message/stream":
                return _error(req_id, ERR_UPSTREAM_NOT_CONFIGURED,
                              "Streaming not supported by this local surface")
            return _error(req_id, ERR_METHOD_NOT_FOUND, f"Unknown method: {method}")
        except _RpcError as e:
            return _error(req_id, e.code, e.message, e.data)
        except Exception as e:  # never leak stack
            return _error(req_id, ERR_INTERNAL, "Internal error", {"type": type(e).__name__})

    def _message_send(self, req_id, params: dict) -> dict:
        message = params.get("message") or {}
        if not message.get("role") or not message.get("parts"):
            raise _RpcError(ERR_INVALID_PARAMS, "message needs role and parts")
        if self.draining:
            raise _RpcError(ERR_AGENT_DRAINING, "Agent is draining; no new tasks accepted")
        if not self.upstream_configured:
            raise _RpcError(ERR_UPSTREAM_NOT_CONFIGURED,
                            "No upstream A2A endpoint/credential configured; "
                            "refusing to fabricate a task result")
        # If wired, this would return a real Task object. Here we never reach out.
        raise _RpcError(ERR_UPSTREAM_NOT_CONFIGURED, "Upstream not wired (BLOCKED_EXTERNAL)")

    def _tasks_get(self, req_id, params: dict) -> dict:
        task_id = params.get("id") or params.get("taskId")
        if not task_id:
            raise _RpcError(ERR_INVALID_PARAMS, "task id required")
        task = self._task_lookup(task_id)
        if task is None:
            raise _RpcError(-32004, "Task not found", {"id": task_id})
        return _ok(req_id, task)

    def _tasks_cancel(self, req_id, params: dict) -> dict:
        task_id = params.get("id") or params.get("taskId")
        if not task_id:
            raise _RpcError(ERR_INVALID_PARAMS, "task id required")
        ok = self._cancel_task(task_id)
        if not ok:
            raise _RpcError(-32004, "Task not found or not cancellable", {"id": task_id})
        return _ok(req_id, {"id": task_id, "status": {"state": "canceled"}})


class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _ok(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str, data: Any = None) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    if data is not None:
        body["error"]["data"] = data
    return body
