"""LIVE Temporal integration against REAL Postgres + REAL Temporal.

- Temporal: real server at 127.0.0.1:7233 (namespace default, queue
  find-yourself), independent OS worker processes. NO time-skipping server.
- Postgres: real rows in the ``findyourself`` database. Approval
  status/digest/expected_version, outbox claim/state and budget
  reservation/cancel all come from PostgresCorePorts over FY_DATABASE_URL.
- The only non-real side effect is the local external effect: it writes one
  idempotent file under .runtime/effects/ (keyed by operation_id), not a paid
  vendor call. We assert the file is created exactly once.

Skipped unless FY_DATABASE_URL is postgresql AND Temporal is reachable.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import sessionmaker
from temporalio.client import Client

from find_yourself.db import models  # noqa: F401
from find_yourself.db.models import (
    Operation,
    Proposal,
    Task,
)
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.proposal import ProposalService
from find_yourself.workflows.workflow import TaskWorkflow

ADDRESS = os.environ.get("FY_TEMPORAL_ADDRESS", "127.0.0.1:7233")
NAMESPACE = os.environ.get("FY_TEMPORAL_NAMESPACE", "default")
# Per-process unique task queue so the in-test workers never compete with the
# always-on production fy-worker container on the shared "find-yourself" queue.
# Override with FY_TEMPORAL_QUEUE only when you know what you are doing.
QUEUE = os.environ.get("FY_TEMPORAL_QUEUE") or f"find-yourself-test-{uuid.uuid4().hex[:8]}"
PG_URL = os.environ.get("FY_DATABASE_URL", "")
ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / ".runtime" / "live_worker.py"
EFFECT_DIR = ROOT / ".runtime" / "effects"


def _reachable(host_port: str) -> bool:
    h, _, port = host_port.partition(":")
    try:
        with socket.create_connection((h, int(port)), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not (PG_URL.startswith("postgresql") and _reachable(ADDRESS)),
    reason="requires real Postgres (FY_DATABASE_URL=postgresql+...) and Temporal reachable",
)


# ---------------------------------------------------------------------------
# Postgres seeding / query helpers
# ---------------------------------------------------------------------------
class PgEnv:
    def __init__(self):
        self.engine = create_engine(PG_URL, future=True, pool_pre_ping=True)
        self.sm = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        self.created: dict[str, list] = {
            "tasks": [], "proposals": [], "operations": [], "reservations": [],
            "audit": [],
        }

    def seed_task(self, tid: str, idem: str) -> None:
        s = self.sm()
        s.add(Task(
            id=tid, owner_id="owner-live", goal="live pg task", domain="personal",
            mode="listen", status="queued", deadline=utcnow() + timedelta(minutes=15),
            idempotency_key=idem,
        ))
        s.commit()
        s.close()
        self.created["tasks"].append(tid)

    def seed_approved_proposal(self, tid: str) -> tuple[str, str]:
        """Create a task.release proposal, approve it -> approved_pending_execution
        with a single pending outbox Operation. Returns (proposal_id, digest)."""
        s = self.sm()
        owner = Actor.owner("owner-live", csrf_token="")
        svc = ProposalService(s, AuditService(s))
        p = svc.create(
            owner, operation="task.release",
            payload={"task": tid, "env": "test"}, reason="live release",
            rollback="none", expires_in_minutes=30,
        )
        s.commit()
        self.created["proposals"].append(p.id)
        decided = svc.decide(owner, p.id, p.digest, approve=True)
        s.commit()
        self.created["operations"].append(
            s.execute(select(Operation).where(Operation.proposal_id == p.id)).scalar_one().id)
        pid, digest = decided.id, decided.digest
        s.close()
        return pid, digest

    def query(self, sql: str, **params) -> Any:
        s = self.sm()
        try:
            return s.execute(text(sql), params).scalar()
        finally:
            s.close()

    def cleanup(self) -> None:
        s = self.sm()
        try:
            if self.created["operations"]:
                s.execute(delete(Operation).where(Operation.id.in_(self.created["operations"])))
            if self.created["proposals"]:
                s.execute(delete(Proposal).where(Proposal.id.in_(self.created["proposals"])))
            if self.created["tasks"]:
                tids = self.created["tasks"]
                # budget_ledger references budget_reservations; delete in FK order.
                s.execute(text(
                    "delete from budget_ledger where reservation_id in "
                    "(select id from budget_reservations where task_id = any(:t))"),
                    {"t": tids})
                s.execute(text(
                    "delete from budget_reservations where task_id = any(:t)"), {"t": tids})
                s.execute(text("delete from task_attempts where task_id = any(:t)"), {"t": tids})
                s.execute(text("delete from tasks where id = any(:t)"), {"t": tids})
            s.commit()
        except Exception:
            s.rollback()
        s.close()
        self.engine.dispose()


# ---------------------------------------------------------------------------
# Temporal / worker helpers
# ---------------------------------------------------------------------------
def _deadline() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()


def _wf_input(tid: str, idem: str) -> dict[str, Any]:
    return {
        "task_id": tid, "owner_id": "owner-live", "goal": "live pg task",
        "domain": "personal", "mode": "listen", "idempotency_key": idem,
        "limits": {"max_steps": 8, "max_retries": 1, "max_cost_usd": 1.0,
                   "deadline": _deadline()},
        "context": {},
    }


async def _wait_query(h, pred, timeout: float = 40.0) -> dict:
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    last: dict = {}
    while loop.time() < end:
        last = await h.query(TaskWorkflow.status)
        if pred(last):
            return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"query wait timeout; last={last}")


async def _new_client() -> Client:
    return await Client.connect(ADDRESS, namespace=NAMESPACE)


class _Worker:
    def __init__(self, proc, snap: Path):
        self.proc = proc
        self.snap = snap

    async def ready(self) -> None:
        assert self.proc.stdout is not None
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"worker exited rc={self.proc.returncode}")
            t = line.decode("utf-8", "replace").strip()
            if '"polling"' in t:
                return
            if '"connect_failed"' in t or '"ports_error"' in t:
                raise RuntimeError(f"worker startup error: {t}")

    async def kill(self) -> None:
        if self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()


async def _spawn(tmp_path: Path, tag: str, scenario: dict) -> _Worker:
    sp = tmp_path / f"scenario-{tag}.json"
    snap = tmp_path / f"snapshot-{tag}.json"
    sp.write_text(json.dumps(scenario), encoding="utf-8")
    env = dict(os.environ)
    env["FY_DATABASE_URL"] = PG_URL
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(RUNNER), "--temporal-address", ADDRESS,
        "--namespace", NAMESPACE, "--queue", QUEUE, "--scenario", str(sp),
        "--snapshot", str(snap), "--ports", "pg", "--identity", f"fy-{tag}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(ROOT), env=env,
    )
    w = _Worker(proc, snap)
    await w.ready()
    return w


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
async def test_live_simple_tool_then_finish(tmp_path):
    pg = PgEnv()
    tid = f"live-s-{uuid.uuid4().hex[:6]}"
    pg.seed_task(tid, f"idem-{tid}")
    worker = await _spawn(tmp_path, "simple", {
        "plan": [{"kind": "tool_call", "tool": "echo",
                  "payload": {"estimated_usd": 0.01, "text": "hi"}}],
    })
    try:
        client = await _new_client()
        h = await client.start_workflow(TaskWorkflow.run, _wf_input(tid, f"idem-{tid}"),
                                        id=f"fy-task:{tid}", task_queue=QUEUE)
        res = await asyncio.wait_for(h.result(), timeout=40)
        assert res["status"] == "completed"
        # Real Postgres: task completed, a budget reservation was written.
        assert pg.query("select status from tasks where id=:i", i=tid) == "completed"
        n_res = pg.query("select count(*) from budget_reservations where task_id=:i", i=tid)
        assert n_res >= 1
    finally:
        await worker.kill()
        pg.cleanup()


async def test_live_approval_restart(tmp_path):
    pg = PgEnv()
    tid = f"live-a-{uuid.uuid4().hex[:6]}"
    pg.seed_task(tid, f"idem-{tid}")
    pid, digest = pg.seed_approved_proposal(tid)
    far = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()

    scenario = {
        "plan": [{"kind": "external_side_effect", "proposal_id": pid,
                  "expected_digest": digest, "expected_version": 0,
                  "proposal_expires_at": far}],
        "watch_proposal_id": pid,
        "effect_dir": str(EFFECT_DIR),
    }
    # Capture the operation id BEFORE approval to count effect files later.
    op_id = pg.query("select id from operations where proposal_id=:p", p=pid)

    w1 = await _spawn(tmp_path, "w1", scenario)
    try:
        client = await _new_client()
        h = await client.start_workflow(TaskWorkflow.run, _wf_input(tid, f"idem-{tid}"),
                                        id=f"fy-task:{tid}", task_queue=QUEUE)
        parked = await _wait_query(
            h, lambda s: s.get("state") == "awaiting_approval", timeout=40)
        assert parked["last_checkpoint_key"] == f"task:{tid}:attempt:1:stage:awaiting_approval"
    finally:
        await w1.kill()  # hard kill while parked

    w2 = await _spawn(tmp_path, "w2", scenario)
    try:
        await h.signal("approval", args=[pid, digest, "approve"])
        await h.signal("approval", args=[pid, digest, "approve"])  # duplicate
        res = await asyncio.wait_for(h.result(), timeout=40)
        assert res["status"] == "completed"
        # Real Postgres assertions.
        assert pg.query("select status from proposals where id=:p", p=pid) == "executed"
        op_state = pg.query("select state from operations where proposal_id=:p", p=pid)
        assert op_state == "succeeded", op_state
        # Effect file created exactly once for this operation.
        eff = list(EFFECT_DIR.glob(f"{op_id}.json"))
        assert len(eff) == 1, f"expected 1 effect file, got {len(eff)}"
        assert pg.query("select status from tasks where id=:i", i=tid) == "completed"
    finally:
        await w2.kill()
        pg.cleanup()


async def test_live_provide_input_and_cancel(tmp_path):
    pg = PgEnv()
    tid_in = f"live-i-{uuid.uuid4().hex[:6]}"
    pg.seed_task(tid_in, f"idem-{tid_in}")
    scn = {"plan": [{"kind": "wait_input"}]}
    w_in = await _spawn(tmp_path, "input", scn)
    try:
        client = await _new_client()
        h_in = await client.start_workflow(TaskWorkflow.run, _wf_input(tid_in, f"idem-{tid_in}"),
                                           id=f"fy-task:{tid_in}", task_queue=QUEUE)
        await _wait_query(h_in, lambda s: s.get("state") == "awaiting_input", timeout=40)
        await h_in.signal("provide_input", args=[{"answer": 42}])
        res_in = await asyncio.wait_for(h_in.result(), timeout=30)
        assert res_in["status"] == "completed"
    finally:
        await w_in.kill()

    tid_cx = f"live-c-{uuid.uuid4().hex[:6]}"
    pg.seed_task(tid_cx, f"idem-{tid_cx}")
    w_cx = await _spawn(tmp_path, "cancel", scn)
    try:
        client = await _new_client()
        h_cx = await client.start_workflow(TaskWorkflow.run, _wf_input(tid_cx, f"idem-{tid_cx}"),
                                           id=f"fy-task:{tid_cx}", task_queue=QUEUE)
        await _wait_query(h_cx, lambda s: s.get("state") == "awaiting_input", timeout=40)
        await h_cx.signal("cancel", args=["owner stopped it"])
        res_cx = await asyncio.wait_for(h_cx.result(), timeout=30)
        assert res_cx["status"] == "cancelled"
        assert pg.query("select status from tasks where id=:i", i=tid_cx) == "cancelled"
    finally:
        await w_cx.kill()
        pg.cleanup()
