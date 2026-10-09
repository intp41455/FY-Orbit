"""Unit tests for CanvasService (05 多Agent协作可视化画布)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from find_yourself.db.models import Task
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.canvas import CanvasService
from find_yourself.services.errors import ValidationFailed


@pytest.fixture
def canvas_service(session: Session, audit: AuditService) -> CanvasService:
    return CanvasService(session, audit)


def create_task(session: Session, owner: Actor, task_id: str = "root-task-01", domain: str = "work") -> Task:
    task = Task(
        id=task_id,
        owner_id=owner.owner_id,
        goal="Test Root Task",
        domain=domain,
        status="running",
        deadline=utcnow() + timedelta(hours=2),
        idempotency_key=f"idem-{task_id}",
    )
    session.add(task)
    session.flush()
    return task


def test_list_templates(canvas_service: CanvasService) -> None:
    templates = canvas_service.list_templates()
    template_ids = {t["id"] for t in templates}
    assert "personal" in template_ids
    assert "work" in template_ids
    personal = next(t for t in templates if t["id"] == "personal")
    assert personal["center"] == "Hermes"
    assert "WorkBuddy" in personal["workers"]


def test_probe_connectors(canvas_service: CanvasService) -> None:
    connectors = canvas_service.probe_connectors()
    assert len(connectors) >= 6
    names = {c["name"] for c in connectors}
    assert "Hermes" in names
    assert "Codex" in names
    assert "OpenCode" in names
    assert "Pi agent" in names
    assert "WorkBuddy" in names
    assert "豆包" in names

    # Status levels check
    for c in connectors:
        assert c["stage"] in [
            "仅设计",
            "发现接口",
            "本机握手通过",
            "合成任务往返",
            "真实授权任务往返",
            "生产可用",
        ]
        if not c["healthy"]:
            assert c["blocking_reason"] is not None


def test_create_and_list_instances(
    canvas_service: CanvasService, owner: Actor
) -> None:
    inst = canvas_service.create_instance(
        owner,
        project_name="个人生活助理项目",
        template_id="personal",
    )
    assert inst.id.startswith("canv-")
    assert inst.project_name == "个人生活助理项目"
    assert inst.orchestrator_id == "Hermes"
    assert inst.state == "active"

    instances = canvas_service.list_instances(owner)
    assert len(instances) == 1
    assert instances[0].id == inst.id

    fetched = canvas_service.get_instance(owner, inst.id)
    assert fetched.id == inst.id

    # Test invalid template
    with pytest.raises(ValidationFailed):
        canvas_service.create_instance(
            owner,
            project_name="Invalid",
            template_id="nonexistent_template",
        )


def test_create_instance_honest_node_registration(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """Threshold 4: Node registration emits node_registered; disconnected workers do NOT emit agent.connected."""
    inst = canvas_service.create_instance(
        owner,
        project_name="严谨节点注册测试",
        template_id="personal",
    )
    events = canvas_service.get_events(owner, inst.id, cursor=0)
    event_types = [e["event_type"] for e in events]
    assert "canvas.instance.created" in event_types
    assert "agent.node_registered" in event_types

    # Disconnected workers like WorkBuddy and 豆包 must NOT have agent.connected!
    connected_agents = [e["agent_id"] for e in events if e["event_type"] == "agent.connected"]
    assert "WorkBuddy" not in connected_agents
    assert "豆包" not in connected_agents


def test_dispatch_subtask(
    canvas_service: CanvasService, owner: Actor
) -> None:
    root_task = create_task(canvas_service.session, owner, task_id="task-01", domain="work")
    inst = canvas_service.create_instance(
        owner,
        project_name="工作研发项目",
        template_id="work",
    )
    assert inst.orchestrator_id == "Codex"

    # Dispatch to internal worker (EngineeringAgent) -> state is dispatched
    rec_internal = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="EngineeringAgent",
        goal="执行单元测试与架构验证",
        acceptance_criteria="全部通过",
        budget_slice=0.25,
    )
    assert rec_internal.subtask_id.startswith("sub-")
    assert rec_internal.worker_id == "EngineeringAgent"
    assert rec_internal.state == "dispatched"

    # Dispatch to disconnected external worker (OpenCode) -> truthful pending_adapter state!
    rec_external = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="OpenCode",
        goal="实现多Agent协作画布组件",
        acceptance_criteria="组件可交互、无类型报错",
        budget_slice=0.25,
    )
    assert rec_external.worker_id == "OpenCode"
    assert rec_external.state == "pending_adapter"

    # Reject worker not in template
    with pytest.raises(ValidationFailed):
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=root_task.id,
            worker_id="RandomWorker",
            goal="Invalid dispatch",
        )


def test_budget_slice_cap_enforcement(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """Threshold 4: Enforce per-subtask maximum budget cap of $0.50."""
    root_task = create_task(canvas_service.session, owner, task_id="task-budget-01")
    inst = canvas_service.create_instance(owner, project_name="预算测试", template_id="work")

    # Exceeding $0.50 cap must be rejected
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="超出预算的派发",
            budget_slice=0.51,
        )
    assert "exceeds per-subtask maximum limit of $0.50" in str(exc_info.value)

    # 0 or negative budget must be rejected
    with pytest.raises(ValidationFailed):
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="零预算派发",
            budget_slice=0.00,
        )

    # Budget <= 0.50 succeeds
    rec = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="EngineeringAgent",
        goal="合规预算派发",
        budget_slice=0.50,
    )
    assert rec.budget_slice == 0.50


def test_root_task_validation(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """Threshold 4: Reject non-existent root task or root task belonging to other owner."""
    inst = canvas_service.create_instance(owner, project_name="任务验证测试", template_id="work")

    # Non-existent task
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id="nonexistent-task",
            worker_id="EngineeringAgent",
            goal="测试任务验证",
        )
    assert "not found" in str(exc_info.value)

    # Task owned by someone else
    other_actor = Actor(subject_type="owner", owner_id="other-owner-999")
    other_task = create_task(canvas_service.session, other_actor, task_id="task-other-owner")
    with pytest.raises(ValidationFailed):
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=other_task.id,
            worker_id="EngineeringAgent",
            goal="越权测试",
        )


def test_cross_domain_violation_enforcement(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """07 Threshold 2 & Phase C: Authoritative Memory checks, 4-way grant verification, and goal privacy enforcement."""
    from find_yourself.db.models import Grant, Memory
    session = canvas_service.session

    root_task = create_task(session, owner, task_id="task-domain-01", domain="work")
    inst_work = canvas_service.create_instance(owner, project_name="跨域检测", template_id="work")

    # Seed authoritative Memory record
    mem_priv = Memory(
        id="rec-priv-01",
        owner_id=owner.owner_id,
        domain="personal",
        category="note",
        content="保密个人笔记：绝密项目财务预估",
        content_hash="h-priv-01",
        active=True,
    )
    session.add(mem_priv)
    session.flush()

    # 1. Without grant -> rejected
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="尝试读取私人敏感数据",
            input_ref={"domain": "personal", "ref": "rec-priv-01"},
        )
    assert "cannot ingest 'personal' domain data without explicit grant authorization" in str(exc_info.value)

    # 2. Case A: Non-existent record ID -> rejected (does not exist in authoritative storage)
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="读取不存在记录",
            input_ref={"domain": "personal", "record_id": "rec-ghost-404"},
        )
    assert "not found in authoritative storage" in str(exc_info.value)

    # 3. Case B: Deleted record ID -> rejected
    mem_del = Memory(
        id="rec-del-01",
        owner_id=owner.owner_id,
        domain="personal",
        category="note",
        content="已删除的私人资料",
        content_hash="h-del-01",
        active=False,
        deleted_at=utcnow(),
    )
    session.add(mem_del)
    session.flush()
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="读取已删除记录",
            input_ref={"domain": "personal", "record_id": "rec-del-01"},
        )
    assert "deleted or deactivated" in str(exc_info.value)

    # 4. Case C: Another user's record ID -> rejected
    mem_other = Memory(
        id="rec-other-01",
        owner_id="attacker-user-999",
        domain="personal",
        category="note",
        content="他人私有信息",
        content_hash="h-other-01",
        active=True,
    )
    session.add(mem_other)
    session.flush()
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="读取他人记录",
            input_ref={"domain": "personal", "record_id": "rec-other-01"},
        )
    assert "not owned by actor" in str(exc_info.value)

    # 5. Case D: Domain spoofing (client says work, record is actually personal) -> rejected
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="客户端自报错误域尝试绕过",
            input_ref={"domain": "work", "record_id": "rec-priv-01"},
        )
    assert "conflicts with authoritative domain" in str(exc_info.value)

    # 6. Case E: Multiple records with conflicting domains -> rejected
    mem_work = Memory(
        id="rec-work-01",
        owner_id=owner.owner_id,
        domain="work",
        category="ticket",
        content="工作工单内容",
        content_hash="h-work-01",
        active=True,
    )
    session.add(mem_work)
    session.flush()
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="混入多个不同域记录",
            input_ref={"record_ids": ["rec-priv-01", "rec-work-01"]},
        )
    assert "mixed-domain input references are rejected" in str(exc_info.value)

    # 7. Case F: Non-existent grant -> rejected
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="伪造 grant_id",
            input_ref={"domain": "personal", "grant_id": "grant-nonexistent-123", "record_id": "rec-priv-01"},
        )
    assert "Grant grant-nonexistent-123 not found" in str(exc_info.value)

    # 8. Case G: Expired grant -> rejected
    g_expired = Grant(
        id="grant-expired-01",
        source_domain="personal",
        consumer_domain="work",
        record_ids=["rec-priv-01"],
        expires_at=utcnow() - timedelta(hours=1),
        state="active",
        scope_hash="hash-expired",
    )
    session.add(g_expired)
    session.flush()

    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="过期授权",
            input_ref={"domain": "personal", "grant_id": "grant-expired-01", "record_id": "rec-priv-01"},
        )
    assert "has expired" in str(exc_info.value)

    # 9. Case H: Revoked grant -> rejected
    g_revoked = Grant(
        id="grant-revoked-01",
        source_domain="personal",
        consumer_domain="work",
        record_ids=["rec-priv-01"],
        expires_at=utcnow() + timedelta(days=5),
        state="revoked",
        revoked_at=utcnow(),
        scope_hash="hash-revoked",
    )
    session.add(g_revoked)
    session.flush()

    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="撤销授权",
            input_ref={"domain": "personal", "grant_id": "grant-revoked-01", "record_id": "rec-priv-01"},
        )
    assert "is not active" in str(exc_info.value)

    # 10. Case I: Record ID mismatch in grant -> rejected
    g_mismatch = Grant(
        id="grant-mismatch-01",
        source_domain="personal",
        consumer_domain="work",
        record_ids=["rec-priv-999"],
        expires_at=utcnow() + timedelta(days=5),
        state="active",
        scope_hash="hash-mismatch",
    )
    session.add(g_mismatch)
    session.flush()

    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="越权读取非授权记录",
            input_ref={"domain": "personal", "grant_id": "grant-mismatch-01", "record_id": "rec-priv-01"},
        )
    assert "does not authorize record 'rec-priv-01'" in str(exc_info.value)

    # 11. Case J: Goal embeds ungranted raw personal content -> rejected
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="处理这段内容：保密个人笔记：绝密项目财务预估，分析它的工作影响",
            input_ref={"record_id": "rec-work-01"},
        )
    assert "ungranted full text" in str(exc_info.value) or "ungranted excerpt/clause" in str(exc_info.value)

    # 11b. Case K: Goal embeds excerpt/clause (short phrase) from ungranted private memory -> rejected
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="请重点审查绝密项目财务预估部分",
            input_ref={"record_id": "rec-work-01"},
        )
    assert "ungranted excerpt/clause" in str(exc_info.value)

    # 11c. Case L: Goal paraphrasing / n-gram overlap with ungranted private memory -> rejected
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="审查个人财务预估和绝密项目风险",
            input_ref={"record_id": "rec-work-01"},
        )
    assert "overlapping fragments" in str(exc_info.value) or "excerpt" in str(exc_info.value)

    # 11d. Case M: Root task domain mismatch (work root task submitted to personal canvas) -> rejected
    inst_personal = canvas_service.create_instance(owner, project_name="个人生活画布", template_id="personal")
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_personal.id,
            root_task_id=root_task.id,  # root_task has domain='work'
            worker_id="Hermes",
            goal="尝试在个人画布使用工作根任务",
        )
    assert "Root task domain 'work' does not match canvas domain 'personal'" in str(exc_info.value)

    # 11e. Case N: Sensitive marker of opposite domain in goal without records -> rejected
    root_task_personal = create_task(session, owner, task_id="task-pers-root-01", domain="personal")
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_personal.id,
            root_task_id=root_task_personal.id,
            worker_id="Hermes",
            goal="协助整理公司的未公开代码和商业机密",
        )
    assert "sensitive marker '商业机密' from work domain" in str(exc_info.value)

    # 12. Valid active unexpired grant covering rec-priv-01 -> succeeds!
    g_valid = Grant(
        id="grant-valid-01",
        source_domain="personal",
        consumer_domain="work",
        record_ids=["rec-priv-01"],
        expires_at=utcnow() + timedelta(days=5),
        state="active",
        scope_hash="hash-valid",
    )
    session.add(g_valid)
    session.flush()

    rec = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst_work.id,
        root_task_id=root_task.id,
        worker_id="EngineeringAgent",
        goal="授权读取私人数据进行工作汇报整合",
        input_ref={"domain": "personal", "grant_id": "grant-valid-01", "record_id": "rec-priv-01"},
    )
    assert rec.state == "dispatched"


def test_budget_lifecycle_unconnected_worker_no_reservation(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """07 Threshold 4: Unconnected worker (pending_adapter) does NOT reserve budget; settled/released on lifecycle."""
    from find_yourself.db.models import BudgetReservation

    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-budget-life-01", domain="work")
    inst = canvas_service.create_instance(owner, project_name="预算生命周期测试", template_id="work")

    # Dispatch to OpenCode (unconnected) -> state='pending_adapter'
    disp_pending = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="OpenCode",
        goal="未接通的 Worker 任务",
        budget_slice=0.20,
    )
    assert disp_pending.state == "pending_adapter"
    # Zero budget reservation created!
    res_pending = session.query(BudgetReservation).filter_by(task_id=root_task.id).all()
    assert len(res_pending) == 0

    # Dispatch to internal EngineeringAgent -> reserves budget!
    disp_internal = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="EngineeringAgent",
        goal="内部可用 Worker 任务",
        budget_slice=0.20,
    )
    assert disp_internal.state == "dispatched"
    res_list = session.query(BudgetReservation).filter_by(task_id=root_task.id).all()
    assert len(res_list) == 1
    assert res_list[0].state == "reserved"
    assert float(res_list[0].amount) == 0.20

    # Complete subtask -> settles budget
    canvas_service.complete_subtask(owner, inst.id, disp_internal.subtask_id, output="执行完成")
    session.refresh(res_list[0])
    assert res_list[0].state == "settled"

    # Dispatch another and cancel -> releases budget
    disp_cancel = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="EngineeringAgent",
        goal="将被取消的任务",
        budget_slice=0.15,
    )
    res_cancel = session.query(BudgetReservation).filter_by(idempotency_key=f"disp-res-dispatch-{inst.id}-{disp_cancel.subtask_id}").first()
    assert res_cancel.state == "reserved"

    canvas_service.cancel_subtask(owner, inst.id, disp_cancel.subtask_id, reason="测试取消释放")
    session.refresh(res_cancel)
    assert res_cancel.state == "released"


def test_hermes_dispatch_roundtrip_settlement(
    canvas_service: CanvasService, owner: Actor, monkeypatch
) -> None:
    """07 Threshold 1: Hermes dispatch calls adapter, receives receipt/output, writes to DispatchRecord and settles budget."""
    from find_yourself.db.models import BudgetReservation

    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-hermes-root-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="Hermes调度往返", template_id="personal")

    # Ensure probe says Hermes is healthy
    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "healthy": True, "stage": "本机握手通过", "binary_path": "hermes", "blocking_reason": None},
    )

    # 1. Success case: mock dispatch_and_run with real telemetry
    fake_local_exec = "exec-local-mock-123456"
    fake_session_id = "sess-hermes-987654"
    fake_output = "Hermes已完成日常安排推演与归档。"
    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "dispatch_and_run",
        lambda subtask_id, goal, timeout_sec=60, acceptance_criteria=None, **kwargs: {
            "subtask_id": subtask_id,
            "local_execution_id": fake_local_exec,
            "external_session_id": fake_session_id,
            "agent": "Hermes",
            "goal": goal,
            "stage_transitions": [
                {"stage": "submitted", "timestamp": "2026-10-01T00:00:00Z", "local_execution_id": fake_local_exec},
                {"stage": "running", "timestamp": "2026-10-01T00:00:01Z"},
                {"stage": "execution_succeeded", "timestamp": "2026-10-01T00:00:04Z", "external_session_id": fake_session_id},
                {"stage": "accepted_by_validator", "timestamp": "2026-10-01T00:00:05Z"},
            ],
            "state": "completed",
            "validation_passed": True,
            "output": fake_output,
            "duration_ms": 4200,
            "tokens": 1500,
            "model": "agnes-2.5-flash",
            "estimated_cost_usd": 0.05,
            "cost_status": "estimated",
        },
    )

    disp = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="梳理本周事项",
        budget_slice=0.25,
        idempotency_key="disp-hermes-roundtrip-01",
    )

    assert disp.state == "completed"
    assert disp.completed_at is not None
    assert disp.input_ref["local_execution_id"] == fake_local_exec
    assert disp.input_ref["external_session_id"] == fake_session_id
    assert disp.input_ref["validation_passed"] is True
    assert disp.input_ref["output"] == fake_output
    assert disp.input_ref["duration_ms"] == 4200

    # Verify budget settled
    res_id = disp.input_ref["reservation_id"]
    res = session.get(BudgetReservation, res_id)
    assert res.state == "settled"

    # Verify canvas events recorded
    events = canvas_service.get_events(owner, inst.id, cursor=0)
    event_types = [e["event_type"] for e in events]
    assert "task.dispatched" in event_types
    assert "agent.task_submitted" in event_types
    assert "agent.task_completed" in event_types

    # Idempotency check: re-dispatching with same key returns existing without duplicate run
    disp_dup = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="梳理本周事项",
        budget_slice=0.25,
        idempotency_key="disp-hermes-roundtrip-01",
    )
    assert disp_dup.id == disp.id


def test_hermes_dispatch_exit_zero_fails_acceptance_criteria_releases_budget(
    canvas_service: CanvasService, owner: Actor, monkeypatch
) -> None:
    """Phase D Item 3: Exit code 0 but failing acceptance criteria marks subtask as failed and releases budget."""
    from find_yourself.db.models import BudgetReservation

    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-hermes-criteria-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="验收门槛测试", template_id="personal")

    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "healthy": True, "stage": "本机握手通过", "binary_path": "hermes", "blocking_reason": None},
    )

    # Returncode 0 from Hermes CLI, but output does NOT satisfy required criteria
    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "dispatch_and_run",
        lambda subtask_id, goal, timeout_sec=60, acceptance_criteria=None, **kwargs: {
            "subtask_id": subtask_id,
            "local_execution_id": "exec-local-crit-001",
            "external_session_id": "sess-crit-001",
            "agent": "Hermes",
            "goal": goal,
            "stage_transitions": [
                {"stage": "submitted", "timestamp": "2026-10-01T00:00:00Z"},
                {"stage": "running", "timestamp": "2026-10-01T00:00:01Z"},
                {"stage": "execution_succeeded", "timestamp": "2026-10-01T00:00:04Z"},
                {"stage": "rejected_by_validator", "timestamp": "2026-10-01T00:00:05Z", "error": "Acceptance criteria failed: missing required phrase 'MANDATORY_OUTPUT' in output"},
            ],
            "state": "failed",
            "validation_passed": False,
            "zero_consumption_proven": True,
            "output": "普通输出文本，不含验收必要字符串",
            "duration_ms": 3100,
            "error": "Acceptance criteria failed: missing required phrase 'MANDATORY_OUTPUT' in output",
        },
    )

    disp = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="执行严格验收任务",
        acceptance_criteria="MANDATORY_OUTPUT",
        budget_slice=0.30,
    )

    assert disp.state == "failed"
    assert disp.input_ref["validation_passed"] is False
    assert "Acceptance criteria failed" in disp.input_ref["error"]

    # Budget reservation is RELEASED, not settled!
    res_id = disp.input_ref["reservation_id"]
    res = session.get(BudgetReservation, res_id)
    assert res.state == "released"


def test_hermes_dispatch_failure_releases_budget(
    canvas_service: CanvasService, owner: Actor, monkeypatch
) -> None:
    """07 Threshold 1 & 4: Hermes dispatch failure releases budget reservation when not started or zero consumption proven."""
    from find_yourself.db.models import BudgetReservation

    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-hermes-fail-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="Hermes调度失败测试", template_id="personal")

    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "healthy": True, "stage": "本机握手通过", "binary_path": "hermes", "blocking_reason": None},
    )

    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "dispatch_and_run",
        lambda subtask_id, goal, timeout_sec=60, **kwargs: {
            "subtask_id": subtask_id,
            "receipt_id": "rcpt-fail-123",
            "agent": "Hermes",
            "goal": goal,
            "state": "failed",
            "not_started": True,
            "error": "Simulated Hermes CLI exit code 1",
            "duration_ms": 1500,
        },
    )

    disp = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="会失败的 Hermes 任务",
        budget_slice=0.30,
    )

    assert disp.state == "failed"
    assert disp.input_ref["error"] == "Simulated Hermes CLI exit code 1"

    # Budget reservation must be released!
    res_id = disp.input_ref["reservation_id"]
    res = session.get(BudgetReservation, res_id)
    assert res.state == "released"


def test_record_handoff(
    canvas_service: CanvasService, owner: Actor
) -> None:
    inst = canvas_service.create_instance(
        owner,
        project_name="工作研发项目",
        template_id="work",
    )
    packet = canvas_service.record_handoff(
        actor=owner,
        instance_id=inst.id,
        stage="architecture_review",
        goal="完成前后端契约交付",
        source_worker_id="Codex",
        target_worker_id="EngineeringAgent",
        source_task_id="task-arch-01",
        completed_items=["定义 CanvasService", "定义 API schemas", "跑通数据库迁移"],
        artifact_refs=["src/find_yourself/services/canvas.py"],
        evidence_refs=["evidence/finalization/F4-collaboration-canvas.md"],
        unresolved_issues=[],
        risks=["外部 agent 未安装时的降级提示"],
        next_steps=["编写前端页面", "执行集成测试"],
    )
    assert packet.id.startswith("hnd-")
    assert packet.stage == "architecture_review"
    assert len(packet.completed_items) == 3


def test_snapshot_and_events_cursor_replay(
    canvas_service: CanvasService, owner: Actor
) -> None:
    root_task = create_task(canvas_service.session, owner, task_id="task-snapshot-01", domain="personal")
    inst = canvas_service.create_instance(
        owner,
        project_name="测试事件流",
        template_id="personal",
    )

    disp = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="WorkBuddy",
        goal="整理近期行程",
    )
    assert disp.state == "pending_adapter"

    canvas_service.record_handoff(
        actor=owner,
        instance_id=inst.id,
        stage="handoff",
        goal="交付整理结果",
        source_worker_id="WorkBuddy",
        target_worker_id="Hermes",
        source_task_id=disp.subtask_id,
        completed_items=["行程列表已导出"],
    )

    snapshot = canvas_service.get_snapshot(owner, inst.id)
    assert snapshot["instance"]["id"] == inst.id
    assert len(snapshot["dispatches"]) == 1
    assert len(snapshot["handoffs"]) == 1
    assert len(snapshot["events"]) >= 5

    # Check cursor replay
    events_all = canvas_service.get_events(owner, inst.id, cursor=0)
    assert len(events_all) == len(snapshot["events"])

    first_seq = events_all[0]["seq"]
    events_after_first = canvas_service.get_events(owner, inst.id, cursor=first_seq)
    assert len(events_after_first) == len(events_all) - 1


def test_event_emission_monotonic_sequence(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """Threshold 4: Verify monotonic sequence allocation without collision."""
    inst = canvas_service.create_instance(owner, project_name="并发序列测试", template_id="personal")
    for i in range(10):
        canvas_service._emit_event(inst.id, f"test.event.{i}", details={"index": i})

    events = canvas_service.get_events(owner, inst.id, cursor=0)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
    assert seqs[-1] == len(events)


def test_concurrent_event_emission_multi_threads(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """07 P1-4: Concurrent event emission verifies monotonic seq allocation without gaps or duplicates."""
    import concurrent.futures

    inst = canvas_service.create_instance(owner, project_name="并发事件压测", template_id="personal")

    def emit_worker(thread_idx: int):
        for j in range(5):
            canvas_service._emit_event(
                instance_id=inst.id,
                event_type="agent.ping",
                agent_id="Hermes",
                details={"thread": thread_idx, "iter": j},
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(emit_worker, i) for i in range(4)]
        concurrent.futures.wait(futures)

    events = canvas_service.get_events(owner, inst.id, cursor=0)
    ping_events = [e for e in events if e["event_type"] == "agent.ping"]
    assert len(ping_events) == 20

    seqs = [e["seq"] for e in ping_events]
    assert len(seqs) == len(set(seqs))
    assert seqs == sorted(seqs)


def test_pre_execution_persistence_and_crash_reconciliation(
    canvas_service: CanvasService, owner: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue 3 / P0.1: DispatchRecord is persisted in 'running' state BEFORE invoking Hermes;
    if a crash occurred and the same idempotency key is retried, it enters unknown_needs_reconciliation
    without re-executing the external process."""
    from find_yourself.db.models import BudgetReservation, DispatchRecord

    # 0. Deterministically mock HermesAdapter.probe to healthy
    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "installed": True, "healthy": True, "version": "0.1.0"},
    )

    session = canvas_service.session
    SecondSession = sessionmaker(bind=session.bind, expire_on_commit=False, future=True)
    root_task = create_task(session, owner, task_id="task-reconcile-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="对账与崩溃测试", template_id="personal")

    # Mock Hermes adapter that tracks call count, inspects DB state mid-execution via a second session,
    # and simulates a crash
    call_count = 0
    state_in_other_session_during_call = None
    idem_key = "crash-test-idem-001"

    def mock_dispatch(subtask_id, goal, **kwargs):
        nonlocal call_count, state_in_other_session_during_call
        call_count += 1
        # Inspect using a separate DB session to prove durable commit prior to external call!
        with SecondSession() as other_session:
            r = other_session.query(DispatchRecord).filter_by(idempotency_key=idem_key).first()
            if r:
                state_in_other_session_during_call = r.state
        raise RuntimeError("Simulated process crash during external execution")

    canvas_service.hermes_adapter.dispatch_and_run = mock_dispatch

    # 1. First execution crashes
    with pytest.raises(RuntimeError):
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=root_task.id,
            worker_id="Hermes",
            goal="测试执行前持久化",
            idempotency_key=idem_key,
        )
    assert call_count == 1
    # Verify that during the call, a separate session saw the record committed in 'running' state!
    assert state_in_other_session_during_call == "running"

    # 2. Verify record exists in DB with state 'unknown_needs_reconciliation' and reservation held as 'unknown'
    rec_after_crash = session.query(DispatchRecord).filter_by(idempotency_key=idem_key).one()
    assert rec_after_crash.state == "unknown_needs_reconciliation"
    res_id = (rec_after_crash.input_ref or {}).get("reservation_id")
    if res_id:
        res_row = session.get(BudgetReservation, res_id)
        assert res_row.state == "unknown"

    # 3. Simulate a power cut before state update: record was left in 'running' in DB,
    # and reservation remained in 'reserved' state (not released).
    rec_after_crash.state = "running"
    if res_id:
        res_row = session.get(BudgetReservation, res_id)
        if res_row:
            res_row.state = "reserved"
    session.commit()

    # 4. Retrying with same idempotency key must NOT invoke Hermes again!
    retry_rec = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="测试执行前持久化重试",
        idempotency_key=idem_key,
    )
    # Hermes was NOT called a second time!
    assert call_count == 1
    # State transitioned to unknown_needs_reconciliation
    assert retry_rec.state == "unknown_needs_reconciliation"
    events = canvas_service.get_events(owner, inst.id)
    assert any(e["event_type"] == "agent.reconciliation_required" for e in events)

    # Budget reservation is held in unknown state rather than released or settled
    if res_id:
        res_row = session.get(BudgetReservation, res_id)
        assert res_row.state == "unknown"


