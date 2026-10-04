"""P2 MCP ecosystem integration: McpClient wired into the dynamic tool registry.

Covers (all offline — MCP servers are simulated in-process via McpStdioServer,
real newline-delimited JSON-RPC frames, no network):

* ``assemble_mcp_tools`` registers remote tools as ``<server_key>.<tool_name>``
  with ``{"type": "mcp", ...}`` entries (registration bridge);
* ``registry.invoke`` bridges to ``McpClient.call_tool`` and produces the same
  structured receipt + call log as builtin/http tools (evidence trail intact);
* an unreachable / misconfigured server is skipped with a WARNING log, never
  silently treated as registered;
* the ``<server_key>.<tool_name>`` convention prevents cross-server name
  collisions and refuses to clobber a different existing registration;
* ``FY_MCP_SERVERS`` config parsing (default empty = MCP disabled);
* the app lifespan calls the assembly exactly through the real startup path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.adapters.mcp import (
    McpClient,
    McpStdioServer,
    McpTool,
    assemble_mcp_tools,
)
from find_yourself.config import Settings
from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.tool_registry import ToolRegistryService

SECRET = "s" * 32  # satisfies the Settings security validator


# --- helpers -------------------------------------------------------------------

def _demo_server() -> McpStdioServer:
    """In-process MCP server exposing two distinguishable tools."""
    return McpStdioServer(
        tools=[
            McpTool(name="ping", description="Health ping",
                    handler=lambda a: {"pong": True, "args": a}),
            McpTool(name="sha256", description="Hash text",
                    handler=lambda a: {"hash": hashlib.sha256(a.get("text", "").encode()).hexdigest()}),
        ]
    )


def _boom_server() -> McpStdioServer:
    def explode(_arguments):
        raise RuntimeError("boom")

    return McpStdioServer(tools=[McpTool(name="explode", description="Always fails",
                                         handler=explode)])


@pytest.fixture()
def registry(tmp_path):
    """Isolated registry so the process-wide singleton's JSON state is untouched."""
    return ToolRegistryService(persist_dir=tmp_path / "tool_registry")


@pytest.fixture()
def demo_settings():
    return SimpleNamespace(mcp_servers={"demo": {}})


# --- 1. config: FY_MCP_SERVERS parsing -----------------------------------------

def test_settings_mcp_servers_default_empty_and_env_json(monkeypatch):
    monkeypatch.delenv("FY_MCP_SERVERS", raising=False)
    s = Settings(_env_file=None, environment="test", session_secret=SECRET)
    assert s.mcp_servers == {}  # default: MCP disabled, boot unchanged

    monkeypatch.setenv("FY_MCP_SERVERS", json.dumps(
        {"demo": {"command": [sys.executable, "-m", "find_yourself.adapters.mcp"],
                  "env": {"FY_MCP_ALLOWED_PRIVILEGED": ""}}}))
    s2 = Settings(_env_file=None, environment="test", session_secret=SECRET)
    assert s2.mcp_servers["demo"]["command"][-1] == "find_yourself.adapters.mcp"
    assert "env" in s2.mcp_servers["demo"]


# --- 2. registration bridge -----------------------------------------------------

def test_assembly_registers_remote_tools_with_prefixed_names(registry, demo_settings):
    client = McpClient.from_server(_demo_server())
    statuses = assemble_mcp_tools(
        registry=registry, settings=demo_settings, clients={"demo": client},
    )
    assert len(statuses) == 1
    status = statuses[0]
    assert status["server"] == "demo" and status["ok"] is True and status["error"] is None
    assert status["registered"] == ["demo.ping", "demo.sha256"]

    meta = registry.get_tool("demo.sha256")
    assert meta["entry"] == {"type": "mcp", "server": "demo", "remote_tool": "sha256"}
    assert meta["parameters"] == {"type": "object"}  # remote inputSchema reused
    assert meta["description"] == "Hash text"


def test_invalid_mcp_entries_rejected(registry):
    with pytest.raises(ValidationFailed) as exc:
        registry.register(name="demo.lone", description="x",
                          parameters={"type": "object"},
                          entry={"type": "mcp", "server": "demo"})
    assert exc.value.code == "invalid_entry_mcp"
    with pytest.raises(ValidationFailed):
        registry.register(name="demo.lone", description="x",
                          parameters={"type": "object"}, entry={"type": "mcp"})


# --- 3. invoke bridging: receipt + call log + schema reuse ----------------------

