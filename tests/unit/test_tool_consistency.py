"""Tool whitelist enforcement + registry hardening + consistency validation.

Covers the P0 fix that wires ``Actor.require_tool`` into the harness gateway's
``invoke()`` (previously the whitelist was defined but never consulted), the
hardened ``register_tool`` contract, atomic snapshot/rollback, and the
DB-backed referencer consistency check (missing/orphan).
"""

from __future__ import annotations

import pytest

from find_yourself.db.base import Base
from find_yourself.db.models import ServiceIdentity
from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from find_yourself.skills.harness import (
    FunctionCallingGateway,
    ToolConsistencyValidator,
    attach_tool_consistency_guard,
    gateway,
)


def _make_session_maker():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import find_yourself.db.models  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _fresh_gateway() -> FunctionCallingGateway:
    gw = FunctionCallingGateway()
    return gw


# ---------------------------------------------------------------------------
# 1. Whitelist enforcement in invoke()
# ---------------------------------------------------------------------------

def test_owner_passes_whitelist():
    receipt = gateway.invoke(
        tool_name="system.health_check", arguments={}, actor=Actor.owner("u1")
    )
    assert receipt["executed"] is True


def test_service_actor_without_allowed_tools_denied():
    actor = Actor.service("svc-1", "agent")
    with pytest.raises(PermissionDenied) as exc:
        gateway.invoke(tool_name="system.health_check", arguments={}, actor=actor)
    assert exc.value.code == "tool_forbidden"


def test_service_actor_with_explicit_tool_allowed_and_others_denied():
    actor = Actor.service("svc-2", "agent", allowed_tools=["system.health_check"])
    receipt = gateway.invoke(
        tool_name="system.health_check", arguments={}, actor=actor
    )
    assert receipt["executed"] is True

    with pytest.raises(PermissionDenied):
        gateway.invoke(
            tool_name="procurement.evaluate_tco",
            arguments={"upfront_cost": 1, "monthly_cost": 1, "months": 1},
            actor=actor,
        )


def test_service_actor_wildcard_allows_all():
    actor = Actor.service("svc-3", "agent", allowed_tools=["*"])
    receipt = gateway.invoke(
        tool_name="system.health_check", arguments={}, actor=actor
    )
    assert receipt["executed"] is True


def test_authorization_precedes_existence_probe():
    # An unauthorized identity must not learn whether a tool exists.
    actor = Actor.service("svc-4", "agent")
    with pytest.raises(PermissionDenied) as exc:
        gateway.invoke(tool_name="secret.unregistered_tool", arguments={}, actor=actor)
    assert exc.value.code == "tool_forbidden"


# ---------------------------------------------------------------------------
# 2. register_tool hardening
# ---------------------------------------------------------------------------

def test_register_rejects_bad_schema_and_name():
    gw = _fresh_gateway()
    with pytest.raises(ValidationFailed):
        gw.register_tool(
            name="ok.name", description="d", handler=lambda a: {},
            schema={"type": "string"},
        )
    with pytest.raises(ValidationFailed):
        gw.register_tool(
            name="Bad Name", description="d", handler=lambda a: {},
            schema={"type": "object"},
        )


def test_register_duplicate_requires_replace():
    gw = _fresh_gateway()
    schema = {"type": "object", "properties": {}}
    with pytest.raises(Conflict):
        gw.register_tool(
            name="system.health_check", description="dup",
            handler=lambda a: {}, schema=schema,
        )
    gw.register_tool(
        name="system.health_check", description="swapped",
        handler=lambda a: {"status": "swapped"}, schema=schema, replace=True,
    )
    receipt = gw.invoke(
        tool_name="system.health_check", arguments={}, actor=Actor.owner("u1")
    )
    assert receipt["result"] == {"status": "swapped"}


def test_register_rejects_out_of_range_level_and_version():
    gw = _fresh_gateway()
    schema = {"type": "object", "properties": {}}
    with pytest.raises(ValidationFailed):
        gw.register_tool(
            name="x.tool", description="d", handler=lambda a: {},
            schema=schema, required_level=7,
        )
    with pytest.raises(ValidationFailed):
        gw.register_tool(
            name="x.tool", description="d", handler=lambda a: {},
            schema=schema, version=0,
        )


def test_snapshot_and_restore_roundtrip():
    gw = _fresh_gateway()
    snap = gw.snapshot()
    gw.unregister_tool("metrics.calculate_ratio")
    assert "metrics.calculate_ratio" not in gw.snapshot()
    gw.restore(snap)
    assert "metrics.calculate_ratio" in gw.snapshot()


def test_unregister_unknown_tool():
    gw = _fresh_gateway()
    with pytest.raises(NotFound):
        gw.unregister_tool("no.such_tool")


# ---------------------------------------------------------------------------
# 3. DB-backed consistency validation
# ---------------------------------------------------------------------------

def test_validator_detects_missing_and_orphan():
    session_maker = _make_session_maker()
    with session_maker() as s:
        s.add(ServiceIdentity(
            id="svc-tool-user", kind="tool_gateway", name="ToolUser",
            capabilities=["tool:custom.report"], secret_hash="x" * 64,
        ))
        s.commit()

    gw = _fresh_gateway()
    gw.register_tool(
        name="custom.other", description="unreferenced dynamic tool",
        handler=lambda a: {}, schema={"type": "object", "properties": {}},
    )
    validator = ToolConsistencyValidator(session_maker)
    gw.attach_consistency(validator)

    report = gw.run_consistency_check()
    assert report["ok"] is False
    assert report["missing"] == ["custom.report"]
    assert "custom.other" in report["orphan"]
    # builtin defaults are exempt from orphan reporting
    assert "system.health_check" not in report["orphan"]


def test_unregister_refuses_while_referenced():
    session_maker = _make_session_maker()
    with session_maker() as s:
        s.add(ServiceIdentity(
            id="svc-holder", kind="tool_gateway", name="Holder",
            capabilities=["tool:procurement.evaluate_tco"], secret_hash="x" * 64,
        ))
        s.commit()

    gw = _fresh_gateway()
    gw.attach_consistency(ToolConsistencyValidator(session_maker))

    with pytest.raises(Conflict) as exc:
        gw.unregister_tool("procurement.evaluate_tco")
    assert exc.value.code == "tool_in_use"

    # revoke the reference first, then unregister succeeds
    with session_maker() as s:
        ident = s.get(ServiceIdentity, "svc-holder")
        ident.capabilities = []
        s.commit()
    gw.unregister_tool("procurement.evaluate_tco")
    assert "procurement.evaluate_tco" not in gw.snapshot()


def test_startup_guard_raises_on_dangling_reference():
    session_maker = _make_session_maker()
    with session_maker() as s:
        s.add(ServiceIdentity(
            id="svc-dangling", kind="tool_gateway", name="Dangling",
            capabilities=["tool:missing.entirely"], secret_hash="x" * 64,
        ))
        s.commit()

    with pytest.raises(RuntimeError) as exc:
        attach_tool_consistency_guard(session_maker)
    assert "missing.entirely" in str(exc.value)
    # detach again so the global singleton is not left with this test's validator
    gateway.attach_consistency(None)  # type: ignore[arg-type]


def test_startup_guard_passes_on_clean_db():
    session_maker = _make_session_maker()
    report = attach_tool_consistency_guard(session_maker)
    assert report["ok"] is True
    assert report["missing"] == []
    gateway.attach_consistency(None)  # type: ignore[arg-type]