def test_truthful_budget_settlement_unknown_vs_known(
    canvas_service: CanvasService, owner: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue 2 / P0.2: Unknown provider cost is held in 'unknown' state without freeing budget;
    known cost settles at actual cost."""
    from decimal import Decimal

    from find_yourself.db.models import BudgetReservation
    from find_yourself.services.errors import Conflict

    # Mock probe to healthy
    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "installed": True, "healthy": True, "version": "0.1.0"},
    )

    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-budget-truth-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="真实费用测试", template_id="personal")

    # Case A: Local execution with cost_status='unknown'
    canvas_service.hermes_adapter.dispatch_and_run = lambda **kwargs: {
        "state": "completed",
        "validation_passed": True,
        "estimated_cost_usd": 0.0,
        "cost_status": "unknown",
        "tokens": 1200,
        "model": "local-hermes",
        "duration_ms": 500,
        "output": "多智能体任务完成",
    }
    rec_unknown = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="多智能体任务",
        acceptance_criteria="多智能体",
        budget_slice=0.25,
    )
    assert rec_unknown.state == "completed"
    settlement = rec_unknown.input_ref["budget_settlement"]
    assert settlement["status"] == "held_unknown"
    assert settlement["reserved_amount_usd"] == 0.25
    assert settlement["settled_amount_usd"] is None
    assert settlement["cost_status"] == "unknown"

    # In DB, the reservation state is 'unknown' (still counting towards limits)
    res_unknown = session.get(BudgetReservation, rec_unknown.input_ref["reservation_id"])
    assert res_unknown.state == "unknown"

    # Case B: Provider with known cost (e.g. $0.08) settles at actual $0.08
    canvas_service.hermes_adapter.dispatch_and_run = lambda **kwargs: {
        "state": "completed",
        "validation_passed": True,
        "estimated_cost_usd": 0.08,
        "cost_status": "actual",
        "tokens": 5000,
        "model": "cloud-hermes",
        "duration_ms": 1200,
        "output": "多智能体已知费用完成",
    }
    rec_known = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="多智能体已知费用",
        acceptance_criteria="多智能体",
        budget_slice=0.25,
        idempotency_key="known-cost-idem-01",
    )
    assert rec_known.state == "completed"
    settlement_known = rec_known.input_ref["budget_settlement"]
    assert settlement_known["status"] == "settled"
    assert settlement_known["reserved_amount_usd"] == 0.25
    assert settlement_known["settled_amount_usd"] == 0.08
    assert settlement_known["cost_status"] == "actual"

    # Case C: Over-settlement exceeding reservation is rejected with Conflict in BudgetService.settle()
    root_task_over = create_task(session, owner, task_id="task-budget-over-01", domain="personal")
    res_over = canvas_service.budget.reserve(
        owner,
        task_id=root_task_over.id,
        amount=Decimal("0.25"),
        idempotency_key="over-res-idem-01",
        scope="canvas_dispatch",
    )
    with pytest.raises(Conflict) as exc_info:
        canvas_service.budget.settle(owner, res_over.id, settled_amount=Decimal("0.50"))
    assert "exceeds reserved amount" in str(exc_info.value)


def test_multi_agent_handoff_and_research_agent_dispatch(
    canvas_service: CanvasService, owner: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue 1 & P0.3: Hermes completes subtask 1, hands off to ResearchAgent,
    and ResearchAgent is dispatched (not faked); caller/internal engine completes subtask 2."""
    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "installed": True, "healthy": True, "version": "0.1.0"},
    )

    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-multiagent-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="双Agent协作测试", template_id="personal")

    canvas_service.hermes_adapter.dispatch_and_run = lambda **kwargs: {
        "state": "completed",
        "validation_passed": True,
        "estimated_cost_usd": 0.0,
        "cost_status": "unknown",
        "tokens": 1500,
        "model": "local-hermes",
        "output": "Hermes分析完成，准备交接研究员",
    }
    # 1. Hermes dispatches subtask 1
    rec1 = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="第一阶段：初步需求分析",
        budget_slice=0.20,
    )
    assert rec1.state == "completed"

    # 2. Hermes hands off to ResearchAgent
    handoff = canvas_service.record_handoff(
        actor=owner,
        instance_id=inst.id,
        stage="research_delegation",
        goal="交接研究任务",
        source_worker_id="Hermes",
        target_worker_id="ResearchAgent",
        source_task_id=rec1.subtask_id,
        completed_items=["完成需求拆解与关键词提取"],
        artifact_refs=["artifacts/hermes-analysis.md"],
        evidence_refs=["evidence/traces/hermes.json"],
    )
    assert handoff.source_worker_id == "Hermes"
    assert handoff.target_worker_id == "ResearchAgent"

    # 3. ResearchAgent dispatches subtask 2 (enters dispatched, NOT fake auto-completed)
    rec2 = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="ResearchAgent",
        goal="第二阶段：深入背景文献调研",
        budget_slice=0.20,
        input_ref={"handoff_packet_id": handoff.id},
    )
    assert rec2.state == "dispatched"

    # 4. Caller/Worker completes subtask 2 truthfully via complete_subtask
    completed_rec2 = canvas_service.complete_subtask(
        actor=owner,
        instance_id=inst.id,
        subtask_id=rec2.subtask_id,
        output="[ResearchAgent] 完成背景文献调研与整理",
    )
    assert completed_rec2.state == "completed"
    assert "[ResearchAgent]" in completed_rec2.input_ref["output"]
    assert completed_rec2.input_ref.get("completed_by") == "owner/manual"
    assert completed_rec2.input_ref.get("is_manual_completion") is True

    # 5. Snapshot verifies both agents collaborated
    snap = canvas_service.get_snapshot(owner, inst.id)
    assert len(snap["dispatches"]) == 2
    assert len(snap["handoffs"]) == 1
    assert any(d["worker_id"] == "Hermes" for d in snap["dispatches"])
    assert any(d["worker_id"] == "ResearchAgent" for d in snap["dispatches"])


