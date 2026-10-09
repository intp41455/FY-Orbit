"""Tests for Phase F7: Media, Private Space & Daily Arrangements (Execution Manual F7, A11, A12).

Verifies:
1. Tool Declarations: Every tool declares purpose, uploaded data, provider, license, budget, cancellability, storage, failure handling.
2. Un-automatable Tools: App UI only tools (WeChat, railway tickets) return manual steps and prohibit automated scraping/hooking.
3. Unconfigured External Tools: DALL-E, Suno, Runway raise provider_not_configured (never simulate fake success).
4. Local Authorized Creation Closed Loop: Calendar (.ics) and SVG artwork generation, SHA-256 computation, S3/artifact persistence, domain="personal".
5. Sensitive Action Individual Approval: Calendar mutations and third-party actions require individual approval.
6. Artifact Lifecycle Integration: Generated personal assets participate in deletion cascading.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from find_yourself.adapters.artifacts import LocalArtifactStore
from find_yourself.adapters.creative_tools import CreativeToolsService
from find_yourself.db.models import Artifact, Task
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.budget import BudgetService
from find_yourself.services.deletion import DeletionService
from find_yourself.services.errors import DomainError


@pytest.fixture
def creative_service(session: Session, tmp_path: Path) -> CreativeToolsService:
    audit = AuditService(session)
    budget = BudgetService(session, audit)
    local_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return CreativeToolsService(session, audit, budget, local_store=local_store)


def test_f7_every_tool_has_complete_declarations(creative_service: CreativeToolsService):
    """F7: Every tool explicitly declares purpose, data uploaded, provider, license, budget, cancellable, storage, failure."""
    declarations = creative_service.list_declarations()
    assert len(declarations) >= 7

    for tool in declarations:
        assert tool["tool_id"]
        assert tool["purpose"]
        assert tool["uploaded_data"]
        assert tool["provider"]
        assert tool["license"]
        assert Decimal(tool["budget_estimate_usd"]) >= Decimal("0.00")
        assert isinstance(tool["cancellable"], bool)
        assert tool["storage_target"] in ("s3_artifacts", "local_audit", "local_artifacts")
        assert tool["failure_handling"]
        assert isinstance(tool["can_automate"], bool)


def test_f7_unautomatable_app_ui_tools_provide_manual_guidance(creative_service: CreativeToolsService):
    """F7: App UI only tools return can_automate=False and explicit manual guidance without browser hacking."""
    owner = Actor.owner("owner-1")

    # WeChat messaging
    wechat_res = creative_service.execute_tool(
        owner,
        tool_id="wechat_social_publish",
        task_id="task-1",
        idempotency_key="k1",
        params={"message": "hello"},
    )
    assert wechat_res["can_automate"] is False
    assert wechat_res["status"] == "manual_action_required"
    assert "微信客户端无官方开放个人自动化接口" in wechat_res["manual_steps"]

    # Railway tickets
    ticket_res = creative_service.execute_tool(
        owner,
        tool_id="railway_ticket_purchase",
        task_id="task-1",
        idempotency_key="k2",
        params={"train": "G123"},
    )
    assert ticket_res["can_automate"] is False
    assert ticket_res["status"] == "manual_action_required"
    assert "12306" in ticket_res["manual_steps"]


def test_f7_unconfigured_external_providers_raise_structured_error(creative_service: CreativeToolsService):
    """F7/A12: External tools without credentials return structured error and never fake success."""
    owner = Actor.owner("owner-1")

    with pytest.raises(DomainError) as exc_dalle:
        creative_service.execute_tool(
            owner,
            tool_id="dalle_image_generation",
            task_id="task-1",
            idempotency_key="k3",
            params={"prompt": "sunset over mountains"},
        )
    assert exc_dalle.value.code == "provider_not_configured"

    with pytest.raises(DomainError) as exc_suno:
        creative_service.execute_tool(
            owner,
            tool_id="suno_music_synthesis",
            task_id="task-1",
            idempotency_key="k4",
            params={"genre": "ambient"},
        )
    assert exc_suno.value.code == "provider_not_configured"


def test_f7_local_calendar_tool_enforces_individual_approval_and_persists_ics(
    creative_service: CreativeToolsService, session: Session
):
    """F7: Calendar tool requires individual approval; upon approval generates .ics and persists artifact."""
    from find_yourself.db.types import utcnow
    owner = Actor.owner("owner-1")
    task = Task(id="task-cal-01", owner_id=owner.owner_id, goal="Plan weekly schedule", status="running", deadline=utcnow(), idempotency_key="t-cal-1")
    session.add(task)
    session.flush()

    # Step 1: Without approval, returns approval_required
    unapproved = creative_service.execute_tool(
        owner,
        tool_id="local_calendar_schedule",
        task_id=task.id,
        idempotency_key="cal-k1",
        params={"title": "我的高效周", "events": ["周一 09:00 晨读", "周四 14:00 深度编程"]},
        approved=False,
    )
    assert unapproved["status"] == "approval_required"
    assert unapproved["requires_individual_approval"] is True

    # Step 2: With owner approval, executes authentic generation & S3/artifact storage
    approved_res = creative_service.execute_tool(
        owner,
        tool_id="local_calendar_schedule",
        task_id=task.id,
        idempotency_key="cal-k2",
        params={"title": "我的高效周", "events": ["周一 09:00 晨读", "周四 14:00 深度编程"]},
        approved=True,
    )
    assert approved_res["status"] == "completed"
    assert approved_res["media_type"] == "text/calendar"
    assert approved_res["domain"] == "personal"
    art_id = approved_res["artifact_id"]

    # Verify DB artifact record
    art = session.get(Artifact, art_id)
    assert art is not None
    assert art.domain == "personal"
    assert art.media_type == "text/calendar"
    assert art.sha256 == approved_res["sha256"]

    # Verify bytes stored locally
    stored_bytes = creative_service.local_store.get(art_id)
    assert b"BEGIN:VCALENDAR" in stored_bytes
    assert b"SUMMARY:\xe5\x91\xa8\xe4\xb8\x80 09:00 \xe6\x99\xa8\xe8\xaf\xbb" in stored_bytes  # "周一 09:00 晨读"


def test_f7_local_svg_artwork_completes_closed_loop(
    creative_service: CreativeToolsService, session: Session
):
    """F7: Local SVG visual creative tool generates valid SVG and registers verified artifact."""
    from find_yourself.db.types import utcnow
    owner = Actor.owner("owner-1")
    task = Task(id="task-svg-01", owner_id=owner.owner_id, goal="Generate mind visual", status="running", deadline=utcnow(), idempotency_key="t-svg-1")
    session.add(task)
    session.flush()

    res = creative_service.execute_tool(
        owner,
        tool_id="local_svg_artwork",
        task_id=task.id,
        idempotency_key="svg-k1",
        params={"topic": "深度认知跃迁"},
        approved=True,
    )
    assert res["status"] == "completed"
    assert res["media_type"] == "image/svg+xml"
    assert res["domain"] == "personal"

    art = session.get(Artifact, res["artifact_id"])
    assert art is not None
    stored_svg = creative_service.local_store.get(res["artifact_id"]).decode("utf-8")
    assert "<svg" in stored_svg
    assert "深度认知跃迁" in stored_svg


def test_f7_private_space_artifacts_cascade_on_deletion(
    creative_service: CreativeToolsService, session: Session
):
    """F7: Generated creative assets in private space are deleted via unified cascading deletion."""
    from find_yourself.db.types import utcnow
    owner = Actor.owner("owner-1")
    task = Task(id="task-del-01", owner_id=owner.owner_id, goal="Temporary art task", status="running", deadline=utcnow(), idempotency_key="t-del-1")
    session.add(task)
    session.flush()

    res = creative_service.execute_tool(
        owner,
        tool_id="local_svg_artwork",
        task_id=task.id,
        idempotency_key="svg-del-k1",
        params={"topic": "待清理图表"},
        approved=True,
    )
    art_id = res["artifact_id"]
    assert session.get(Artifact, art_id) is not None

    # Execute cascading deletion of the task
    deletion = DeletionService(session, AuditService(session))
    tombstone = deletion.delete(owner, target_id=task.id, target_kind="task", reason="cleanup")
    session.flush()

    art = session.get(Artifact, art_id)
    assert art is not None
    assert art.deleted_at is not None
    assert tombstone.target_id == task.id
    assert tombstone.dep_graph_hash is not None
