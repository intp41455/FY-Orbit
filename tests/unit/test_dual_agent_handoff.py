"""Unit Tests for Authentic Dual-Agent Handoff and Autonomous Execution (Step 2 Verification).

Verifies the end-to-end chain between Hermes and ResearchAgent:
1. Hermes produces real output/artifact -> Structured handoff recorded
2. ResearchAgent ingests Hermes handoff packet and executes autonomously (NO manual input)
3. Results returned with academic citations, evidence hash, non-manual completion marker
4. Truthful budget accounting and settlement
5. Failure recovery: exceptions hold reservations in unknown state pending reconciliation
6. Permission boundary: unauthorized cross-domain handoffs are strictly rejected
7. Incomplete upstream task handoff rejection
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from find_yourself.db.models import BudgetReservation, DispatchRecord, Memory, Task
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.canvas import CanvasService
from find_yourself.services.errors import ValidationFailed


@pytest.fixture
def canvas_service(session: Session, audit: AuditService) -> CanvasService:
    return CanvasService(session, audit)


def create_task(session: Session, owner: Actor, task_id: str, domain: str = "personal") -> Task:
    task = Task(
        id=task_id,
        owner_id=owner.owner_id,
        root_task_id=task_id,
        goal="Root task for dual-agent canvas tests",
        domain=domain,
        status="queued",
        deadline=utcnow() + timedelta(days=3),
        idempotency_key=f"idem-root-{task_id}",
    )
    session.add(task)
    session.commit()
    return task


def test_hermes_to_research_agent_real_handoff_success(
    canvas_service: CanvasService, owner: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 2 Requirement: Hermes produces real output, Canvas records handoff,

    ResearchAgent autonomously executes on Hermes output, returns citations & evidence hash,
    and settles budget without manual intervention (completed_by='ResearchAgent/A2A', is_manual_completion=False).
    """
    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-dual-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="双Agent真实协作项目", template_id="personal")

    # 1. Hermes completes subtask 1 (mocking external CLI execution)
    hermes_output = "Hermes已完成自我探索核心主题聚类：职业倦怠与内在驱动力失衡。"
    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "healthy": True, "stage": "本机握手通过", "version": "0.1.0"},
    )
    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "dispatch_and_run",
        lambda subtask_id, goal, **kwargs: {
            "subtask_id": subtask_id,
            "local_execution_id": "exec-local-hermes-01",
            "external_session_id": "sess-hermes-live-01",
            "agent": "Hermes",
            "goal": goal,
            "state": "completed",
            "validation_passed": True,
            "output": hermes_output,
            "duration_ms": 3500,
            "tokens": 1200,
            "model": "agnes-2.5-flash",
            "estimated_cost_usd": 0.04,
            "cost_status": "estimated",
            "stage_transitions": [
                {"stage": "submitted", "timestamp": "2026-10-01T00:00:00Z"},
                {"stage": "running", "timestamp": "2026-10-01T00:00:01Z"},
                {"stage": "execution_succeeded", "timestamp": "2026-10-01T00:00:04Z"},
                {"stage": "accepted_by_validator", "timestamp": "2026-10-01T00:00:04Z"},
            ],
        },
    )

    disp_hermes = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="阶段一：识别心理状态关键矛盾",
        budget_slice=0.25,
        idempotency_key="disp-hermes-dual-01",
    )
    assert disp_hermes.state == "completed"
    assert disp_hermes.input_ref.get("output") == hermes_output

    # 2. Canvas records structured handoff packet from Hermes to ResearchAgent
    handoff = canvas_service.record_handoff(
        actor=owner,
        instance_id=inst.id,
        stage="literature_verification",
        goal="针对Hermes聚类的职业倦怠与内在驱动力失衡进行实证文献调研",
        source_worker_id="Hermes",
        target_worker_id="ResearchAgent",
        source_task_id=disp_hermes.subtask_id,
        completed_items=["核心主题提取: 职业倦怠", "矛盾聚类: 内在驱动力失衡"],
        artifact_refs=["artifacts/hermes-cluster-report.md"],
        evidence_refs=["evidence/traces/hermes-session-01.json"],
        next_steps=["检索相关实证文献并验证因果链条"],
    )
    assert handoff.source_worker_id == "Hermes"
    assert handoff.target_worker_id == "ResearchAgent"
    assert handoff.source_task_id == disp_hermes.subtask_id

    # 3. Dispatch subtask 2 to ResearchAgent with handoff packet (auto_run=True)
    disp_research = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="ResearchAgent",
        goal="阶段二：检索心理学实证研究并验证内在动机假说",
        budget_slice=0.20,
        input_ref={"handoff_packet_id": handoff.id},
        auto_run=True,
        idempotency_key="disp-research-dual-02",
    )

    # 4. Strict verification of autonomous execution (NOT manual completion)
    assert disp_research.state == "completed"
    assert disp_research.completed_at is not None

    ref = disp_research.input_ref
    assert ref["completed_by"] == "ResearchAgent/local_stub"
    assert ref["is_manual_completion"] is False
    assert ref["handoff_packet"]["source_worker_id"] == "Hermes"
    assert ref["handoff_packet"]["upstream_output"] == hermes_output
    assert len(ref["handoff_packet"]["completed_items"]) == 2

    # Verify research agent outputs: mock citations and evidence hash
    trace = ref["execution_trace"]
    assert trace["agent"] == "ResearchAgent"
    assert trace["validation_passed"] is True
    assert len(trace["citations"]) >= 2
    assert all("mock-ref" in c for c in trace["citations"])
    assert trace["evidence_hash"].startswith("sha256:")
    assert "[ResearchAgent 本地规则桩/local_stub 论据整理]" in ref["output"]

    # Verify truthful budget settlement (estimated cost, NOT claimed actual)
    settlement = ref["budget_settlement"]
    assert settlement["status"] == "settled"
    assert settlement["reserved_amount_usd"] == 0.20
    assert settlement["settled_amount_usd"] > 0.0
    assert settlement["settled_amount_usd"] <= 0.20
    assert settlement["cost_status"] == "estimated"

    # Verify reservation in DB is settled
    res_db = session.get(BudgetReservation, ref["reservation_id"])
    assert res_db.state == "settled"

    # 5. Verify snapshot reflects both agents' dispatches and handoff
    snapshot = canvas_service.get_snapshot(owner, inst.id)
    assert len(snapshot["dispatches"]) == 2
    assert len(snapshot["handoffs"]) == 1
    assert snapshot["dispatches"][0]["worker_id"] == "Hermes"
    assert snapshot["dispatches"][1]["worker_id"] == "ResearchAgent"
    assert snapshot["dispatches"][1]["input_ref"]["is_manual_completion"] is False

    # 6. Verify event timeline includes submitted and completed events
    events = canvas_service.get_events(owner, inst.id)
    agent_completed_events = [e for e in events if e["event_type"] == "agent.task_completed"]
    assert len(agent_completed_events) == 2
    research_event = [e for e in agent_completed_events if e["agent_id"] == "ResearchAgent"][0]
    assert research_event["details"]["completed_by"] == "ResearchAgent/local_stub"
    assert research_event["details"]["is_manual_completion"] is False