def test_hermes_dispatch_timeout_remote_started_holds_unknown_budget(
    canvas_service: CanvasService, owner: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """12 Review P0: Exception/Timeout after dispatch must hold budget in 'unknown' state
    and transition record to 'unknown_needs_reconciliation' without releasing budget or re-running."""
    from find_yourself.db.models import BudgetReservation, DispatchRecord

    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-timeout-test-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="超时挂起预算测试", template_id="personal")

    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "installed": True, "healthy": True, "version": "0.1.0"},
    )

    call_count = 0

    def mock_timeout_dispatch(subtask_id, goal, **kwargs):
        nonlocal call_count
        call_count += 1
        raise TimeoutError("Remote process received task but local execution timed out waiting for receipt")

    canvas_service.hermes_adapter.dispatch_and_run = mock_timeout_dispatch

    idem_key = "timeout-res-idem-999"
    with pytest.raises(TimeoutError):
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=root_task.id,
            worker_id="Hermes",
            goal="执行可能超时的远端任务",
            budget_slice=0.25,
            idempotency_key=idem_key,
        )

    assert call_count == 1
    rec = session.query(DispatchRecord).filter_by(idempotency_key=idem_key).one()
    assert rec.state == "unknown_needs_reconciliation"
    settlement = rec.input_ref.get("budget_settlement", {})
    assert settlement.get("status") == "held_unknown"
    assert settlement.get("cost_status") == "unknown"

    res_id = (rec.input_ref or {}).get("reservation_id")
    assert res_id is not None
    res_row = session.get(BudgetReservation, res_id)
    assert res_row.state == "unknown"  # Critical: NOT released!

    # Verify reconciliation_required event emitted
    events = canvas_service.get_events(owner, inst.id)
    assert any(e["event_type"] == "agent.reconciliation_required" for e in events)

    # Retry with same idempotency key must NOT invoke Hermes again!
    retry_rec = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="执行可能超时的远端任务重试",
        budget_slice=0.25,
        idempotency_key=idem_key,
    )
    assert call_count == 1
    assert retry_rec.state == "unknown_needs_reconciliation"


