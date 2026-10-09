"""Worker entrypoint: ``python -m find_yourself.workflows.worker``.

Registers :class:`TaskWorkflow` and the :class:`Activities` bound to an injected
:class:`CorePorts` adapter. All startup / health output is one-line JSON on
stdout so an operator or supervisor can parse it. Secrets, connection strings
and private content are NEVER printed.

Adapters (``--adapter``):
  auto  (default) production/integration: real PostgresCorePorts over
        FY_DATABASE_URL. No fake fallback.
  pg    explicit real PostgresCorePorts (same as auto, for clarity).
  fake  in-memory adapter, LOCAL TEST ONLY; must never be used for acceptance.

Exit codes:
  0  clean shutdown / health-check ready
  2  usage / argument error (argparse)
  64 cannot connect to Temporal, DB, or required configuration missing
  70 unexpected runtime error
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from . import __version__
from .activities import Activities
from .workflow import TaskWorkflow

TASK_WORKFLOW_NAME = "fy-task-workflow"


def _emit(obj: dict[str, Any]) -> None:
    """Single-line JSON event on stdout, no secrets, no private content."""
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    print(line, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m find_yourself.workflows.worker",
        description="Find Yourself Temporal worker: durable task orchestration.",
    )
    p.add_argument("--temporal-address", default="",
                   help="Temporal frontend host:port (env FY_TEMPORAL_ADDRESS).")
    p.add_argument("--namespace", default="default",
                   help="Temporal namespace (default: default).")
    p.add_argument("--queue", default="find-yourself",
                   help="Task queue to poll (default: find-yourself).")
    p.add_argument("--identity", default="", help="Optional worker identity suffix.")
    p.add_argument(
        "--adapter", choices=["auto", "pg", "fake"], default="auto",
        help="Core ports adapter. 'auto'/'pg' require a real Postgres DSN in "
             "FY_DATABASE_URL and are what integration/prod use. 'fake' is an "
             "in-memory adapter for local tests only and must never be used "
             "for acceptance. auto/pg never fall back to fake.")
    p.add_argument("--effect-dir", default=".runtime/effects",
                   help="Directory for idempotent local external effects "
                        "(default: .runtime/effects).")
    p.add_argument("--health-check", action="store_true",
                   help="Connect to Temporal AND verify the configured adapter "
                        "is usable, print readiness JSON, and exit (0=ready, 64=not).")
    p.add_argument("--max-concurrent-activities", type=int, default=10)
    return p


def _resolve_address(args: argparse.Namespace) -> str:
    return args.temporal_address or os.environ.get("FY_TEMPORAL_ADDRESS", "")


def _is_fake(args: argparse.Namespace) -> bool:
    return args.adapter == "fake"


def _build_pg_activities(effect_dir: str) -> Activities:
    """Construct Activities backed by the real PostgresCorePorts.

    Raises RuntimeError (translated to config_error/connect_failed) when
    FY_DATABASE_URL is missing/non-postgres or the DB cannot be reached.
    """
    url = os.environ.get("FY_DATABASE_URL", "")
    if not url:
        raise RuntimeError("FY_DATABASE_URL is not set")
    if not url.startswith("postgresql"):
        raise RuntimeError("FY_DATABASE_URL must point to a PostgreSQL DSN")
    # Imported lazily so --adapter fake / health-only paths do not require a
    # working SQL stack, and so a missing Core module surfaces as config_error.
    from sqlalchemy import text

    from ..db.session import engine_from_url, session_factory
    from ..services.pg_ports import PostgresCorePorts

    engine = engine_from_url(url)
    # Prove the DB is reachable before declaring ready (no rows, no secrets).
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    sf = session_factory(engine)
    ports = PostgresCorePorts(sf, effect_dir=effect_dir)
    return Activities(ports, actor="worker-pg")


def _make_activities(args: argparse.Namespace) -> Activities:
    if _is_fake(args):
        # Test-only in-memory ports. Kept out of the production wiring path.
        from .fake import InMemoryPorts  # local import: test-only
        return Activities(InMemoryPorts(), actor="worker-fake")
    return _build_pg_activities(args.effect_dir)


async def _run(args: argparse.Namespace) -> int:
    address = _resolve_address(args)
    registered_activities = [
        "task_record_started", "task_update_stage", "task_complete", "task_fail",
        "task_cancel", "plan_next_step", "reserve_budget", "settle_budget",
        "release_budget", "run_tool_step", "verify_approval_permission",
        "claim_outbox_operation", "execute_external_effect",
        "record_outbox_result", "append_audit",
    ]
    _emit({"event": "starting", "service": "find-yourself-worker",
           "version": __version__, "namespace": args.namespace,
           "queue": args.queue, "adapter": args.adapter,
           "workflows": [TASK_WORKFLOW_NAME],
           "activity_count": len(registered_activities)})

    if not address:
        _emit({"event": "config_error", "code": "temporal_address_missing",
               "message": "set --temporal-address or FY_TEMPORAL_ADDRESS"})
        return 64

    # Build the adapter before connecting to Temporal so a missing/invalid DB
    # DSN fails fast with a single-line JSON error (no fake fallback).
    try:
        activities = _make_activities(args)
    except RuntimeError as exc:
        _emit({"event": "config_error", "code": "adapter_unavailable",
               "message": str(exc)})
        return 64
    except Exception as exc:  # DB unreachable / driver error
        _emit({"event": "connect_failed", "code": "postgres_connect_failed",
               "message": type(exc).__name__})
        return 64

    try:
        client = await Client.connect(
            address, namespace=args.namespace, identity=args.identity or None)
    except Exception as exc:  # surface a safe, machine-readable error
        _emit({"event": "connect_failed", "code": "temporal_connect_failed",
               "message": type(exc).__name__})
        return 64

    _emit({"event": "connected", "namespace": args.namespace, "queue": args.queue,
           "adapter": args.adapter})

    if args.health_check:
        # Temporal connected AND a real adapter was constructed (SELECT 1 ran).
        _emit({"event": "ready", "status": "ok", "adapter": args.adapter})
        return 0

    worker = Worker(
        client,
        task_queue=args.queue,
        workflows=[TaskWorkflow],
        activities=activities.all(),
        max_concurrent_activities=args.max_concurrent_activities,
    )
    _emit({"event": "polling", "queue": args.queue})
    await worker.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        _emit({"event": "shutdown", "reason": "keyboard_interrupt"})
        return 0
    except Exception as exc:  # never print a traceback / secret
        _emit({"event": "fatal", "code": type(exc).__name__, "message": str(exc)[:200]})
        return 70


if __name__ == "__main__":
    sys.exit(main())