def test_mcp_invoke_receipt_and_call_log(registry, demo_settings):
    assemble_mcp_tools(registry=registry, settings=demo_settings,
                       clients={"demo": McpClient.from_server(_demo_server())})

    receipt = registry.invoke("demo.sha256", {"text": "abc"})
    assert receipt["executed"] is True
    assert receipt["tool"] == "demo.sha256"
    assert receipt["result"] == {"hash": hashlib.sha256(b"abc").hexdigest()}
    assert receipt["call_id"].startswith("call-")

    # Evidence trail: the call is appended to the same on-disk log as builtin/http.
    calls = registry.recent_calls()
    assert len(calls) == 1 and calls[0]["tool"] == "demo.sha256"
    assert calls[0]["result"] == {"hash": hashlib.sha256(b"abc").hexdigest()}


def test_mcp_invoke_reuses_schema_validation(registry, demo_settings):
    assemble_mcp_tools(registry=registry, settings=demo_settings,
                       clients={"demo": McpClient.from_server(_demo_server())})
    # Tighten the declared schema for the bridged remote tool.
    registry.register(
        name="demo.strict", description="Strict remote tool",
        parameters={"type": "object", "required": ["q"],
                    "properties": {"q": {"type": "string"}}},
        entry={"type": "mcp", "server": "demo", "remote_tool": "ping"},
    )
    with pytest.raises(ValidationFailed) as exc:
        registry.invoke("demo.strict", {})
    assert exc.value.code == "schema_validation"

    receipt = registry.invoke("demo.strict", {"q": "hi"})
    assert receipt["result"]["pong"] is True


def test_mcp_remote_tool_failure_wraps_as_tool_execution_error(registry):
    statuses = assemble_mcp_tools(
        registry=registry,
        settings=SimpleNamespace(mcp_servers={"boom": {}}),
        clients={"boom": McpClient.from_server(_boom_server())},
    )
    assert statuses[0]["ok"] is True and statuses[0]["registered"] == ["boom.explode"]
    with pytest.raises(ValidationFailed) as exc:
        registry.invoke("boom.explode", {})
    assert exc.value.code == "tool_execution_error"


def test_mcp_invoke_without_attached_client_fails_honestly(tmp_path):
    """After a restart the JSON metadata is back but no live client is wired."""
    d = tmp_path / "tr"
    r1 = ToolRegistryService(persist_dir=d)
    r1.register(name="demo.ping", description="p", parameters={"type": "object"},
                entry={"type": "mcp", "server": "demo", "remote_tool": "ping"})
    r2 = ToolRegistryService(persist_dir=d)
    assert r2.get_tool("demo.ping")["entry"]["remote_tool"] == "ping"  # survived restart
    with pytest.raises(ValidationFailed) as exc:
        r2.invoke("demo.ping", {})
    assert exc.value.code == "mcp_server_not_attached"
    assert r2.recent_calls() == []  # the failed call leaves no fake receipt


# --- 4. unreachable / misconfigured server: skip + log --------------------------