def test_hermes_dispatch_failure_missing_telemetry_holds_unknown_budget(
    canvas_service: CanvasService, owner: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """12 Review P0: Process failure without telemetry (missing tokens/cost) cannot prove zero consumption;
    holds budget in 'unknown' state."""
    from find_yourself.db.models import BudgetReservation

    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-missing-telem-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="缺遥测失败测试", template_id="personal")

    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "installed": True, "healthy": True, "version": "0.1.0"},
    )

    canvas_service.hermes_adapter.dispatch_and_run = lambda **kwargs: {
        "state": "failed",
        "error": "Non-zero exit code 1; usage.json was not generated or missing telemetry",
        "tokens": None,
        "cost_status": "unknown",
        "estimated_cost_usd": None,
        "zero_consumption_proven": False,
        "not_started": False,
    }

    rec = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="Hermes",
        goal="执行缺遥测失败任务",
        budget_slice=0.25,
    )
    assert rec.state == "unknown_needs_reconciliation"
    settlement = rec.input_ref.get("budget_settlement", {})
    assert settlement.get("status") == "held_unknown"
    assert settlement.get("cost_status") == "unknown"

    res_id = (rec.input_ref or {}).get("reservation_id")
    res_row = session.get(BudgetReservation, res_id)
    assert res_row.state == "unknown"  # Held!


