"""Unit tests for CanvasService (05 多Agent协作可视化画布)."""

from __future__ import annotations

from datetime import timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from find_yourself.db.models import Base, Task
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.canvas import CanvasService
from find_yourself.services.errors import ValidationFailed, NotFound


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
    """Threshold 4: Reject cross-domain data reading without explicit grant."""
    root_task = create_task(canvas_service.session, owner, task_id="task-domain-01", domain="work")
    inst_work = canvas_service.create_instance(owner, project_name="跨域检测", template_id="work")

    # Work instance accessing personal data without grant -> rejected
    with pytest.raises(ValidationFailed) as exc_info:
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst_work.id,
            root_task_id=root_task.id,
            worker_id="EngineeringAgent",
            goal="尝试读取私人敏感数据",
            input_ref={"domain": "personal", "ref": "private-notes.txt"},
        )
    assert "Work canvas cannot ingest personal domain data" in str(exc_info.value)

    # Accessing with explicit grant -> succeeds
    rec = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst_work.id,
        root_task_id=root_task.id,
        worker_id="EngineeringAgent",
        goal="授权读取私人数据",
        input_ref={"domain": "personal", "grant_id": "grant-123"},
    )
    assert rec.state == "dispatched"


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
