"""Unit tests for CanvasService (05 多Agent协作可视化画布)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from find_yourself.db.models import Base
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.canvas import CanvasService
from find_yourself.services.errors import ValidationFailed, NotFound


@pytest.fixture
def canvas_service(session: Session, audit: AuditService) -> CanvasService:
    return CanvasService(session, audit)


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


def test_dispatch_subtask(
    canvas_service: CanvasService, owner: Actor
) -> None:
    inst = canvas_service.create_instance(
        owner,
        project_name="工作研发项目",
        template_id="work",
    )
    assert inst.orchestrator_id == "Codex"

    # Dispatch to allowed worker
    record = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id="root-task-01",
        worker_id="OpenCode",
        goal="实现多Agent协作画布组件",
        acceptance_criteria="组件可交互、无类型报错",
        budget_slice=0.25,
    )
    assert record.subtask_id.startswith("sub-")
    assert record.worker_id == "OpenCode"
    assert record.state == "dispatched"

    # Reject worker not in template
    with pytest.raises(ValidationFailed):
        canvas_service.dispatch_subtask(
            actor=owner,
            instance_id=inst.id,
            root_task_id="root-task-01",
            worker_id="RandomWorker",
            goal="Invalid dispatch",
        )


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
    inst = canvas_service.create_instance(
        owner,
        project_name="测试事件流",
        template_id="personal",
    )

    disp = canvas_service.dispatch_subtask(
        actor=owner,
        instance_id=inst.id,
        root_task_id="root-1",
        worker_id="WorkBuddy",
        goal="整理近期行程",
    )

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