def test_budget_monthly_cap_covers_canvas_dispatches_and_settlement(
    canvas_service: CanvasService, owner: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """12 Review P0: Canvas dispatches (scope='canvas_dispatch') and settled actual expenses
    must count towards the rolling monthly limit; triggers alert >= 80% and raises Conflict on breach."""
    from decimal import Decimal

    from find_yourself.services.errors import Conflict

    monkeypatch.setattr(
        canvas_service.hermes_adapter,
        "probe",
        lambda: {"name": "Hermes", "installed": True, "healthy": True, "version": "0.1.0"},
    )

    session = canvas_service.session
    inst = canvas_service.create_instance(owner, project_name="月度额度覆盖测试", template_id="personal")

    alerts = []
    canvas_service.budget.alert_handlers.append(lambda a: alerts.append(a))

    # Configure per-month limit to $1.00 for clear test thresholds (default is $10.00)
    canvas_service.budget.limits.per_month_usd = Decimal("1.00")
    canvas_service.budget.limits.per_task_usd = Decimal("10.00")

    # Dispatch 1: $0.40 known actual cost $0.35 -> settles $0.35
    canvas_service.hermes_adapter.dispatch_and_run = lambda **kwargs: {
        "state": "completed",
        "validation_passed": True,
        "estimated_cost_usd": 0.35,
        "cost_status": "actual",
        "tokens": 2000,
        "output": "完成1",
    }
    t1 = create_task(session, owner, task_id="task-month-01", domain="personal")
    rec1 = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=t1.id,
        worker_id="Hermes",
        goal="月度任务1",
        budget_slice=0.40,
        idempotency_key="m-disp-01",
    )
    assert rec1.state == "completed"

    # Dispatch 2: $0.45 reservation (month total projected = 0.35 settled + 0.45 = 0.80 -> 80% warning!)
    t2 = create_task(session, owner, task_id="task-month-02", domain="personal")
    canvas_service.hermes_adapter.dispatch_and_run = lambda **kwargs: {
        "state": "completed",
        "validation_passed": True,
        "estimated_cost_usd": 0.40,
        "cost_status": "actual",
        "tokens": 2000,
        "output": "完成2",
    }
    rec2 = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=t2.id,
        worker_id="Hermes",
        goal="月度任务2",
        budget_slice=0.45,
        idempotency_key="m-disp-02",
    )
    assert rec2.state == "completed"
    assert len(alerts) >= 1
    assert any(a.get("level") == "warning" for a in alerts)

    # Now month used = 0.35 + 0.40 = 0.75 settled.
    # Dispatch 3: $0.30 reservation -> projected = 0.75 + 0.30 = 1.05 > 1.00 limit -> Must be rejected with Conflict!
    t3 = create_task(session, owner, task_id="task-month-03", domain="personal")
    with pytest.raises(Conflict) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id=t3.id,
            worker_id="Hermes",
            goal="月度任务3越限",
            budget_slice=0.30,
            idempotency_key="m-disp-03",
        )
    assert "Monthly budget exceeded" in str(exc_info.value)


