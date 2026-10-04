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


# --- 落库闸门：未白名单 kind 拒写、白名单 kind 可写 ------------------- #
def test_notify_targets_rejects_unwhitelisted_kind(svc, session, alice):
    """comment_reply 未进白名单 → 落库被拒（fail loud），且不留半行。"""
    _task(session, "t1", "A")
    ref = svc.resolve_record("task", "t1")
    cid = svc.add_comment(alice, record_kind="task", record_id="t1", body="hi")["comment"]["id"]
    row = session.get(Comment, cid)
    with pytest.raises(ValidationFailed):
        svc.notify_targets(alice, row, ref, [NotificationTarget("B", NOTIFICATION_REPLY)])
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


def test_comment_reply_is_designed_but_not_yet_whitelisted():
    assert NOTIFICATION_REPLY not in NOTIFICATION_KINDS
    assert NOTIFICATION_MENTION in NOTIFICATION_KINDS


# --- kind 白名单单一真源 ---------------------------------------------- #
def test_kind_whitelist_expr_is_derived_and_matches_0030():
    from sqlalchemy import CheckConstraint

    assert NOTIFICATION_KIND_IN == "kind IN ('mention')"
    exprs = [
        str(c.sqltext) for c in Notification.__table__.constraints
        if isinstance(c, CheckConstraint) and "kind IN" in str(c.sqltext)
    ]
    assert exprs == [NOTIFICATION_KIND_IN]


def test_migration_0030_kind_check_matches_model():
    """模型派生的 CHECK 与已落盘 0030 迁移逐字一致 → 无 fresh/migrated schema 分叉。"""
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0030_collaboration.py"
    text = path.read_text(encoding="utf-8")
    assert "kind IN ('mention')" in text
    assert NOTIFICATION_KIND_IN == "kind IN ('mention')"


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
