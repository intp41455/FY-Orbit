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


# ============================================================================
# Outbound A2A Client & Server-side Trusted Endpoint Registry (F4)
# ============================================================================

from dataclasses import dataclass, field
import httpx
import secrets


class A2AClientError(Exception):
    """Base exception for outbound A2A client operations."""


class UntrustedEndpointError(A2AClientError):
    """Raised when an outbound A2A call targets an endpoint not in the trusted registry."""


class A2ARpcError(A2AClientError):
    """Raised when a remote agent returns a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class A2ATimeoutError(A2AClientError):
    """Raised when an outbound A2A call times out."""


@dataclass(frozen=True)
class TrustedEndpointConfig:
    endpoint_key: str
    base_url: str
    expected_name: str | None = None
    auth_token: str | None = None


class TrustedEndpointRegistry:
    """Server-side registry of pre-approved outbound Agent endpoints.

    Prevents models or callers from directing requests to arbitrary or internal URLs.
    """

    def __init__(self, endpoints: list[TrustedEndpointConfig] | None = None):
        self._endpoints: dict[str, TrustedEndpointConfig] = {}
        for ep in endpoints or []:
            self.register(ep.endpoint_key, ep.base_url, ep.expected_name, ep.auth_token)

    def register(self, endpoint_key: str, base_url: str,
                 expected_name: str | None = None,
                 auth_token: str | None = None) -> TrustedEndpointConfig:
        config = TrustedEndpointConfig(
            endpoint_key=endpoint_key,
            base_url=base_url.rstrip("/"),
            expected_name=expected_name,
            auth_token=auth_token,
        )
        self._endpoints[endpoint_key] = config
        return config

    def get(self, endpoint_key: str) -> TrustedEndpointConfig | None:
        return self._endpoints.get(endpoint_key)

    def validate_target(self, endpoint_key_or_url: str) -> TrustedEndpointConfig:
        clean = endpoint_key_or_url.rstrip("/")
        # 1. Match by endpoint_key
        if clean in self._endpoints:
            return self._endpoints[clean]
        # 2. Match by exact base_url
        for ep in self._endpoints.values():
            if ep.base_url == clean:
                return ep
        raise UntrustedEndpointError(
            f"Endpoint '{endpoint_key_or_url}' is not in the server-side trusted endpoint registry"
        )


class A2AClient:
    """Outbound A2A client discovering cards and submitting tasks to trusted external agents."""

    def __init__(self, registry: TrustedEndpointRegistry | None = None,
                 http_client: httpx.Client | None = None,
                 app: Any = None,
                 default_base_url: str = "http://agent.local"):
        self.registry = registry or TrustedEndpointRegistry()
        if http_client is not None:
            self._client = http_client
        elif app is not None:
            from starlette.testclient import TestClient
            self._client = TestClient(app, base_url=default_base_url)
            setattr(self._client, "_is_test_client", True)
        else:
            self._client = httpx.Client(timeout=10.0)

    def _post(self, url: str, payload: dict, headers: dict, timeout: float | None):
        kwargs: dict[str, Any] = {"json": payload, "headers": headers}
        if not getattr(self._client, "_is_test_client", False) and timeout is not None:
            kwargs["timeout"] = timeout
        return self._client.post(url, **kwargs)

    def get_agent_card(self, endpoint_key_or_url: str) -> dict:
        """Fetch and validate the Agent Card from /.well-known/agent.json."""
        config = self.registry.validate_target(endpoint_key_or_url)
        url = f"{config.base_url}{AGENT_CARD_PATH}"
        try:
            resp = self._client.get(url)
        except httpx.TimeoutException as e:
            raise A2ATimeoutError(f"Timeout fetching agent card from {url}") from e
        except Exception as e:
            raise A2AClientError(f"Network error fetching agent card: {e}") from e

        if resp.status_code != 200:
            raise A2AClientError(f"Failed to fetch agent card (HTTP {resp.status_code}): {resp.text}")

        card = resp.json()
        required_fields = ("name", "version", "protocolVersion", "capabilities", "skills")
        for f in required_fields:
            if f not in card:
                raise A2AClientError(f"Invalid Agent Card: missing required field '{f}'")

        if config.expected_name and card.get("name") != config.expected_name:
            raise A2AClientError(
                f"Agent Card name mismatch: expected '{config.expected_name}', got '{card.get('name')}'"
            )
        return card

    def send_message(self, endpoint_key_or_url: str, message: dict, *,
                     task_id: str | None = None,
                     auth_token: str | None = None,
                     timeout: float = 10.0) -> dict:
        """Submit a task to the remote agent using JSON-RPC message/send."""
        config = self.registry.validate_target(endpoint_key_or_url)
        url = f"{config.base_url}{JSONRPC_PATH}"
        token = auth_token or config.auth_token

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req_id = secrets.token_hex(8)
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "message/send",
            "params": {"message": message, "taskId": task_id},
        }

        try:
            resp = self._post(url, payload, headers, timeout)
        except httpx.TimeoutException as e:
            raise A2ATimeoutError(f"Timeout submitting task to {url}") from e
        except Exception as e:
            raise A2AClientError(f"Network error calling {url}: {e}") from e

        if resp.status_code != 200:
            raise A2AClientError(f"A2A call failed with HTTP {resp.status_code}: {resp.text}")

        body = resp.json()
        if "error" in body:
            err = body["error"]
            raise A2ARpcError(err.get("code", -32000), err.get("message", "RPC Error"), err.get("data"))
        return body.get("result", {})

    def get_task(self, endpoint_key_or_url: str, task_id: str, *,
                 auth_token: str | None = None,
                 timeout: float = 10.0) -> dict:
        """Poll task status using tasks/get."""
        config = self.registry.validate_target(endpoint_key_or_url)
        url = f"{config.base_url}{JSONRPC_PATH}"
        token = auth_token or config.auth_token

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req_id = secrets.token_hex(8)
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tasks/get",
            "params": {"id": task_id},
        }

        try:
            resp = self._post(url, payload, headers, timeout)
        except httpx.TimeoutException as e:
            raise A2ATimeoutError(f"Timeout polling task {task_id} from {url}") from e
        except Exception as e:
            raise A2AClientError(f"Network error polling task {task_id}: {e}") from e

        if resp.status_code != 200:
            raise A2AClientError(f"A2A tasks/get failed with HTTP {resp.status_code}: {resp.text}")

        body = resp.json()
        if "error" in body:
            err = body["error"]
            raise A2ARpcError(err.get("code", -32000), err.get("message", "RPC Error"), err.get("data"))
        return body.get("result", {})

    def cancel_task(self, endpoint_key_or_url: str, task_id: str, *,
                    auth_token: str | None = None,
                    timeout: float = 10.0) -> dict:
        """Cancel an in-flight task using tasks/cancel."""
        config = self.registry.validate_target(endpoint_key_or_url)
        url = f"{config.base_url}{JSONRPC_PATH}"
        token = auth_token or config.auth_token

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req_id = secrets.token_hex(8)
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tasks/cancel",
            "params": {"id": task_id},
        }

        try:
            resp = self._post(url, payload, headers, timeout)
        except httpx.TimeoutException as e:
            raise A2ATimeoutError(f"Timeout cancelling task {task_id} on {url}") from e
        except Exception as e:
            raise A2AClientError(f"Network error cancelling task {task_id}: {e}") from e

        if resp.status_code != 200:
            raise A2AClientError(f"A2A tasks/cancel failed with HTTP {resp.status_code}: {resp.text}")

        body = resp.json()
        if "error" in body:
            err = body["error"]
            raise A2ARpcError(err.get("code", -32000), err.get("message", "RPC Error"), err.get("data"))
        return body.get("result", {})