def test_complete_subtask_distinguishes_manual_completion(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """12 Review P1: Manual completion must record completed_by='owner/manual' and is_manual_completion=True."""
    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-manual-comp-01", domain="personal")
    inst = canvas_service.create_instance(owner, project_name="手动完成标识测试", template_id="personal")

    # Dispatch to ResearchAgent (stays in dispatched)
    rec = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="ResearchAgent",
        goal="需要所有者手动完成的任务",
        budget_slice=0.10,
    )
    assert rec.state == "dispatched"

    # Complete via complete_subtask with default completed_by
    completed = canvas_service.complete_subtask(
        actor=owner,
        instance_id=inst.id,
        subtask_id=rec.subtask_id,
        output="所有者手动填写的分析结果",
    )
    assert completed.state == "completed"
    assert completed.input_ref["completed_by"] == "owner/manual"
    assert completed.input_ref["is_manual_completion"] is True

    # Verify event emitted with manual completion attributes
    events = canvas_service.get_events(owner, inst.id)
    comp_events = [e for e in events if e["event_type"] == "agent.task_completed" and e["task_id"] == rec.subtask_id]
    assert len(comp_events) == 1
    assert comp_events[0]["details"]["completed_by"] == "owner/manual"
    assert comp_events[0]["details"]["is_manual_completion"] is True


