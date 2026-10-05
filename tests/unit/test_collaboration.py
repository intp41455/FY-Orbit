"""需求 15 第一切片：评论 / @人 / 通知 / 角色 —— 单元测试。

真实 SQLite（内存库 + ``create_all``，与 ``test_team_approval.py`` / ``test_hitl.py``
同风格），**不 mock**。协作的价值全在「越权的人真的被挡住、无权可见与不存在
返回一致」，mock 掉 DB 只能证明 mock 是这么配的。

覆盖：

- 评论 CRUD；**只能编辑/删除自己的**（他人评论 → 权限错误）；
- 角色矩阵 owner/admin/manager/viewer ×（读评论 / 写评论 / 删他人评论）逐格断言；
- @人：合法提及 / 不存在用户 / 无权可见用户 / 自我提及；
- 通知：被 @ 者收到、未被 @ 者收不到、已读标记幂等、**正文不含评论原文**；
- owner 隔离：无角色者读评论 → 与不存在**同一个** NotFound；A 读不到 B 的通知；
- 授权复用：服务身份跨域读**必须**过 ``GrantService.is_authorized``（角色不绕过）；
  30 天上限复用 ``grant.MAX_GRANT_SECONDS``、禁空主体/空 record；
- 审计链：协作事件不破坏哈希链，``message_id`` 关联可用且不写空键；
- 迁移 0030 可升可降、幂等。
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.db.base import Base
import find_yourself.db.models  # noqa: F401
import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.collaboration_models  # noqa: F401  (本次新增)
from find_yourself.db.canvas_models import CanvasInstance
from find_yourself.db.collaboration_models import (
    ALL_ROLES,
    NOTIFICATION_KIND_IN,
    NOTIFICATION_KINDS,
    NOTIFICATION_MENTION,
    NOTIFICATION_REPLY,
    CollaborationRole,
    Comment,
    Notification,
)
import find_yourself.services.collaboration as collaboration_module
from find_yourself.services.collaboration import NotificationTarget
from find_yourself.db.models import Artifact, AuditEvent, Memory, Task
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.collaboration import (
    COMMENT_DEFAULT_LIMIT,
    COMMENT_MAX_LIMIT,
    CollaborationService,
)
from find_yourself.services.errors import NotFound, PermissionDenied, ValidationFailed
from find_yourself.services.grant import MAX_GRANT_SECONDS, GrantService


# ----------------------------------------------------------------------
# 夹具：独立内存库（本地导入模型后建表，与 conftest 的解耦）
# ----------------------------------------------------------------------
@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = sm()
    s.execute(sa.text("PRAGMA foreign_keys=ON"))
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def audit(session):
    return AuditService(session)


@pytest.fixture()
def grants(session, audit):
    return GrantService(session, audit)


@pytest.fixture()
def svc(session, audit, grants):
    return CollaborationService(session, audit, grants=grants)


@pytest.fixture()
def alice():
    return Actor.owner("A")


@pytest.fixture()
def bob():
    return Actor.owner("B")


def _expiry(days: float = 7) -> datetime:
    return utcnow() + timedelta(days=days)


def _task(session, tid: str, owner: str, domain: str = "personal") -> Task:
    row = Task(
        id=tid, owner_id=owner, goal="g", domain=domain,
        deadline=utcnow() + timedelta(days=1), idempotency_key=tid,
    )
    session.add(row)
    session.flush()
    return row


def _memory(session, mid: str, owner: str, domain: str = "personal") -> Memory:
    row = Memory(
        id=mid, owner_id=owner, domain=domain, category="fact",
        content="c", content_hash="h" * 64,
    )
    session.add(row)
    session.flush()
    return row


def _canvas(session, cid: str, owner: str, domain: str = "personal") -> CanvasInstance:
    row = CanvasInstance(id=cid, owner_id=owner, project_name="p", domain=domain)
    session.add(row)
    session.flush()
    return row


def _artifact(session, aid: str, task_id: str | None, domain: str = "personal") -> Artifact:
    row = Artifact(id=aid, task_id=task_id, domain=domain, sha256="a" * 64, size=1)
    session.add(row)
    session.flush()
    return row


def _assign(svc, owner_actor, kind, rid, user_id, role, days: float = 7):
    return svc.assign_role(
        owner_actor, record_kind=kind, record_id=rid, user_id=user_id,
        role=role, expires_at=_expiry(days),
    )


# ----------------------------------------------------------------------
# record 解析
# ----------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["task", "memory", "canvas"])
def test_resolve_record_for_native_kinds(svc, session, kind):
    if kind == "task":
        row = _task(session, "t1", "A")
    elif kind == "memory":
        row = _memory(session, "m1", "A")
    else:
        row = _canvas(session, "c1", "A")
    ref = svc.resolve_record(kind, row.id)
    assert ref is not None
    assert (ref.kind, ref.id, ref.owner_id, ref.domain) == (kind, row.id, "A", "personal")


def test_artifact_owner_resolved_through_task(svc, session):
    t = _task(session, "t1", "A")
    a = _artifact(session, "a1", t.id)
    ref = svc.resolve_record("artifact", a.id)
    assert ref is not None and ref.owner_id == "A"


def test_artifact_without_task_is_unresolvable(svc, session):
    a = _artifact(session, "a1", None)
    ref = svc.resolve_record("artifact", a.id)
    assert ref is not None and ref.owner_id == ""


def test_unknown_kind_rejected(svc):
    with pytest.raises(ValidationFailed):
        svc.resolve_record("banana", "x")


# ----------------------------------------------------------------------
# 评论 CRUD
# ----------------------------------------------------------------------
def test_owner_adds_and_lists_comment(svc, session, alice):
    _task(session, "t1", "A")
    out = svc.add_comment(alice, record_kind="task", record_id="t1", body="hello")
    assert out["comment"]["author_id"] == "A"
    assert out["comment"]["body"] == "hello"
    items = svc.list_comments(alice, record_kind="task", record_id="t1")
    assert [c["body"] for c in items] == ["hello"]


def test_empty_comment_body_rejected(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(ValidationFailed):
        svc.add_comment(alice, record_kind="task", record_id="t1", body="   ")


def test_owner_edits_own_comment_bumps_version(svc, session, alice):
    _task(session, "t1", "A")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="v1")["comment"]["id"]
    out = svc.edit_comment(alice, cid, "v2")
    assert out["comment"]["body"] == "v2"
    assert out["comment"]["version"] == 2
    assert out["comment"]["edited_at"] is not None


def test_edit_empty_body_rejected(svc, session, alice):
    _task(session, "t1", "A")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="v1")["comment"]["id"]
    with pytest.raises(ValidationFailed):
        svc.edit_comment(alice, cid, "  ")


def test_owner_soft_deletes_own_comment_and_list_excludes_it(svc, session, alice):
    _task(session, "t1", "A")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="bye")["comment"]["id"]
    view = svc.delete_comment(alice, cid)
    assert view["deleted"] is True
    assert svc.list_comments(alice, record_kind="task", record_id="t1") == []


def test_comment_on_nonexistent_record_is_not_found(svc, alice):
    with pytest.raises(NotFound):
        svc.add_comment(alice, record_kind="task", record_id="nope", body="x")


# ----------------------------------------------------------------------
# 只能编辑/删除自己的
# ----------------------------------------------------------------------
def test_manager_cannot_edit_others_comment(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="mine")["comment"]["id"]
    with pytest.raises(PermissionDenied):
        svc.edit_comment(bob, cid, "hacked")


def test_manager_cannot_delete_others_comment(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="mine")["comment"]["id"]
    with pytest.raises(PermissionDenied):
        svc.delete_comment(bob, cid)


def test_admin_can_delete_others_comment(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "admin")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="mine")["comment"]["id"]
    assert svc.delete_comment(bob, cid)["deleted"] is True


def test_owner_can_delete_others_comment(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    cid = svc.add_comment(bob, record_kind="task", record_id="t1", body="bob")["comment"]["id"]
    assert svc.delete_comment(alice, cid)["deleted"] is True


# ----------------------------------------------------------------------
# 角色矩阵：owner/admin/manager/viewer ×（读 / 写 / 删他人）逐格断言
# ----------------------------------------------------------------------
_MATRIX_SETUP = {
    "owner": ("A", None),
    "admin": ("B", "admin"),
    "manager": ("B", "manager"),
    "viewer": ("B", "viewer"),
}


def _matrix_actor(role, alice, bob):
    return alice if role == "owner" else bob


@pytest.mark.parametrize("role", ALL_ROLES)
def test_matrix_read(role, svc, session, alice, bob):
    _task(session, "t1", "A")
    user, assigned = _MATRIX_SETUP[role]
    if assigned:
        _assign(svc, alice, "task", "t1", user, assigned)
    svc.add_comment(alice, record_kind="task", record_id="t1", body="visible")
    items = svc.list_comments(_matrix_actor(role, alice, bob), record_kind="task", record_id="t1")
    assert len(items) == 1  # 所有四级都能读


@pytest.mark.parametrize("role", ALL_ROLES)
def test_matrix_write(role, svc, session, alice, bob):
    _task(session, "t1", "A")
    user, assigned = _MATRIX_SETUP[role]
    if assigned:
        _assign(svc, alice, "task", "t1", user, assigned)
    actor = _matrix_actor(role, alice, bob)
    if role == "viewer":
        with pytest.raises(PermissionDenied):
            svc.add_comment(actor, record_kind="task", record_id="t1", body="x")
    else:
        assert svc.add_comment(actor, record_kind="task", record_id="t1", body="x")["comment"]


@pytest.mark.parametrize("role", ALL_ROLES)
def test_matrix_delete_others(role, svc, session, alice, bob):
    _task(session, "t1", "A")
    user, assigned = _MATRIX_SETUP[role]
    if assigned:
        _assign(svc, alice, "task", "t1", user, assigned)
    # 由 A 写一条，让「删他人」这个动作真实存在。
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="alice")["comment"]["id"]
    actor = _matrix_actor(role, alice, bob)
    if role in ("owner", "admin"):
        assert svc.delete_comment(actor, cid)["deleted"] is True
    else:  # manager / viewer
        with pytest.raises(PermissionDenied):
            svc.delete_comment(actor, cid)


# ----------------------------------------------------------------------
# @人
# ----------------------------------------------------------------------
def test_mention_of_visible_collaborator_creates_notification(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    out = svc.add_comment(alice, record_kind="task", record_id="t1", body="hi @B please review")
    assert out["comment"]["mentions"] == ["B"]
    assert [n["owner_id"] for n in svc.list_notifications(bob)] == ["B"]


def test_mention_of_unknown_user_is_dropped(svc, session, alice):
    _task(session, "t1", "A")
    out = svc.add_comment(alice, record_kind="task", record_id="t1", body="hey @ghost")
    assert out["comment"]["mentions"] == []
    assert out["notified"] == []


def test_mention_of_user_without_access_is_dropped(svc, session, alice):
    _task(session, "t1", "A")
    # D 是「存在但对该 record 无权可见」的人：没有任何角色行。
    out = svc.add_comment(alice, record_kind="task", record_id="t1", body="hey @D")
    assert out["comment"]["mentions"] == []
    assert session.query(Notification).filter_by(owner_id="D").count() == 0


def test_self_mention_records_mention_but_no_self_notification(svc, session, alice):
    _task(session, "t1", "A")
    out = svc.add_comment(alice, record_kind="task", record_id="t1", body="note to self @A")
    assert out["comment"]["mentions"] == ["A"]
    assert out["notified"] == []
    assert svc.list_notifications(alice) == []


def test_edit_adding_mention_notifies_new_user(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="plain")["comment"]["id"]
    assert svc.list_notifications(bob) == []
    svc.edit_comment(alice, cid, "now @B please look")
    assert len(svc.list_notifications(bob)) == 1


# ----------------------------------------------------------------------
# 通知
# ----------------------------------------------------------------------
def test_unmentioned_collaborator_gets_no_notification(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")  # B 可见，但没被 @
    svc.add_comment(alice, record_kind="task", record_id="t1", body="just chatting")
    assert svc.list_notifications(bob) == []


def test_mark_notification_read_is_idempotent(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="@B ping")
    nid = svc.list_notifications(bob)[0]["id"]
    first = svc.mark_notification_read(bob, nid)
    assert first["read"] is True and first["read_at"] is not None
    second = svc.mark_notification_read(bob, nid)
    assert second["version"] == first["version"]  # 重复标记不再改行


def test_notification_does_not_carry_comment_body(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    secret = "SECRET-TOKEN-abc123-should-never-appear-in-a-notification"
    svc.add_comment(alice, record_kind="task", record_id="t1", body=f"@B {secret}")
    notif = svc.list_notifications(bob)[0]
    assert secret not in notif["summary"]
    assert "body" not in notif  # 通知里根本没有正文字段
    assert len(notif["summary"]) <= 200


def test_cross_owner_mark_read_returns_not_found(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="@B hi")
    nid = svc.list_notifications(bob)[0]["id"]
    # A 去标 B 的通知已读 → 与「不存在」同一个 NotFound。
    with pytest.raises(NotFound):
        svc.mark_notification_read(alice, nid)


# ----------------------------------------------------------------------
# owner 隔离（越权与不存在返回一致）
# ----------------------------------------------------------------------
def test_non_collaborator_read_comments_is_not_found(svc, session, bob):
    _task(session, "t1", "A")
    with pytest.raises(NotFound):
        svc.list_comments(bob, record_kind="task", record_id="t1")


def test_non_collaborator_write_is_not_found(svc, session, bob):
    _task(session, "t1", "A")
    with pytest.raises(NotFound):
        svc.add_comment(bob, record_kind="task", record_id="t1", body="intruder")


def test_non_collaborator_gets_same_error_as_missing_record(svc, session, bob):
    _task(session, "t1", "A")
    with pytest.raises(NotFound) as existing:
        svc.list_comments(bob, record_kind="task", record_id="t1")
    with pytest.raises(NotFound) as missing:
        svc.list_comments(bob, record_kind="task", record_id="does-not-exist")
    assert existing.value.code == missing.value.code
    assert existing.value.http_status == missing.value.http_status


def test_owner_cannot_read_another_owners_notifications(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="@B hello")
    assert len(svc.list_notifications(bob)) == 1
    assert svc.list_notifications(alice) == []  # A 读不到 B 的通知


# ----------------------------------------------------------------------
# 角色授权：30 天上限 / 禁通配 / 资格
# ----------------------------------------------------------------------
def test_role_expiry_over_30_days_rejected(svc, session, alice):
    _task(session, "t1", "A")
    too_long = utcnow() + timedelta(seconds=MAX_GRANT_SECONDS + 60)
    with pytest.raises(ValidationFailed):
        svc.assign_role(
            alice, record_kind="task", record_id="t1", user_id="B",
            role="viewer", expires_at=too_long,
        )


def test_role_expiry_in_past_rejected(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(ValidationFailed):
        svc.assign_role(
            alice, record_kind="task", record_id="t1", user_id="B",
            role="viewer", expires_at=utcnow() - timedelta(seconds=1),
        )


def test_role_naive_expiry_rejected(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(ValidationFailed):
        svc.assign_role(
            alice, record_kind="task", record_id="t1", user_id="B",
            role="viewer", expires_at=datetime(2030, 1, 1),
        )


def test_role_requires_nonempty_user(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(ValidationFailed):
        _assign(svc, alice, "task", "t1", "  ", "viewer")


def test_bad_role_rejected(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(ValidationFailed):
        _assign(svc, alice, "task", "t1", "B", "superuser")


def test_manager_cannot_assign_roles(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")  # manager 没有 manage_roles
    with pytest.raises(PermissionDenied):
        _assign(svc, bob, "task", "t1", "C", "viewer")


def test_duplicate_assign_is_idempotent(svc, session, alice):
    _task(session, "t1", "A")
    first = _assign(svc, alice, "task", "t1", "B", "viewer")
    second = _assign(svc, alice, "task", "t1", "B", "manager")
    assert first["id"] == second["id"]
    assert second["role"] == "manager" and second["version"] == 2
    assert session.query(CollaborationRole).filter_by(record_id="t1", user_id="B").count() == 1


def test_revoke_role_blocks_access(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    assert svc.list_comments(bob, record_kind="task", record_id="t1") == []
    svc.revoke_role(alice, record_kind="task", record_id="t1", user_id="B")
    with pytest.raises(NotFound):
        svc.list_comments(bob, record_kind="task", record_id="t1")


# ----------------------------------------------------------------------
# 授权复用：跨域服务身份必须先过 GrantService.is_authorized
# ----------------------------------------------------------------------
def _agent() -> Actor:
    return Actor.service("agent-1", "agent", ["work"])


def test_service_cross_domain_without_grant_is_denied(svc, session, alice):
    mem = _memory(session, "m1", "A", domain="personal")
    _assign(svc, alice, "memory", mem.id, "agent-1", "viewer")
    # 有角色，但没有跨域 grant → 数据关卡先拦下（角色不绕过 grant）。
    with pytest.raises(NotFound):
        svc.list_comments(_agent(), record_kind="memory", record_id="m1")


def test_service_cross_domain_with_grant_is_allowed(svc, session, alice, grants):
    mem = _memory(session, "m1", "A", domain="personal")
    _assign(svc, alice, "memory", mem.id, "agent-1", "viewer")
    grants.create(
        alice, source_domain="personal", consumer_domain="work",
        record_ids=[mem.id], expires_at=_expiry(7),
    )
    assert svc.list_comments(_agent(), record_kind="memory", record_id="m1") == []


def test_service_grant_for_different_record_does_not_widen(svc, session, alice, grants):
    inside = _memory(session, "m1", "A", domain="personal")
    outside = _memory(session, "m2", "A", domain="personal")
    _assign(svc, alice, "memory", outside.id, "agent-1", "viewer")
    grants.create(
        alice, source_domain="personal", consumer_domain="work",
        record_ids=[inside.id], expires_at=_expiry(7),
    )
    # grant 只覆盖 m1，不能用来读 m2（禁通配）。
    with pytest.raises(NotFound):
        svc.list_comments(_agent(), record_kind="memory", record_id=outside.id)


def test_service_revoked_grant_is_denied(svc, session, alice, grants):
    mem = _memory(session, "m1", "A", domain="personal")
    _assign(svc, alice, "memory", mem.id, "agent-1", "viewer")
    g = grants.create(
        alice, source_domain="personal", consumer_domain="work",
        record_ids=[mem.id], expires_at=_expiry(7),
    )
    grants.revoke(alice, g.id)
    with pytest.raises(NotFound):
        svc.list_comments(_agent(), record_kind="memory", record_id="m1")


def test_service_same_domain_needs_no_grant(svc, session, alice):
    mem = _memory(session, "m1", "A", domain="work")
    _assign(svc, alice, "memory", mem.id, "agent-1", "viewer")
    agent = Actor.service("agent-1", "agent", ["work"])
    assert svc.list_comments(agent, record_kind="memory", record_id="m1") == []


# ----------------------------------------------------------------------
# 审计链：不破坏既有哈希链；message_id 关联可用且不写空键
# ----------------------------------------------------------------------
def test_audit_chain_still_verifies_after_collaboration(svc, session, alice, audit):
    _task(session, "t1", "A")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="hi")
    assert audit.verify().ok is True


def test_comment_audit_frame_is_linked_by_message_id(svc, session, alice, audit, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="@B hi")["comment"]["id"]
    actions = [f.action for f in audit.frames_for_message(alice, cid)]
    assert "collaboration.comment.created" in actions


def test_audit_event_without_message_id_has_no_key(svc, session, alice, audit):
    _task(session, "t1", "A")
    audit.append(alice, "collaboration.probe", "x", {"a": 1})
    assert audit.frames_for_message(alice, "nope") == []
    row = session.execute(
        sa.select(AuditEvent).where(AuditEvent.action == "collaboration.probe")
    ).scalar_one()
    assert "message_id" not in (row.details or {})


def test_verify_detects_no_gap_for_legacy_then_collab_events(svc, session, alice, audit):
    _task(session, "t1", "A")
    audit.append(alice, "legacy.event", "x", {"k": "v"})  # 无 message_id 的旧式事件
    svc.add_comment(alice, record_kind="task", record_id="t1", body="hi")
    result = audit.verify()
    assert result.ok is True and result.checked >= 2


# ----------------------------------------------------------------------
# 表级不变量
# ----------------------------------------------------------------------
def test_role_check_rejects_bad_role(session, alice):
    session.add(
        CollaborationRole(
            id="cr-x", record_kind="task", record_id="t1", owner_id="A",
            user_id="B", role="superuser", state="active", expires_at=_expiry(),
        )
    )
    with pytest.raises(Exception):
        session.flush()


def test_role_check_rejects_empty_record_id(session):
    session.add(
        CollaborationRole(
            id="cr-x", record_kind="task", record_id="", owner_id="A",
            user_id="B", role="viewer", state="active", expires_at=_expiry(),
        )
    )
    with pytest.raises(Exception):
        session.flush()


def test_notification_unique_per_recipient_and_comment(session):
    common = dict(
        owner_id="B", kind="mention", record_kind="task", record_id="t1",
        comment_id="cm-1", author_id="A", summary="s",
    )
    session.add(Notification(id="nt-1", **common))
    session.flush()
    session.add(Notification(id="nt-2", **common))
    with pytest.raises(Exception):
        session.flush()


def test_comment_body_check_rejects_empty(session):
    session.add(
        Comment(
            id="cm-x", owner_id="A", record_kind="task", record_id="t1",
            author_id="A", body="",
        )
    )
    with pytest.raises(Exception):
        session.flush()


# ----------------------------------------------------------------------
# 迁移 0030 可升可降、幂等
# ----------------------------------------------------------------------
def _migration_0030():
    import importlib

    return importlib.import_module("migrations.versions.0030_collaboration")


def _run_migration(conn, mod, direction: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    mod.op = Operations(MigrationContext.configure(conn))
    getattr(mod, direction)()


def test_migration_0030_upgrade_creates_tables():
    mod = _migration_0030()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _run_migration(conn, mod, "upgrade")
        insp = sa.inspect(conn)
        for table in ("collaboration_roles", "comments", "notifications"):
            assert insp.has_table(table)
        cols = {c["name"] for c in insp.get_columns("collaboration_roles")}
        assert {"record_kind", "record_id", "user_id", "role", "expires_at"} <= cols
        # expires_at 非空 = 不存在永久协作授权。
        assert insp.get_columns("collaboration_roles")[
            [c["name"] for c in insp.get_columns("collaboration_roles")].index("expires_at")
        ]["nullable"] is False


def test_migration_0030_downgrade_drops_tables_and_is_idempotent():
    mod = _migration_0030()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _run_migration(conn, mod, "upgrade")
        _run_migration(conn, mod, "upgrade")  # 幂等：不得重复建表报错
        _run_migration(conn, mod, "downgrade")
        insp = sa.inspect(conn)
        for table in ("collaboration_roles", "comments", "notifications"):
            assert not insp.has_table(table)
        _run_migration(conn, mod, "downgrade")  # 再降一次也不得报错


# ======================================================================
# 第二切片：未读计数 / 评论分页排序 / 删除评论联动通知
# ======================================================================
def _raw_comment(session, cid, owner, record_id, body, *, offset_seconds=0):
    """直接落一条评论行（可控 created_at），用于确定性地测排序/分页。"""
    created = utcnow() + timedelta(seconds=offset_seconds)
    session.add(
        Comment(
            id=cid, owner_id=owner, record_kind="task", record_id=record_id,
            author_id=owner, body=body, mentions=[], created_at=created, updated_at=created,
        )
    )
    session.flush()
    return cid


# --- 未读计数 --------------------------------------------------------- #
def test_unread_count_starts_zero(svc, session, alice, bob):
    _task(session, "t1", "A")
    assert svc.unread_count(bob) == 0


def test_unread_count_increments_on_mention(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="@B hi")
    assert svc.unread_count(bob) == 1


def test_unread_count_clears_after_mark_read(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="@B hi")
    nid = svc.list_notifications(bob)[0]["id"]
    svc.mark_notification_read(bob, nid)
    assert svc.unread_count(bob) == 0


def test_unread_count_is_per_identity(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="@B hi")
    assert svc.unread_count(bob) == 1
    assert svc.unread_count(alice) == 0  # A 没有收到任何通知


def test_unread_count_counts_all_unread_kinds(svc, session, alice, bob):
    """未读数只由 read_at 决定，不按 kind 过滤（kind 多类型后此式仍正确）。"""
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="@B one")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="@B two")
    assert svc.unread_count(bob) == 2
    svc.mark_notification_read(bob, svc.list_notifications(bob)[0]["id"])
    assert svc.unread_count(bob) == 1


def test_unread_count_needs_no_body(svc, session, alice, bob):
    """未读数是整数，接口/服务层都不返回任何通知正文。"""
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    svc.add_comment(alice, record_kind="task", record_id="t1", body="@B secret-payload")
    assert isinstance(svc.unread_count(bob), int)


# --- 评论分页与排序 --------------------------------------------------- #
def test_default_limit_is_bounded(svc, session, alice):
    _task(session, "t1", "A")
    for i in range(COMMENT_DEFAULT_LIMIT + 5):
        _raw_comment(session, f"cm-{i:03d}", "A", "t1", f"c{i}", offset_seconds=i)
    items = svc.list_comments(alice, record_kind="task", record_id="t1")
    assert len(items) == COMMENT_DEFAULT_LIMIT  # 默认有界，不是全量


def test_limit_offset_and_page_metadata(svc, session, alice):
    _task(session, "t1", "A")
    for i in range(5):
        _raw_comment(session, f"cm-{i}", "A", "t1", f"c{i}", offset_seconds=i)
    page0 = svc.list_comments_page(alice, record_kind="task", record_id="t1", limit=2, offset=0)
    assert [c["body"] for c in page0["items"]] == ["c0", "c1"]
    assert page0["count"] == 2 and page0["has_more"] is True
    page2 = svc.list_comments_page(alice, record_kind="task", record_id="t1", limit=2, offset=4)
    assert [c["body"] for c in page2["items"]] == ["c4"]
    assert page2["has_more"] is False


def test_order_desc_reverses(svc, session, alice):
    _task(session, "t1", "A")
    for i in range(4):
        _raw_comment(session, f"cm-{i}", "A", "t1", f"c{i}", offset_seconds=i)
    items = svc.list_comments(alice, record_kind="task", record_id="t1", order="desc")
    assert [c["body"] for c in items] == ["c3", "c2", "c1", "c0"]


def test_offset_beyond_end_is_empty_not_wrapped(svc, session, alice):
    _task(session, "t1", "A")
    _raw_comment(session, "cm-0", "A", "t1", "only", offset_seconds=0)
    page = svc.list_comments_page(alice, record_kind="task", record_id="t1", limit=10, offset=50)
    assert page["items"] == [] and page["has_more"] is False


def test_limit_over_max_rejected(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(ValidationFailed):
        svc.list_comments(alice, record_kind="task", record_id="t1", limit=COMMENT_MAX_LIMIT + 1)


def test_limit_zero_or_negative_rejected(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(ValidationFailed):
        svc.list_comments(alice, record_kind="task", record_id="t1", limit=0)


def test_negative_offset_rejected(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(ValidationFailed):
        svc.list_comments(alice, record_kind="task", record_id="t1", offset=-1)


def test_bad_order_rejected(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(ValidationFailed):
        svc.list_comments(alice, record_kind="task", record_id="t1", order="sideways")


def test_pagination_only_returns_the_requested_record(svc, session, alice):
    _task(session, "t1", "A")
    _task(session, "t2", "A")
    _raw_comment(session, "cm-a", "A", "t1", "on-t1", offset_seconds=0)
    _raw_comment(session, "cm-b", "A", "t2", "on-t2", offset_seconds=1)
    items = svc.list_comments(alice, record_kind="task", record_id="t1", limit=100)
    assert [c["body"] for c in items] == ["on-t1"]


def test_non_collaborator_pagination_is_not_found(svc, session, bob):
    """越权分页必须与不存在一致 —— 分页参数不得成为绕过隔离的手段。"""
    _task(session, "t1", "A")
    _raw_comment(session, "cm-0", "A", "t1", "secret", offset_seconds=0)
    with pytest.raises(NotFound):
        svc.list_comments(bob, record_kind="task", record_id="t1", limit=1, offset=0)


# --- 删除评论联动通知 ------------------------------------------------- #
def test_delete_comment_invalidates_its_notifications(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="@B hi")["comment"]["id"]
    assert len(svc.list_notifications(bob)) == 1
    svc.delete_comment(alice, cid)
    assert svc.list_notifications(bob) == []
    assert svc.unread_count(bob) == 0


def test_delete_comment_only_invalidates_its_own_notifications(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    keep = svc.add_comment(alice, record_kind="task", record_id="t1", body="@B keep")["comment"]["id"]
    drop = svc.add_comment(alice, record_kind="task", record_id="t1", body="@B drop")["comment"]["id"]
    svc.delete_comment(alice, drop)
    remaining = svc.list_notifications(bob)
    assert len(remaining) == 1 and remaining[0]["comment_id"] == keep


def test_deleted_comment_notification_cannot_be_marked_read(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="@B hi")["comment"]["id"]
    nid = svc.list_notifications(bob)[0]["id"]
    svc.delete_comment(alice, cid)
    with pytest.raises(NotFound):
        svc.mark_notification_read(bob, nid)


def test_edit_removing_mention_invalidates_notification(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="@B hi")["comment"]["id"]
    assert len(svc.list_notifications(bob)) == 1
    svc.edit_comment(alice, cid, "never mind")
    assert svc.list_notifications(bob) == []


def test_delete_requires_author_or_delete_any_still_enforced(svc, session, alice, bob):
    """联动删除不得成为越权删除的旁路：manager 仍不能删他人评论。"""
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="@B x")["comment"]["id"]
    with pytest.raises(PermissionDenied):
        svc.delete_comment(bob, cid)
    assert len(svc.list_notifications(bob)) == 1  # 越权删除被拒，通知原封不动


# ======================================================================
# 第三切片：回复通知语义（纯 planner）/ 自通知抑制收口 / kind 单一真源
# ======================================================================
# --- 回复通知语义：谁通知谁（纯函数，不落库） ------------------------- #
def test_plan_reply_notifies_parent_author_with_reply_kind(svc):
    plan = svc.plan_reply_targets(author_id="B", parent_author_id="A")
    assert plan == [NotificationTarget("A", NOTIFICATION_REPLY)]


def test_plan_reply_to_self_produces_nothing(svc):
    assert svc.plan_reply_targets(author_id="B", parent_author_id="B") == []


def test_plan_reply_notifies_mentioned_as_mention_kind(svc):
    plan = svc.plan_reply_targets(author_id="B", parent_author_id="A", mentioned=["C"])
    assert NotificationTarget("A", NOTIFICATION_REPLY) in plan
    assert NotificationTarget("C", NOTIFICATION_MENTION) in plan


def test_plan_reply_dedups_parent_author_to_the_more_specific_reply_kind(svc):
    # A 既是父评论作者、又被 @：只收一条，取更具体的 comment_reply。
    plan = svc.plan_reply_targets(author_id="B", parent_author_id="A", mentioned=["A", "A"])
    assert plan == [NotificationTarget("A", NOTIFICATION_REPLY)]


def test_plan_reply_never_self_notifies_even_when_mentioned(svc):
    plan = svc.plan_reply_targets(author_id="B", parent_author_id="A", mentioned=["B"])
    assert [t.user_id for t in plan] == ["A"]  # B 不在名单里


def test_plan_reply_does_not_auto_notify_record_owner(svc):
    # owner 是 A；回复人 B 回复 C 的评论、未 @ A → A 不应被惊动。
    plan = svc.plan_reply_targets(author_id="B", parent_author_id="C")
    assert [t.user_id for t in plan] == ["C"]


#: 一个**不在**任何白名单里的 kind，用于验证落库闸门（fail loud）。
BOGUS_KIND = "not_a_real_kind"


# --- 落库闸门：未白名单 kind 拒写、白名单 kind 可写 ------------------- #
def test_notify_targets_rejects_unwhitelisted_kind(svc, session, alice):
    """白名单外的 kind → 落库被拒（fail loud），且不留半行。"""
    _task(session, "t1", "A")
    ref = svc.resolve_record("task", "t1")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="hi")["comment"]["id"]
    row = session.get(Comment, cid)
    with pytest.raises(ValidationFailed):
        svc.notify_targets(alice, row, ref, [NotificationTarget("B", BOGUS_KIND)])
    assert session.query(Notification).filter_by(owner_id="B").count() == 0


def test_notify_targets_persists_every_whitelisted_kind(svc, session, alice):
    """前瞻性：对当前白名单里的每个 kind 都能落库（将来扩白名单即自动覆盖）。"""
    _task(session, "t1", "A")
    ref = svc.resolve_record("task", "t1")
    for i, kind in enumerate(NOTIFICATION_KINDS):
        cid = svc.add_comment(alice, record_kind="task", record_id="t1", body=f"c{i}")["comment"]["id"]
        row = session.get(Comment, cid)
        out = svc.notify_targets(alice, row, ref, [NotificationTarget("B", kind)])
        assert len(out) == 1 and out[0]["kind"] == kind


def test_comment_reply_is_whitelisted_after_0032():
    assert NOTIFICATION_REPLY in NOTIFICATION_KINDS
    assert NOTIFICATION_MENTION in NOTIFICATION_KINDS


# --- kind 白名单单一真源 ---------------------------------------------- #
def test_kind_whitelist_expr_is_derived_and_matches_0032():
    from sqlalchemy import CheckConstraint

    assert NOTIFICATION_KIND_IN == "kind IN ('mention', 'comment_reply')"
    exprs = [
        str(c.sqltext) for c in Notification.__table__.constraints
        if isinstance(c, CheckConstraint) and "kind IN" in str(c.sqltext)
    ]
    assert exprs == [NOTIFICATION_KIND_IN]


def test_migration_0030_keeps_the_historical_kind_check():
    """0030 是历史：它落盘的仍是单值 CHECK，不应被改写。"""
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0030_collaboration.py"
    assert "kind IN ('mention')" in path.read_text(encoding="utf-8")


def test_migration_0032_kind_check_matches_model_by_reusing_the_constant():
    """0032 扩宽 CHECK 时直接 import 模型派生表达式，而非手抄 → 不可能漂移。"""
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations" / "versions" / "0032_collaboration_replies.py"
    )
    text = path.read_text(encoding="utf-8")
    assert "from find_yourself.db.collaboration_models import" in text
    assert "NOTIFICATION_KIND_IN" in text
    assert "kind IN ('mention', 'comment_reply')" not in text  # 不手抄字面量


def _service_non_docstring_string_literals() -> list[str]:
    src = Path(collaboration_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    docstrings: set[int] = set()

    def _mark_doc(node) -> None:
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))

    _mark_doc(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _mark_doc(node)
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def test_service_does_not_hardcode_notification_kind_literals():
    """服务层不得硬编码 kind 字面量：必须引用 db 层的白名单常量（单一真源）。"""
    banned = set(NOTIFICATION_KINDS) | {NOTIFICATION_REPLY}
    offenders = sorted({s for s in _service_non_docstring_string_literals() if s in banned})
    assert offenders == [], f"service must reference NOTIFICATION_* constants, not literals: {offenders}"


# ======================================================================
# 第四切片：回复评论（0032）—— 落库通知 / 同 record 校验 / 防环 / 软删占位
# ======================================================================
def _reply(svc, actor, parent, *, kind="task", rid="t1", body="reply"):
    return svc.add_reply(
        actor, record_kind=kind, record_id=rid, parent_comment_id=parent, body=body
    )


def test_add_reply_creates_child_and_notifies_parent_author(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="q")["comment"]["id"]
    out = _reply(svc, bob, c1, body="a")
    assert out["comment"]["parent_comment_id"] == c1
    assert out["comment"]["author_id"] == "B"
    notifs = svc.list_notifications(alice)
    assert len(notifs) == 1 and notifs[0]["kind"] == NOTIFICATION_REPLY


def test_reply_to_own_comment_does_not_notify_self(svc, session, alice):
    _task(session, "t1", "A")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="q")["comment"]["id"]
    out = _reply(svc, alice, c1, body="self-reply")
    assert out["notified"] == []
    assert svc.list_notifications(alice) == []


def test_reply_mention_creates_mention_notification(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    _assign(svc, alice, "task", "t1", "C", "viewer")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="q")["comment"]["id"]
    out = _reply(svc, bob, c1, body="@C take a look")
    assert out["comment"]["mentions"] == ["C"]
    carol = Actor.owner("C")
    assert [n["kind"] for n in svc.list_notifications(carol)] == [NOTIFICATION_MENTION]
    assert [n["kind"] for n in svc.list_notifications(alice)] == [NOTIFICATION_REPLY]


def test_reply_notification_has_no_body(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="q")["comment"]["id"]
    secret = "REPLY-SECRET-zzz-should-not-leak"
    _reply(svc, bob, c1, body=secret)
    n = svc.list_notifications(alice)[0]
    assert secret not in n["summary"] and "body" not in n


def test_reply_parent_must_belong_to_the_same_record(svc, session, alice, bob):
    """跨记录借道必须被拒：父评论属于 t1，却以 t2 的访问权去回复。"""
    _task(session, "t1", "A")
    _task(session, "t2", "A")
    _assign(svc, alice, "task", "t2", "B", "manager")  # B 对 t2 有写权、对 t1 无
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="on-t1")["comment"]["id"]
    with pytest.raises(NotFound):
        _reply(svc, bob, c1, kind="task", rid="t2", body="borrow")
    # owner 也不例外：A 同时拥有 t1/t2，用 t2 声明回复 t1 的评论同样被拒。
    with pytest.raises(NotFound):
        _reply(svc, alice, c1, kind="task", rid="t2", body="borrow")
    assert svc.list_comments(alice, record_kind="task", record_id="t1") != []


def test_reply_to_nonexistent_parent_is_not_found(svc, session, alice):
    _task(session, "t1", "A")
    with pytest.raises(NotFound):
        _reply(svc, alice, "cm-does-not-exist", body="x")


def test_viewer_cannot_reply(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "viewer")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="q")["comment"]["id"]
    with pytest.raises(PermissionDenied):
        _reply(svc, bob, c1, body="x")


def test_non_collaborator_reply_is_not_found(svc, session, alice, bob):
    _task(session, "t1", "A")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="q")["comment"]["id"]
    with pytest.raises(NotFound):
        _reply(svc, bob, c1, body="x")


def test_reply_parents_form_a_forward_only_dag_no_cycles(svc, session, alice, bob):
    """沿 parent 回溯必然终止于 None 且不重复 —— 无环不变量（构造性证明的可执行副本）。"""
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="c1")["comment"]["id"]
    c2 = _reply(svc, bob, c1, body="c2")["comment"]["id"]
    c3 = _reply(svc, alice, c2, body="c3")["comment"]["id"]
    c4 = _reply(svc, bob, c3, body="c4")["comment"]["id"]
    parents = {c1: None, c2: c1, c3: c2, c4: c3}
    for start in parents:
        seen, cur = set(), start
        while cur is not None:
            assert cur not in seen, "cycle detected"
            seen.add(cur)
            cur = parents[cur]
    views = {
        c["id"]: c
        for c in svc.list_comments(alice, record_kind="task", record_id="t1", limit=100)
    }
    assert views[c1]["parent_comment_id"] is None
    assert views[c2]["parent_comment_id"] == c1
    assert views[c3]["parent_comment_id"] == c2


def test_edit_comment_cannot_change_parent(svc, session, alice, bob):
    """编辑只改 body；没有改父指针的入口 —— 成环因此无路可走。"""
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="q")["comment"]["id"]
    c2 = _reply(svc, bob, c1, body="a")["comment"]["id"]
    svc.edit_comment(bob, c2, "edited body")
    views = {
        c["id"]: c
        for c in svc.list_comments(alice, record_kind="task", record_id="t1", limit=100)
    }
    assert views[c2]["parent_comment_id"] == c1


def test_soft_deleted_parent_keeps_reply_visible_with_placeholder(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="q")["comment"]["id"]
    c2 = _reply(svc, bob, c1, body="a")["comment"]["id"]
    svc.delete_comment(alice, c1)
    items = {
        c["id"]: c
        for c in svc.list_comments(alice, record_kind="task", record_id="t1", limit=100)
    }
    assert c1 not in items                     # 父评论已删，不再列出
    assert c2 in items                         # 子回复仍可见（不隐藏）
    assert items[c2]["parent_deleted"] is True  # 占位语义
    # 删除父评论只失效父评论自己的通知；子回复给 A 的评论回复通知仍在。
    assert any(n["comment_id"] == c2 for n in svc.list_notifications(alice))


def test_reply_view_flags_parent_deleted_false_while_parent_alive(svc, session, alice, bob):
    _task(session, "t1", "A")
    _assign(svc, alice, "task", "t1", "B", "manager")
    c1 = svc.add_comment(alice, record_kind="task", record_id="t1", body="q")["comment"]["id"]
    out = _reply(svc, bob, c1, body="a")
    assert out["comment"]["parent_deleted"] is False


# --- 迁移 0032 真跑：升级 / 降级 / 往返 --------------------------------- #
def _migration_0032():
    import importlib

    return importlib.import_module("migrations.versions.0032_collaboration_replies")


def _build_0030_tables(conn):
    _run_migration(conn, _migration_0030(), "upgrade")


def _kind_check_sql(conn) -> str | None:
    """按**后缀**匹配：真实 alembic 运行会经 naming_convention 展开成
    ``ck_notifications_ck_notif_kind``，而裸 ``Operations``（无约定）下是短名
    ``ck_notif_kind``。两者都要能命中。"""
    for c in sa.inspect(conn).get_check_constraints("notifications"):
        if (c.get("name") or "").endswith("ck_notif_kind"):
            return (c.get("sqltext") or "").strip()
    return None


def test_migration_0032_upgrade_widens_check_and_adds_parent_column():
    mod = _migration_0032()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _build_0030_tables(conn)
        _run_migration(conn, mod, "upgrade")
        insp = sa.inspect(conn)
        assert "parent_comment_id" in {c["name"] for c in insp.get_columns("comments")}
        assert any(
            i["name"] == "ix_comments_parent_comment_id" for i in insp.get_indexes("comments")
        )
        fks = {fk["name"]: fk for fk in insp.get_foreign_keys("comments")}
        assert "fk_comments_parent_comment_id_comments" in fks
        assert fks["fk_comments_parent_comment_id_comments"]["options"].get("ondelete") == "SET NULL"
        assert _kind_check_sql(conn) == NOTIFICATION_KIND_IN
        # batch 重建不得弄丢其它 CHECK。
        names = {c["name"] for c in insp.get_check_constraints("comments")}
        assert {"ck_comment_body_nonempty", "ck_comment_kind"} <= names


def test_migration_0032_round_trip_upgrade_downgrade_upgrade():
    mod = _migration_0032()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _build_0030_tables(conn)
        _run_migration(conn, mod, "upgrade")
        _run_migration(conn, mod, "downgrade")
        insp = sa.inspect(conn)
        assert "parent_comment_id" not in {c["name"] for c in insp.get_columns("comments")}
        assert not any(
            i["name"] == "ix_comments_parent_comment_id" for i in insp.get_indexes("comments")
        )
        assert _kind_check_sql(conn) == "kind IN ('mention')"
        # 再升一次：往返必须成功（幂等）。
        _run_migration(conn, mod, "upgrade")
        assert "parent_comment_id" in {c["name"] for c in sa.inspect(conn).get_columns("comments")}
        assert _kind_check_sql(conn) == NOTIFICATION_KIND_IN


def test_migration_0032_is_noop_without_tables():
    mod = _migration_0032()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _run_migration(conn, mod, "upgrade")  # must not raise
        _run_migration(conn, mod, "downgrade")


def test_alembic_cli_full_chain_widens_kind_check_and_adds_parent_column(tmp_path):
    """真实 `alembic upgrade head`（带 naming_convention）到底后，schema 与模型一致。"""
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    url = "sqlite:///" + str(tmp_path / "mig.db").replace("\\", "/")
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    insp = sa.inspect(sa.create_engine(url))
    assert _kind_check_sql_engine(url) == NOTIFICATION_KIND_IN
    cols = {c["name"] for c in insp.get_columns("comments")}
    assert "parent_comment_id" in cols
    assert any(i["name"] == "ix_comments_parent_comment_id" for i in insp.get_indexes("comments"))
    # 真实迁移下约束名经 naming_convention 展开，与 fresh create_all 一致。
    names = {c["name"] for c in insp.get_check_constraints("notifications")}
    assert "ck_notifications_ck_notif_kind" in names

    # downgrade → 再 upgrade（往返）
    command.downgrade(cfg, "0031_plugin_ecosystem")
    insp = sa.inspect(sa.create_engine(url))
    assert "parent_comment_id" not in {c["name"] for c in insp.get_columns("comments")}
    assert _kind_check_sql_engine(url) == "kind IN ('mention')"
    command.upgrade(cfg, "head")
    assert _kind_check_sql_engine(url) == NOTIFICATION_KIND_IN


def _kind_check_sql_engine(url: str) -> str | None:
    return _kind_check_sql(sa.create_engine(url))


# ======================================================================
# P7 · 迁移 0032 的变异测试（2026-10-05）
# ----------------------------------------------------------------------
# 背景：前一位执行方留下的 0032 在**真实 alembic 全链**上 downgrade 会炸：
#   ValueError: No such constraint:
#   'ck_notifications_ck_notifications_ck_notif_kind'
# 根因：把 ``sa.inspect`` 反射回来的**展开名**喂回 ``drop_constraint``，
# 而 ``NAMING_CONVENTION['ck']`` 会把它再当 ``%(constraint_name)s`` 插值一次。
#
# 下面这些用例是**变异测试**：每条都断言「某个具体写法会被拒绝 / 某条不变量成立」，
# 目的不是测当前实现，而是**确保修复不会被后人改回去**。
# 判据统一为「断言失败即代表 bug 回归」，不接受 try/except 吞异常。
# ======================================================================


def _migration_0032_with_naming_convention(conn, direction: str) -> None:
    """在**带 naming_convention** 的 MigrationContext 下跑 0032。

    与 ``_run_migration`` 的区别：后者用裸 ``MigrationContext.configure(conn)``
    （无约定），约束名不被展开，恰好绕开了这个 bug。真实 ``alembic upgrade/downgrade``
    经 ``env.py`` 的 ``target_metadata=Base.metadata``（带约定），所以只有本函数
    走的那条路径能复现生产行为。

    这是**变异测试的载体**：若有人在 0032 里改回「传展开名」，只有本函数会红。
    """
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    mod = _migration_0032()
    ctx = MigrationContext.configure(conn)
    # env.py 注入的就是 Base.metadata；约定从它的 metadata 上取。
    ctx.opts["target_metadata"] = Base.metadata
    mod.op = Operations(ctx)
    getattr(mod, direction)()


def test_0032_downgrade_survives_naming_convention(tmp_path):
    """变异测试①：带命名约定时，0032 downgrade 必须成功（不双重前缀）。"""
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    url = "sqlite:///" + str(tmp_path / "mig.db").replace("\\", "/")
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "0032_collaboration_replies")
    # 这一步是本用例的全部意义：修复前在此抛 ValueError。
    command.downgrade(cfg, "0031_plugin_ecosystem")
    insp = sa.inspect(sa.create_engine(url))
    assert _kind_check_sql_engine(url) == "kind IN ('mention')"
    assert "parent_comment_id" not in {c["name"] for c in insp.get_columns("comments")}
    # 再升回来：往返必须仍然成立。
    command.upgrade(cfg, "0032_collaboration_replies")
    assert _kind_check_sql_engine(url) == NOTIFICATION_KIND_IN


def test_0032_never_double_prefixes_the_constraint_name(tmp_path):
    """变异测试②：0032 往返后，kind CHECK 的名字**不得**出现双重前缀。

    直接钉住错误信息里那个具体形状 ``ck_notifications_ck_notifications_*``：
    任何人把展开名喂回 drop_constraint，这里立刻红。
    """
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    url = "sqlite:///" + str(tmp_path / "mig.db").replace("\\", "/")
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")
    for _ in range(2):  # 往返两轮：每一轮都检查
        command.downgrade(cfg, "0032_collaboration_replies")
        command.upgrade(cfg, "head")
        names = {c["name"] for c in sa.inspect(sa.create_engine(url)).get_check_constraints("notifications")}
        assert "ck_notifications_ck_notif_kind" in names
        assert not any(n.count("ck_notifications_") > 1 for n in names), (
            f"出现双重前缀约束名: {sorted(names)}"
        )


def test_0032_passes_short_name_to_drop_constraint():
    """变异测试③：静态检查——0032 源码里 ``drop_constraint`` 只允许传短名常量。

    这是「反射名不得回喂」这条规则的**唯一可执行的文档**。用 AST 读源码而不是
    跑一遍迁移，是因为跑迁移的路径无法区分「恰好没炸」与「写法正确」。
    """
    mod = _migration_0032()
    short = mod._KIND_CONSTRAINT
    assert short == "ck_notif_kind", "短名常量被改名，会与库里已落盘的约束脱节"

    src = Path(_migration_0032_path()).read_text(encoding="utf-8")
    tree = ast.parse(src)
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "drop_constraint"):
            continue
        # 只管 CHECK。FK 的 drop 传 `_PARENT_FK` 是对的：``NAMING_CONVENTION['fk']``
        # 插值的是 column_0_name / referred_table_name（与传入名无关），天然幂等，
        # 不存在 CHECK 那种双重前缀问题。
        if "check" not in _const_kwarg(node, "type_"):
            continue
        checked += 1
        arg = node.args[0] if node.args else None
        assert isinstance(arg, ast.Name), (
            "drop_constraint 的第一个参数必须是常量名（短名），不能是表达式"
        )
        assert arg.id == "_KIND_CONSTRAINT", (
            f"CHECK 的 drop_constraint 传了 {arg.id!r}，必须传短名常量 _KIND_CONSTRAINT"
        )
    assert checked >= 2, "0032 源码里 CHECK 的 drop_constraint 少于 2 处，检查是否被误删"


def _const_kwarg(node: ast.Call, name: str) -> str:
    """取调用里的字面量 kwarg 值（非字面量返回 '?'）。"""
    for kw in node.keywords:
        if kw.arg == name:
            return ast.unparse(kw.value)
    return ""


def test_0032_kind_probe_does_not_return_a_constraint_name():
    """变异测试④：探测函数只回传 ``(存在, sqltext)``，**不得**回传反射名。

    回传名字正是本次 bug 的源头（调用方会顺手把它喂回 drop_constraint）。
    把它从接口层面去掉，比在注释里写「别这么用」可靠。

    判据是**实际调用**：真跑一次 `_kind_check`，断言返回元里没有任何元素长得
    像约束名。只看类型签名不够——把返回值塞进 ``tuple`` 一样能骗过签名检查
    （变异体验证：签名不变、返回值多一个字符串时，本用例仍会红）。
    """
    mod = _migration_0032()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _build_0030_tables(conn)
        found, current = mod._kind_check(conn)
    assert found is True
    assert current == "kind IN ('mention')", "第二个返回值应是 CHECK 的 sqltext"

    # 第三个返回值 = 约束名（bug 的源头形状）
    probe = _engine_with_0030()
    assert len(_kind_check_tuple(mod, probe)) == 2, (
        "_kind_check 返回了多于 2 个元素 —— 多出来的很可能是约束名"
    )


def _kind_check_tuple(mod, eng):
    with eng.begin() as conn:
        return tuple(mod._kind_check(conn))


def _engine_with_0030():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _build_0030_tables(conn)
    return eng


def test_0032_raises_loudly_when_kind_check_missing():
    """变异测试⑤：表在但白名单 CHECK 不在 → upgrade **必须报错**，不得静默跳过。

    「静默跳过」会让「库里没有这条约束」看起来像「迁移已生效」——
    与本仓库的诚实原则（§2-1）直接冲突。
    """
    mod = _migration_0032()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE notifications (id VARCHAR(64) PRIMARY KEY, kind VARCHAR(24) NOT NULL)")
        with pytest.raises(RuntimeError) as ei:
            _run_migration(conn, mod, "upgrade")
        assert "ck_notif_kind" in str(ei.value)


def test_0032_is_idempotent_under_naming_convention(tmp_path):
    """变异测试⑥：带约定时重复 upgrade 两遍不得报错，也不得叠加约束。"""
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    url = "sqlite:///" + str(tmp_path / "mig.db").replace("\\", "/")
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "0032_collaboration_replies")
    before = {
        c["name"] for c in sa.inspect(sa.create_engine(url)).get_check_constraints("notifications")
    }
    command.downgrade(cfg, "0031_plugin_ecosystem")
    command.upgrade(cfg, "0032_collaboration_replies")
    command.upgrade(cfg, "0032_collaboration_replies")  # 已到目标版本，应为 no-op
    after = {
        c["name"] for c in sa.inspect(sa.create_engine(url)).get_check_constraints("notifications")
    }
    assert before == after, "重复 upgrade 后约束集合变了（叠加或丢失）"
    assert _kind_check_sql_engine(url) == NOTIFICATION_KIND_IN


def _migration_0032_path() -> Path:
    return Path(__file__).resolve().parents[2] / (
        "migrations/versions/0032_collaboration_replies.py"
    )


# ===========================================================================
# P8 · 通知 SSE 推送（复用 runtime.sse 共享总线；载荷无正文；重连幂等）
# ===========================================================================
from find_yourself.runtime.sse import bus as p8_bus
from find_yourself.services.collaboration import notification_channel


@pytest.fixture()
def clean_bus():
    """总线是模块级单例：每个用例前清空，避免跨用例串味。"""
    p8_bus._hist.clear()
    p8_bus._subs.clear()
    yield p8_bus


def _events_for(user_id: str):
    return list(p8_bus._hist.get(notification_channel(user_id), []))


def test_push_payload_has_no_comment_body(svc, session, alice, bob, clean_bus):
    """门禁核心①：推送载荷绝不含评论正文，只含 _summary 定位串与元数据。"""
    _memory(session, "m-p8-1", "A")
    _assign(svc, alice, "memory", "m-p8-1", "B", "manager")
    secret = "SECRET-COMMENT-BODY-XYZ"
    svc.add_comment(bob, record_kind="memory", record_id="m-p8-1",
                    body=f"请 @{alice.owner_id if False else 'A'} 看这个：{secret}")
    events = _events_for("A")
    assert events, "被@人必须收到推送事件"
    for ev in events:
        payload = json.dumps(ev.data, ensure_ascii=False)
        assert secret not in payload, "推送载荷泄漏了评论正文！"
        assert ev.data["summary"] == "B 在 memory 的评论中提到了你"


def test_push_goes_only_to_target_user_channel(svc, session, alice, bob, clean_bus):
    _memory(session, "m-p8-2", "A")
    _assign(svc, alice, "memory", "m-p8-2", "B", "manager")
    svc.add_comment(bob, record_kind="memory", record_id="m-p8-2", body="hi @A")
    assert _events_for("A"), "目标用户必须收到事件"
    assert _events_for("B") == [], "非目标用户的频道不得收到事件（author 自通知抑制）"


def test_push_event_carries_kind_and_record_ref(svc, session, alice, bob, clean_bus):
    _memory(session, "m-p8-3", "A")
    _assign(svc, alice, "memory", "m-p8-3", "B", "manager")
    svc.add_comment(bob, record_kind="memory", record_id="m-p8-3", body="look @A")
    ev = _events_for("A")[0]
    assert ev.data["kind"] == "mention"
    assert ev.data["record_kind"] == "memory"
    assert ev.data["record_id"] == "m-p8-3"
    assert ev.data["notification_id"].startswith("nt-")
    assert ev.data["comment_id"].startswith("cm-")


def test_push_unread_count_in_payload(svc, session, alice, bob, clean_bus):
    _memory(session, "m-p8-4", "A")
    _assign(svc, alice, "memory", "m-p8-4", "B", "manager")
    svc.add_comment(bob, record_kind="memory", record_id="m-p8-4", body="one @A")
    assert _events_for("A")[-1].data["unread_count"] == 1
    svc.add_comment(bob, record_kind="memory", record_id="m-p8-4", body="two @A")
    assert _events_for("A")[-1].data["unread_count"] == 2


def test_reply_push_uses_reply_kind(svc, session, alice, bob, clean_bus):
    """门禁语义：回复走 comment_reply kind 的推送。"""
    _memory(session, "m-p8-5", "A")
    _assign(svc, alice, "memory", "m-p8-5", "B", "manager")
    parent = svc.add_comment(alice, record_kind="memory", record_id="m-p8-5",
                             body="root")["comment"]
    _events_for("A").clear()  # 清掉父评论阶段的事件，专注回复推送
    p8_bus._hist.clear()
    svc.add_reply(bob, record_kind="memory", record_id="m-p8-5",
                  parent_comment_id=parent["id"], body="a reply, no mention")
    evs = _events_for("A")
    assert evs and evs[0].data["kind"] == "comment_reply"


def test_self_mention_no_push(svc, session, alice, clean_bus):
    _memory(session, "m-p8-6", "A")
    svc.add_comment(alice, record_kind="memory", record_id="m-p8-6", body="note to self @A")
    assert _events_for("A") == [], "自通知抑制必须同样抑制推送"


def test_duplicate_notify_no_duplicate_push(svc, session, alice, bob, clean_bus):
    """DB 幂等（同评论同人同 kind 只一条）传导到推送：不重复推。"""
    _memory(session, "m-p8-7", "A")
    _assign(svc, alice, "memory", "m-p8-7", "B", "manager")
    svc.add_comment(bob, record_kind="memory", record_id="m-p8-7", body="hi @A")
    n1 = len(_events_for("A"))
    # 重复同步同一评论的通知计划（幂等路径：DB 已有 → 不新建 → 不推送）
    comment = session.execute(
        __import__("sqlalchemy").select(
            __import__("find_yourself.db.collaboration_models", fromlist=["Comment"]).Comment)
    ).scalars().first()
    ref = svc.resolve_record("memory", "m-p8-7")
    svc.notify_targets(bob, comment, ref,
                       [NotificationTarget(user_id="A", kind=NOTIFICATION_MENTION)])
    assert len(_events_for("A")) == n1, "重复落库被幂等抑制，推送也不得重复"


def test_reconnect_with_last_event_id_no_duplicates(clean_bus):
    """门禁核心②：断线重连带 Last-Event-ID → 已收过的事件不重复（总线机制）。"""
    ch = notification_channel("user-r")
    bus_publish = clean_bus.publish
    e1 = bus_publish(ch, "notification", {"n": 1})
    e2 = bus_publish(ch, "notification", {"n": 2})

    import asyncio

    # subscribe 是无限生成器：拿到缓冲重放 + 等待新事件。用任务+超时只取重放部分。
    async def collect_bounded(last_id: int, expect: int):
        out = []
        async def consume():
            async for ev in clean_bus.subscribe(ch, last_event_id=last_id):
                out.append(ev)
                if len(out) >= expect:
                    break
        task = asyncio.wait_for(consume(), timeout=2)
        try:
            await task
        except asyncio.TimeoutError:
            pass
        return out

    async def scenario():
        # 断点续传：从 e1 之后应立刻重放出 e2（缓冲里的历史），绝不重复 e1
        replay = await asyncio.wait_for(collect_bounded(e1.seq, 1), timeout=3)
        assert [ev.seq for ev in replay] == [e2.seq], "重连不得重放已收过的事件"
        # 新事件只推一次
        e3 = bus_publish(ch, "notification", {"n": 3})
        fresh = await asyncio.wait_for(collect_bounded(e2.seq, 1), timeout=3)
        assert fresh and fresh[-1].seq == e3.seq

    asyncio.run(asyncio.wait_for(scenario(), timeout=10))


def test_channel_for_is_per_user_and_auth_guarded(svc, alice, bob):
    assert svc.channel_for(alice) != svc.channel_for(bob)
    assert svc.channel_for(alice).endswith(":A")


def test_sse_frame_format_carries_no_body(svc, session, alice, bob, clean_bus):
    """SSE 帧形态：id/event/data 三段；data 为 JSON 且无正文（端点用 to_sse 输出）。"""
    _memory(session, "m-p8-8", "A")
    _assign(svc, alice, "memory", "m-p8-8", "B", "manager")
    svc.add_comment(bob, record_kind="memory", record_id="m-p8-8",
                    body="frame @A SECRET-BODY")
    ev = _events_for("A")[0]
    frame = ev.to_sse()
    assert frame.startswith(f"id: {ev.seq}\n")
    assert "event: notification" in frame
    assert "SECRET-BODY" not in frame
    data_line = next(l for l in frame.splitlines() if l.startswith("data:"))
    assert "summary" in data_line


def test_summary_is_positioning_string_without_body(svc, session, alice, bob, clean_bus):
    _memory(session, "m-p8-9", "A")
    _assign(svc, alice, "memory", "m-p8-9", "B", "manager")
    svc.add_comment(bob, record_kind="memory", record_id="m-p8-9",
                    body="unique-content-abcdef @A")
    summary = _events_for("A")[0].data["summary"]
    assert "unique-content-abcdef" not in summary
    assert summary == "B 在 memory 的评论中提到了你"