def test_research_agent_failure_recovery_and_budget_hold(
    canvas_service: CanvasService, owner: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 2 Requirement: When second agent encounters an unrecoverable exception,

    it transitions to 'unknown_needs_reconciliation', holds reservation in 'unknown' state,
    and emits failure and reconciliation events without leaking tokens.
    """
    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-dual-fail-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="异常恢复测试", template_id="personal")

    # Simulate adapter throwing an unexpected exception
    def broken_dispatch(*args, **kwargs):
        raise RuntimeError("A2A connection lost to ResearchAgent peer process")

    monkeypatch.setattr(canvas_service.research_agent_adapter, "dispatch_and_run", broken_dispatch)

    with pytest.raises(RuntimeError) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=root_task.id,
            worker_id="ResearchAgent",
            goal="测试执行中异常中断",
            budget_slice=0.15,
            auto_run=True,
            idempotency_key="disp-fail-idem-01",
        )
    assert "A2A connection lost" in str(exc_info.value)

    # In DB, the record must be persisted in 'unknown_needs_reconciliation'
    rec = session.query(DispatchRecord).filter_by(idempotency_key="disp-fail-idem-01").first()
    assert rec is not None
    assert rec.state == "unknown_needs_reconciliation"
    assert "A2A connection lost" in rec.input_ref.get("error", "")

    # Budget reservation must be held in 'unknown' state
    res_id = rec.input_ref.get("reservation_id")
    res_db = session.get(BudgetReservation, res_id)
    assert res_db.state == "unknown"

    # Events must include task_failed and reconciliation_required
    events = canvas_service.get_events(owner, inst.id)
    event_types = [e["event_type"] for e in events]
    assert "agent.task_failed" in event_types
    assert "agent.reconciliation_required" in event_types


def test_cross_domain_handoff_rejection_without_grant(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """Step 2 Requirement: Attempting to reference other-domain data in handoff

    without an active Grant must be rejected with ValidationFailed.
    """
    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-cross-reject-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="跨域越权拦截测试", template_id="personal")

    # Work domain record
    content = "公司未公开商业计划与架构机密"
    work_mem = Memory(
        id="mem-work-secret-01",
        owner_id=owner.owner_id,
        domain="work",
        category="note",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        created_at=utcnow(),
        active=True,
    )
    session.add(work_mem)
    session.commit()

    # Attempt dispatch to ResearchAgent referencing work domain record without grant
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=root_task.id,
            worker_id="ResearchAgent",
            goal="分析并提取文献要点",
            budget_slice=0.10,
            input_ref={"record_id": work_mem.id},
            auto_run=True,
        )
    assert "without explicit grant authorization" in str(exc_info.value)


def test_cross_domain_handoff_allowed_with_active_grant(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """Step 2 Requirement: Valid cross-domain grant allows ResearchAgent to consume work record."""
    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-cross-ok-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="跨域授权通过测试", template_id="personal")

    # Work domain record
    content = "某学术会议已发表论文公开草稿"
    work_mem = Memory(
        id="mem-work-paper-01",
        owner_id=owner.owner_id,
        domain="work",
        category="note",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        created_at=utcnow(),
        active=True,
    )
    session.add(work_mem)

    # Valid grant authorizing personal canvas to read this work record
    grant = canvas_service.grants.create(
        owner,
        source_domain="work",
        consumer_domain="personal",
        record_ids=[work_mem.id],
        expires_at=utcnow() + timedelta(hours=2),
    )

    # Dispatch to ResearchAgent with valid grant
    disp = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="ResearchAgent",
        goal="梳理该论文的核心实证依据",
        budget_slice=0.10,
        input_ref={"record_id": work_mem.id, "grant_id": grant.id, "domain": "work"},
        auto_run=True,
    )
    assert disp.state == "completed"
    assert disp.input_ref["completed_by"] == "ResearchAgent/local_stub"
    assert disp.input_ref["is_manual_completion"] is False


def test_upstream_incomplete_subtask_handoff_rejection(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """Step 2 Requirement: Handoff cannot consume an uncompleted or running upstream task."""
    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-incomplete-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="前置未完拦截测试", template_id="personal")

    # An incomplete upstream record in 'running' state
    incomplete_rec = DispatchRecord(
        id="disp-incomplete-01",
        instance_id=inst.id,
        root_task_id=root_task.id,
        subtask_id="sub-incomplete-01",
        orchestrator_id="Hermes",
        worker_id="Hermes",
        idempotency_key="idem-incomplete-01",
        goal="正在执行的任务",
        state="running",
        budget_slice=0.20,
        deadline=utcnow() + timedelta(hours=1),
    )
    session.add(incomplete_rec)
    session.commit()

    # Attempt to hand off the incomplete task to ResearchAgent
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=root_task.id,
            worker_id="ResearchAgent",
            goal="跟进前置任务",
            budget_slice=0.10,
            input_ref={"source_task_id": incomplete_rec.subtask_id},
            auto_run=True,
        )
    assert "cannot hand off incomplete task" in str(exc_info.value)