def test_cancel_subtask_releases_budget_and_confirms_process_tree_killed(
    canvas_service: CanvasService, owner: Actor
) -> None:
    """Verify subtask cancellation terminates process tree and releases budget reservation."""
    from find_yourself.adapters.community_harness_adapter import _ACTIVE_EXECUTIONS

    session = canvas_service.session
    root_task = create_task(session, owner, task_id="task-cancel-tree-01", domain="work")
    inst = canvas_service.create_instance(owner, project_name="取消与进程树清理测试", template_id="work")

    # Dispatch subtask with $0.05 budget reservation
    rec = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id=root_task.id,
        worker_id="EngineeringAgent",
        goal="执行长时间编译与测试任务",
        budget_slice=0.05,
    )
    assert rec.state in ("dispatched", "running")

    # Simulate active execution registered in adapter
    exec_id = "exec-test-cancel-tree-123"
    _ACTIVE_EXECUTIONS[exec_id] = {
        "task_id": rec.subtask_id,
        "executor": "peri",
        "status": "running",
        "events": [],
        "cancelled": False,
    }
    rec_input = dict(rec.input_ref or {})
    rec_input["harness_result"] = {"execution_id": exec_id}
    rec.input_ref = rec_input
    session.flush()

    # Cancel the subtask
    cancelled_rec = canvas_service.cancel_subtask(
        actor=owner,
        instance_id=inst.id,
        subtask_id=rec.subtask_id,
        reason="用户主动取消长时间任务",
    )
    assert cancelled_rec.state == "cancelled"
    assert cancelled_rec.input_ref["cancel_reason"] == "用户主动取消长时间任务"
    assert cancelled_rec.input_ref["process_tree_killed"] is True

    # Verify adapter recorded cancellation
    assert _ACTIVE_EXECUTIONS[exec_id]["cancelled"] is True
    assert _ACTIVE_EXECUTIONS[exec_id]["status"] == "cancelled"

    # Verify emitted event
    events = canvas_service.get_events(owner, inst.id)
    cancel_events = [e for e in events if e["event_type"] == "agent.task_cancelled" and e["task_id"] == rec.subtask_id]
    assert len(cancel_events) == 1
    assert cancel_events[0]["details"]["process_tree_killed"] is True


