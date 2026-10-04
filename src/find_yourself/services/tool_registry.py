"""Function/Tool Calling 注册与发现中心 (P1-05).

A dynamic tool registry, separate from the static ``skills.harness`` gateway:

* ``register``: log a tool with name / description / parameter JSON Schema /
  execution entry. Entries are either ``builtin`` (an executor shipped with the
  registry, e.g. ``echo`` / ``add``), ``http`` (a callback URL executed by
  POSTing the validated arguments) or ``mcp`` (a remote tool on a configured
  MCP server, executed through ``adapters.mcp.McpClient.call_tool``; the live
  client is attached with ``attach_mcp_client`` and not persisted).
* ``discover``: list every registered tool so a model or agent can find tools.
* ``invoke``: validate arguments against the declared JSON Schema, execute the
  real entry point and return a structured receipt; every call is appended to a
  bounded on-disk call log (P1-05 evidence trail).

Persistence is intentionally memory + JSON file (no migration): metadata
survives process restarts; in-process Python handlers must be re-wired by the
hosting code after a restart (``set_handler``).
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import Conflict, NotFound, ValidationFailed

TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_PERSIST_ENV = "FY_TOOL_REGISTRY_DIR"
_DEFAULT_DIR = ".runtime/tool_registry"
_LOG_CAP = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- builtin executors (real implementations, no simulation) ------------------

def _builtin_echo(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"echo": arguments}


def _builtin_add(arguments: dict[str, Any]) -> dict[str, Any]:
    # The registered schema already enforces numeric a/b; keep a defensive cast.
    return {"sum": arguments["a"] + arguments["b"]}


def _builtin_now(_arguments: dict[str, Any]) -> dict[str, Any]:
    return {"now": _now()}


def _builtin_kb_search(arguments: dict[str, Any]) -> dict[str, Any]:
    """W3：知识库检索执行器（真实调用 ``services.knowledge``）。

    延迟 import 以免与 knowledge 服务形成导入环；执行器内部自己开短生命周期
    session，因此不依赖 HTTP 请求上下文。检索 owner 由服务端决定（见
    ``knowledge.run_tool_search``），**不接受调用方传入的 owner_id**。
    """
    from .knowledge import run_tool_search

    return run_tool_search(arguments)


BUILTIN_EXECUTORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "echo": _builtin_echo,
    "add": _builtin_add,
    "now": _builtin_now,
    # W3 本地知识库：真实走 services.knowledge.search（owner 隔离），不是假检索。
    "kb.search": _builtin_kb_search,
}


# --- minimal JSON Schema (subset) validation ----------------------------------

def _check_type(value: Any, expected: str) -> bool:
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    return True


def validate_arguments(arguments: Any, schema: dict[str, Any]) -> list[str]:
    """Validate ``arguments`` against the declared JSON Schema subset.

    Supports: type, required, properties, enum, items, minLength/maxLength,
    minimum/maximum. Returns a list of human-readable error strings (empty =
    valid). Kept dependency-free on purpose (no jsonschema package).
    """
    errors: list[str] = []

    def walk(value: Any, sch: dict[str, Any], path: str) -> None:
        t = sch.get("type")
        if t and not _check_type(value, t):
            errors.append(f"{path}: expected type '{t}', got '{type(value).__name__}'")
            return
        if "enum" in sch and value not in sch["enum"]:
            errors.append(f"{path}: value {value!r} not in enum {sch['enum']!r}")
        if isinstance(value, dict):
            for req in sch.get("required", []):
                if req not in value:
                    errors.append(f"{path}: missing required property '{req}'")
            for key, sub in sch.get("properties", {}).items():
                if key in value:
                    walk(value[key], sub, f"{path}.{key}")
        elif isinstance(value, list):
            items = sch.get("items")
            if isinstance(items, dict):
                for i, item in enumerate(value):
                    walk(item, items, f"{path}[{i}]")
        if isinstance(value, str):
            if "minLength" in sch and len(value) < sch["minLength"]:
                errors.append(f"{path}: length {len(value)} < minLength {sch['minLength']}")
            if "maxLength" in sch and len(value) > sch["maxLength"]:
                errors.append(f"{path}: length {len(value)} > maxLength {sch['maxLength']}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in sch and value < sch["minimum"]:
                errors.append(f"{path}: value {value} < minimum {sch['minimum']}")
            if "maximum" in sch and value > sch["maximum"]:
                errors.append(f"{path}: value {value} > maximum {sch['maximum']}")

    walk(arguments, schema, "arguments")
    return errors


# --- registry service ----------------------------------------------------------

class ToolRegistryService:
    """In-memory tool registry with JSON-file persistence and a call log."""

    def __init__(self, persist_dir: str | os.PathLike | None = None):
        raw = persist_dir or os.environ.get(_PERSIST_ENV) or _DEFAULT_DIR
        self._dir = Path(raw)
        self._tools_file = self._dir / "tools.json"
        self._log_file = self._dir / "call_log.json"
        self._lock = threading.Lock()
        self._tools: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._mcp_clients: dict[str, Any] = {}
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self._tools_file.is_file():
            return
        try:
            data = json.loads(self._tools_file.read_text(encoding="utf-8"))
            tools = data.get("tools", {})
            if isinstance(tools, dict):
                self._tools = tools
        except (json.JSONDecodeError, OSError):
            # Corrupt file: start empty rather than crash the API on boot.
            self._tools = {}

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {"updated_at": _now(), "tools": self._tools}
        tmp = self._tools_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._tools_file)

    def _append_log(self, record: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        log: list[dict[str, Any]] = []
        if self._log_file.is_file():
            try:
                loaded = json.loads(self._log_file.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    log = loaded
            except (json.JSONDecodeError, OSError):
                log = []
        log.append(record)
        log = log[-_LOG_CAP:]
        tmp = self._log_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._log_file)

    # -- registry operations ----------------------------------------------------

    def register(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        """Register (or idempotently re-register) a tool; returns its metadata."""
        if not isinstance(name, str) or not TOOL_NAME_RE.fullmatch(name):
            raise ValidationFailed(
                "invalid_tool_name",
                "Tool name must match ^[a-z][a-z0-9_.-]{1,63}$",
            )
        if not isinstance(description, str) or not description.strip():
            raise ValidationFailed("invalid_tool_description", "Tool description must not be empty")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValidationFailed(
                "invalid_tool_schema",
                "Tool parameters must be a JSON Schema object with '\"type\": \"object\"'",
            )

        etype = entry.get("type")
        if etype == "builtin":
            executor = entry.get("executor")
            if executor not in BUILTIN_EXECUTORS and executor not in self._handlers:
                raise ValidationFailed(
                    "unknown_builtin_executor",
                    f"Builtin executor '{executor}' is not available; "
                    f"known: {sorted(set(BUILTIN_EXECUTORS))}",
                )
        elif etype == "http":
            url = entry.get("url") or ""
            if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
                raise ValidationFailed("invalid_entry_url", "http entry requires an absolute http(s) url")
        elif etype == "mcp":
            server = entry.get("server")
            remote = entry.get("remote_tool")
            if not (isinstance(server, str) and server.strip()) or not (
                isinstance(remote, str) and remote.strip()
            ):
                raise ValidationFailed(
                    "invalid_entry_mcp",
                    "mcp entry requires non-empty 'server' and 'remote_tool'",
                )
        else:
            raise ValidationFailed("invalid_entry_type", "Entry type must be 'builtin', 'http' or 'mcp'")

        with self._lock:
            existing = self._tools.get(name)
            meta = {
                "name": name,
                "description": description.strip(),
                "parameters": parameters,
                "entry": {"type": etype,
                          **({k: entry[k] for k in ("executor", "url", "server", "remote_tool")
                              if k in entry})},
                "registered_at": existing["registered_at"] if existing else _now(),
                "updated_at": _now(),
            }
            self._tools[name] = meta
            self._save()
        return dict(meta)

    def set_handler(self, name: str, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        """Wire an in-process handler to an already-registered tool (not persisted)."""
        if name not in self._tools:
            raise NotFound("tool_not_found", f"Tool '{name}' is not registered")
        self._handlers[name] = handler

    def attach_mcp_client(self, server_key: str, client: Any) -> None:
        """Wire a live MCP client for ``server_key`` (in-memory, not persisted).

        Mirrors ``set_handler``: metadata survives restarts via the JSON file,
        but live MCP connections must be re-attached by the hosting code
        (``adapters.mcp.assemble_mcp_tools`` does this at startup).
        """
        if not isinstance(server_key, str) or not server_key.strip():
            raise ValidationFailed("invalid_mcp_server_key", "MCP server key must be a non-empty string")
        if not callable(getattr(client, "call_tool", None)):
            raise ValidationFailed("invalid_mcp_client", "MCP client must expose a callable 'call_tool'")
        self._mcp_clients[server_key] = client

    def mcp_client(self, server_key: str) -> Any:
        """Return the live client attached for ``server_key`` (None if absent)."""
        return self._mcp_clients.get(server_key)

    def list_tools(self) -> list[dict[str, Any]]:
        return [dict(self._tools[n]) for n in sorted(self._tools)]

    def get_tool(self, name: str) -> dict[str, Any]:
        meta = self._tools.get(name)
        if meta is None:
            raise NotFound("tool_not_found", f"Tool '{name}' is not registered")
        return dict(meta)

    def recent_calls(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._log_file.is_file():
            return []
        try:
            log = json.loads(self._log_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(log, list):
            return []
        return log[-max(1, min(limit, _LOG_CAP)):]

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate arguments against the tool schema, then really execute it."""
        meta = self.get_tool(name)
        if not isinstance(arguments, dict):
            raise ValidationFailed("schema_validation", "Arguments must be an object")

        errors = validate_arguments(arguments, meta["parameters"])
        if errors:
            raise ValidationFailed(
                "schema_validation",
                f"Arguments failed schema validation: {'; '.join(errors[:5])}",
            )

        entry = meta["entry"]
        started = _now()
        try:
            if entry["type"] == "builtin":
                executor = entry.get("executor")
                handler = self._handlers.get(executor) or BUILTIN_EXECUTORS[executor]
                result = handler(arguments)
            elif entry["type"] == "mcp":
                client = self._mcp_clients.get(entry.get("server", ""))
                if client is None:
                    raise ValidationFailed(
                        "mcp_server_not_attached",
                        f"MCP server '{entry.get('server')}' has no live client; "
                        "re-run MCP assembly (e.g. after a restart)",
                    )
                result = client.call_tool(entry["remote_tool"], arguments)
            else:
                import httpx

                resp = httpx.post(entry["url"], json=arguments, timeout=10.0)
                if resp.status_code // 100 != 2:
                    raise Conflict("tool_endpoint_error",
                                   f"Tool endpoint returned HTTP {resp.status_code}")
                body = resp.json() if resp.content else {}
                result = body if isinstance(body, dict) else {"response": body}
        except ValidationFailed:
            raise
        except Exception as exc:  # noqa: BLE001 — convert to structured domain error
            raise ValidationFailed(
                "tool_execution_error", f"Tool '{name}' execution failed: {exc}"
            ) from exc

        receipt = {
            "call_id": f"call-{uuid.uuid4().hex[:12]}",
            "tool": name,
            "arguments": arguments,
            "result": result,
            "executed": True,
            "executed_at": started,
        }
        with self._lock:
            self._append_log(receipt)
        return receipt


# Process-wide singleton (metadata restored from JSON on first import/use).
tool_registry = ToolRegistryService()
