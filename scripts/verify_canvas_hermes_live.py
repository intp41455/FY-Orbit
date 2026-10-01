"""Execute real end-to-end Canvas API -> Hermes CLI dispatch and record authentic trace."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from find_yourself.adapters.hermes_adapter import HermesAdapter
from find_yourself.db.base import Base
from find_yourself.db.models import Task, CanvasInstance, DispatchRecord, CanvasEvent, BudgetReservation
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.budget import BudgetService
from find_yourself.services.canvas import CanvasService


def main():
    print("[1/5] Initializing Hermes adapter and probing host binary...")
    adapter = HermesAdapter()
    probe = adapter.probe()
    print(f"Probe result: {probe}")
    if not probe["healthy"]:
        print(f"ERROR: Hermes binary not healthy: {probe.get('blocking_reason')}")
        sys.exit(1)

    print("[2/5] Setting up isolated database session for authentic trace...")
    db_path = Path(".runtime") / "live_hermes_canvas_trace.db"
    if db_path.exists():
        db_path.unlink()
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)

    session = Session(engine)
    audit = AuditService(session)
    budget = BudgetService(session, audit)
    canvas = CanvasService(session, audit, budget=budget, hermes_adapter=adapter)

    owner = Actor(
        subject_type="owner",
        owner_id="owner-local-admin",
        service_id="",
        service_kind="",
        csrf_token="csrf-live-01",
        bound_domains=("personal", "work"),
    )

    print("[3/5] Creating root task and canvas instance...")
    root_task = Task(
        id="task-live-root-01",
        owner_id=owner.owner_id,
        goal="Personal coordination and multi-agent safety guidelines",
        domain="personal",
        deadline=datetime.now(timezone.utc),
        idempotency_key="task-live-root-01",
    )
    session.add(root_task)
    session.flush()

    instance = canvas.create_instance(
        actor=owner,
        project_name="Hermes真实端到端派发验证",
        template_id="personal",
    )
    print(f"Canvas instance created: {instance.id} (orchestrator: {instance.orchestrator_id})")

    print("[4/5] Dispatching subtask to Hermes CLI via CanvasService...")
    goal = "请用一句简短中文回答并包含'多智能体'关键词：你作为协调代理的核心职责是什么？"
    subtask_id = "sub-live-hermes-01"

    disp = canvas.dispatch_subtask(
        actor=owner,
        instance_id=instance.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal=goal,
        acceptance_criteria="多智能体",
        budget_slice=0.25,
        subtask_id=subtask_id,
        idempotency_key="disp-live-hermes-01",
    )
    session.commit()

    print(f"Dispatch status: {disp.state}")
    print(f"Subtask ID: {disp.subtask_id}")
    print(f"Local Execution ID: {disp.input_ref.get('local_execution_id')}")
    print(f"External Session ID: {disp.input_ref.get('external_session_id')}")
    print(f"Output preview: {disp.input_ref.get('output', '')[:120]}")
    print(f"Duration: {disp.input_ref.get('duration_ms')} ms")

    events = canvas.get_events(owner, instance.id, cursor=0)
    print(f"Total canvas events recorded: {len(events)}")

    print("[5/5] Writing authentic trace to evidence/process-traces/06-canvas-hermes-roundtrip.json...")
    output_text = disp.input_ref.get("output", "")
    output_hash = hashlib.sha256(output_text.encode("utf-8")).hexdigest()

    trace_payload = {
        "title": "CanvasService → Hermes CLI 本地往返",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "canvas_instance_id": instance.id,
        "root_task_id": root_task.id,
        "subtask_id": disp.subtask_id,
        "local_execution_id": disp.input_ref.get("local_execution_id"),
        "external_session_id": disp.input_ref.get("external_session_id"),
        "agent": "Hermes",
        "agent_version": probe.get("version"),
        "goal": goal,
        "acceptance_criteria": "多智能体",
        "validation_passed": disp.input_ref.get("validation_passed"),
        "budget_slice_usd": 0.25,
        "estimated_cost_usd": disp.input_ref.get("estimated_cost_usd", 0.0),
        "cost_status": disp.input_ref.get("cost_status", "unknown"),
        "settled_cost_usd": (disp.input_ref.get("budget_settlement") or {}).get("settled_amount_usd"),
        "budget_settlement": disp.input_ref.get("budget_settlement"),
        "duration_ms": disp.input_ref.get("duration_ms"),
        "tokens": disp.input_ref.get("tokens"),
        "model": disp.input_ref.get("model"),
        "output": output_text,
        "output_sha256": output_hash,
        "state": disp.state,
        "stage_transitions": disp.input_ref.get("execution_trace", {}).get("stage_transitions", []),
        "timeline_events": events,
    }

    out_file = Path("evidence/process-traces/06-canvas-hermes-roundtrip.json")
    out_file.write_text(json.dumps(trace_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Successfully saved authentic trace to {out_file}!")


if __name__ == "__main__":
    main()