class _ListHandler(logging.Handler):
    """Test-scoped handler: immune to global logging state mutations by other
    tests in the same session (caplog proved order-dependent here)."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_unreachable_and_misconfigured_server_skipped_and_logged(registry):
    settings = SimpleNamespace(mcp_servers={
        "dead": {"command": [sys.executable, "-c", "import sys; sys.exit(1)"]},
        "broken": {"command": "not-a-list"},
    })
    h = _ListHandler()
    logger = logging.getLogger("find_yourself.adapters.mcp")
    old_level = logger.level
    logger.addHandler(h)
    logger.setLevel(logging.WARNING)
    try:
        statuses = assemble_mcp_tools(registry=registry, settings=settings)
    finally:
        logger.removeHandler(h)
        logger.setLevel(old_level)

    by_server = {s["server"]: s for s in statuses}
    assert by_server["dead"]["ok"] is False
    assert by_server["dead"]["error"]  # honest error text, not silence
    assert by_server["broken"]["ok"] is False
    assert "command" in by_server["broken"]["error"]
    assert registry.list_tools() == []  # nothing fake-registered

    assert any("dead" in m for m in h.messages)
    assert any("broken" in m for m in h.messages)


# --- 5. naming convention: cross-server collision prevention --------------------

def test_cross_server_same_remote_tool_names_do_not_collide(registry):
    def server(marker):
        return McpStdioServer(tools=[McpTool(
            name="ping", description="p", handler=lambda a, m=marker: {"from": m})])

    statuses = assemble_mcp_tools(
        registry=registry,
        settings=SimpleNamespace(mcp_servers={"alpha": {}, "beta": {}}),
        clients={"alpha": McpClient.from_server(server("alpha")),
                 "beta": McpClient.from_server(server("beta"))},
    )
    assert all(s["ok"] for s in statuses)
    assert registry.get_tool("alpha.ping")["entry"]["remote_tool"] == "ping"
    assert registry.get_tool("beta.ping")["entry"]["remote_tool"] == "ping"
    assert registry.invoke("alpha.ping", {})["result"] == {"from": "alpha"}
    assert registry.invoke("beta.ping", {})["result"] == {"from": "beta"}


def test_assembly_never_clobbers_different_existing_registration(registry):
    registry.register(name="srv.ping", description="local builtin",
                      parameters={"type": "object"},
                      entry={"type": "builtin", "executor": "echo"})
    h = _ListHandler()
    logger = logging.getLogger("find_yourself.adapters.mcp")
    old_level = logger.level
    logger.addHandler(h)
    logger.setLevel(logging.WARNING)
    try:
        statuses = assemble_mcp_tools(
            registry=registry,
            settings=SimpleNamespace(mcp_servers={"srv": {}}),
            clients={"srv": McpClient.from_server(_demo_server())},
        )
    finally:
        logger.removeHandler(h)
        logger.setLevel(old_level)
    assert statuses[0]["registered"] == ["srv.sha256"]
    assert statuses[0]["skipped"] == [{
        "tool": "srv.ping",
        "reason": "name collision: 'srv.ping' already registered with a different entry",
    }]
    assert any("collision" in m for m in h.messages)
    # The local builtin is untouched and still executes.
    assert registry.invoke("srv.ping", {"a": 1})["result"] == {"echo": {"a": 1}}


def test_reassembly_is_idempotent(registry, demo_settings):
    client = McpClient.from_server(_demo_server())
    first = assemble_mcp_tools(registry=registry, settings=demo_settings, clients={"demo": client})
    meta_before = registry.get_tool("demo.ping")
    second = assemble_mcp_tools(registry=registry, settings=demo_settings, clients={"demo": client})
    assert first[0]["skipped"] == [] and second[0]["skipped"] == []
    assert second[0]["registered"] == ["demo.ping", "demo.sha256"]
    meta_after = registry.get_tool("demo.ping")
    assert meta_after["registered_at"] == meta_before["registered_at"]


# --- 6. app lifespan wiring ------------------------------------------------------

def test_lifespan_wires_mcp_servers_and_skips_dead_server(monkeypatch, tmp_path):
    import find_yourself.db.canvas_models  # noqa: F401
    import find_yourself.db.models  # noqa: F401
    import find_yourself.db.profile_models  # noqa: F401
    import find_yourself.db.prompt_models  # noqa: F401
    import find_yourself.db.session_state_models  # noqa: F401
    import find_yourself.db.staging_models  # noqa: F401
    import find_yourself.db.sync_models  # noqa: F401
    import find_yourself.db.team_models  # noqa: F401
    import find_yourself.db.workbench_models  # noqa: F401
    from find_yourself.api.app import create_app
    from find_yourself.db.base import Base
    import find_yourself.services.tool_registry as tr_module

    monkeypatch.delenv("FY_MCP_SERVERS", raising=False)
    isolated = ToolRegistryService(persist_dir=tmp_path / "tr")
    monkeypatch.setattr(tr_module, "tool_registry", isolated)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    app_settings = Settings(
        _env_file=None, environment="test", session_secret=SECRET,
        mcp_servers={
            "local": {"command": [sys.executable, "-m", "find_yourself.adapters.mcp"]},
            "dead": {"command": [sys.executable, "-c", "import sys; sys.exit(1)"]},
        },
    )
    app = create_app(session_maker=sessionmaker(bind=engine, expire_on_commit=False),
                     settings=app_settings)
    with TestClient(app):  # entering the context runs the lifespan
        meta = isolated.get_tool("local.ping")
        assert meta["entry"] == {"type": "mcp", "server": "local", "remote_tool": "ping"}
        with pytest.raises(NotFound):
            isolated.get_tool("dead.ping")
        # Live bridge through the real startup path.
        receipt = isolated.invoke("local.sha256", {"text": "abc"})
        assert receipt["result"] == {"hash": hashlib.sha256(b"abc").hexdigest()}

    status = {s["server"]: s for s in app.state.mcp_status}
    assert status["local"]["ok"] is True
    assert status["dead"]["ok"] is False and status["dead"]["error"]
    # Boot was not blocked by the dead server and the app is still serving.
    client = TestClient(app)
    resp = client.get("/health/live")
    assert resp.status_code == 200


# --- 7. HTTP surface model accepts mcp entries -----------------------------------

def test_tool_entry_api_model_accepts_mcp():
    from find_yourself.api.routes.tools import ToolEntryIn

    entry = ToolEntryIn(type="mcp", server="demo", remote_tool="ping")
    assert entry.model_dump(exclude_none=True) == {
        "type": "mcp", "server": "demo", "remote_tool": "ping",
    }
    with pytest.raises(Exception):
        ToolEntryIn(type="mcp", server="demo")  # remote_tool missing
    with pytest.raises(Exception):
        ToolEntryIn(type="stdio")  # pattern still rejects unknown types
