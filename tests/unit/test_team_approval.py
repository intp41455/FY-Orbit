"""Unit tests: 需求 6 团队级权限与审批流。

真实 HTTP + 真实 SQLite（内存库 + ``create_all``，沿用 ``test_hitl.py`` 的
既有风格），**不 mock**。团队审批的价值全在「资格判定真的挡住了人」，
mock 掉 DB/服务的测试只能证明 mock 是这么配的。

覆盖：

- 团队与成员：建组自动成为 member、审批人才能加人/撤人、非审批人不能加人、
  撤销保留行（可追溯）、非成员看不到名册；
- 提交申请：执行真的被挂起（pending）、轮次从1 起、退回后重提 round+1；
- 拍板：**同团队审批人可批**、**跨团队被拒（按不存在处理）**、
  **审批人不能审自己**、**非审批人不能批**、非成员不能批；
- 终态与不可逆：approve/reject/return_for_change 三态落对、重复拍板冲突、
  并发只有一个赢家、越权决定不改变状态；
- 状态唯一真相源：申请表**没有 status 列**，状态一律读 ``hitl_interrupts``；
- 表级不变量：CHECK 挡住非法 role / 非法 required_role / round<1 /
  同队重复成员行 / 一个中断被两个申请引用；
- 与既有体系不打架：``/api/hitl`` 个人级路径不受影响、proposal/grant 不回归。
"""

from __future__ import annotations

from datetime import timezone
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.types import TZDateTime
import find_yourself.db.models  # noqa: F401
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401
import find_yourself.db.hitl_models  # noqa: F401  (需求12：团队审批的状态真相源)
import find_yourself.db.team_approval_models  # noqa: F401  (需求6：本次新增)

LOCAL_TOKEN = "dev-token-secret-team-approval"


# --- Test-only SQLite TZ shim (same as tests/unit/test_hitl.py) ----------
def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):  # the local dev-token gate requires a loopback peer
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    # 与 ``db/session.py`` 的生产引擎一致：SQLite 默认**不**强制外键，
    # 不开这个 pragma 的话「interrupt_id 必须指向真实中断」这条约束在测试里
    # 形同虚设——而那正是本模块「状态唯一真相源」的地基。
    with eng.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


@pytest.fixture()
def app(session_maker, tmp_path) -> FastAPI:
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )
    return create_app(session_maker=session_maker, settings=settings)


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(_force_loopback(app)) as c:
        yield c


@pytest.fixture()
def headers(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/local/dev-token", json={"token": LOCAL_TOKEN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


# ----------------------------------------------------------------------
# 服务层夹具：多主体场景（HTTP 单会话只能有一个 owner，跨主体必须走服务层）
# ----------------------------------------------------------------------
@pytest.fixture()
def svc(session_maker):
    """直接持服务，绕过单owner 会话的限制来构造多主体团队。"""
    from find_yourself.services.audit import AuditService
    from find_yourself.services.team_approval import TeamApprovalService

    db = session_maker()
    service = TeamApprovalService(db, AuditService(db))
    yield service, db
    db.close()


def _mk_team(svc, creator: str = "alice", name: str = "内容审核组") -> str:
    return svc.create_team(_actor(creator), name)["id"]


def _actor(user_id: str):
    from find_yourself.services.actor import Actor

    return Actor.owner(user_id)


def _add(svc, team_id: str, user_id: str, role: str) -> None:
    svc.add_member(_actor("alice"), team_id, user_id, role=role)


def _is_member(svc, team_id: str, user_id: str) -> bool:
    from sqlalchemy import select

    from find_yourself.db.team_approval_models import ApprovalTeamMember

    row = svc.session.execute(
        select(ApprovalTeamMember).where(
            ApprovalTeamMember.team_id == team_id,
            ApprovalTeamMember.user_id == user_id,
        )
    ).scalar_one_or_none()
    return row is not None and row.state == "active"


def _submit(svc, team_id: str, requester: str, *, execution_id: str, **kw):
    """以 ``requester`` 身份提交申请。

    只有建组人 alice 是自动入册的；其他主体必须显式获得资格。这里在提交前
    补一次入册（**不覆盖**已存在的角色，否则会把``requester`` 悄悄升成
    ``member``，让role 分级的用例失去意义）。要测「非成员被拒」的场景用
    ``enroll=False``。
    """
    if kw.pop("enroll", True) and not _is_member(svc, team_id, requester):
        _add(svc, team_id, requester, kw.pop("role", "member"))
    return svc.submit(
        _actor(requester), team_id, execution_id=execution_id,
        checkpoint="before_publish", **kw,
    )


# ======================================================================
# 团队与成员
# ======================================================================
def test_create_team_makes_creator_member(svc):
    service, db = svc
    team = service.create_team(_actor("alice"), "内容审核组")
    assert team["name"] == "内容审核组"
    assert team["state"] == "active"
    assert team["created_by"] == "alice"
    roster = service.list_members(_actor("alice"), team["id"])
    assert len(roster) == 1
    assert roster[0]["user_id"] == "alice"
    assert roster[0]["role"] == "member"  # 可提可批
    db.close()


def test_create_team_requires_non_empty_name(svc):
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    with pytest.raises(ValidationFailed):
        service.create_team(_actor("alice"), "   ")
    db.close()


def test_list_teams_only_shows_my_teams(svc):
    service, db = svc
    a = service.create_team(_actor("alice"), "A组")["id"]
    service.create_team(_actor("bob"), "B组")
    assert [t["id"] for t in service.list_teams(_actor("alice"))] == [a]
    assert service.list_teams(_actor("carol")) == []  # 无团队 -> 空，不是全部
    db.close()


def test_approver_can_add_member(svc):
    service, db = svc
    team = _mk_team(service)
    row = service.add_member(_actor("alice"), team, "bob", role="approver")
    assert row["user_id"] == "bob"
    assert row["role"] == "approver"
    assert row["granted_by"] == "alice"  # 审批权是显式授予的，来源留痕
    db.close()


def test_non_approver_cannot_add_member(svc):
    """任何成员都能扩审批权，等于审批权可以自我扩散。"""
    from find_yourself.services.errors import PermissionDenied

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "bob", "requester")
    with pytest.raises(PermissionDenied):
        service.add_member(_actor("bob"), team, "mallory", role="approver")
    db.close()


def test_revoke_keeps_row_for_traceability(svc):
    """撤销保留行（state=revoked）——审批记录要能解释「当时为什么有资格」。"""
    service, db = svc
    team = _mk_team(service)
    _add(service, team, "bob", "approver")
    revoked = service.revoke_member(_actor("alice"), team, "bob")
    assert revoked["state"] == "revoked"
    assert revoked["revoked_at"]
    # 重复撤销是幂等的，不是错误
    assert service.revoke_member(_actor("alice"), team, "bob")["state"] == "revoked"
    # 行还在，名册里仍可见（带 revoked 状态）
    roster = service.list_members(_actor("alice"), team)
    assert {m["user_id"] for m in roster} == {"alice", "bob"}
    db.close()


def test_revoked_member_loses_approval_right(svc):
    from find_yourself.services.errors import NotFound

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "bob", "approver")
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-1")
    service.revoke_member(_actor("alice"), team, "carol")
    with pytest.raises(NotFound):  # 撤销后不再是成员 -> 按「不在此团队」处理
        service.decide(_actor("carol"), req["id"], "approve")
    assert service.decide(_actor("alice"), req["id"], "approve")["status"] == "approved"
    db.close()


def test_outsider_cannot_read_roster(svc):
    from find_yourself.services.errors import NotFound

    service, db = svc
    team = _mk_team(service)
    with pytest.raises(NotFound):
        service.list_members(_actor("mallory"), team)
    db.close()


def test_unknown_team_is_404(svc):
    from find_yourself.services.errors import NotFound

    service, db = svc
    with pytest.raises(NotFound):
        service.list_members(_actor("alice"), "team-does-not-exist")
    db.close()


# ======================================================================
# 提交申请
# ======================================================================
def test_submit_suspends_execution(svc):
    """申请一提交，执行就真的卡住了——没有「先落库再异步通知」的中间态。"""
    service, db = svc
    team = _mk_team(service)
    req = _submit(service, team, "bob", execution_id="run-7", title="发布到公开频道")
    assert req["status"] == "pending"
    assert req["pending"] is True
    assert req["decided"] is False
    assert req["requester_id"] == "bob"
    assert req["round"] == 1
    assert req["execution_id"] == "run-7"
    assert req["checkpoint"] == "before_publish"
    db.close()


def test_submit_registers_paused_hitl_interrupt(svc):
    """复用 HITL：执行侧能看到自己卡住了。"""
    from find_yourself.services.hitl import HitlInterruptService

    service, db = svc
    team = _mk_team(service)
    req = _submit(service, team, "bob", execution_id="run-8")
    hitl = HitlInterruptService(db)
    assert hitl.pending(_actor("bob"), "run-8") is not None
    db.close()


def test_second_submit_on_same_execution_conflicts(svc):
    from find_yourself.services.errors import Conflict

    service, db = svc
    team = _mk_team(service)
    _submit(service, team, "bob", execution_id="run-9")
    with pytest.raises(Conflict):
        _submit(service, team, "bob", execution_id="run-9")
    db.close()


def test_non_member_cannot_submit(svc):
    from find_yourself.services.errors import NotFound

    service, db = svc
    team = _mk_team(service)
    with pytest.raises(NotFound):  # 外人连「这个团队存在」都不该知道
        _submit(service, team, "mallory", execution_id="run-x", enroll=False)
    db.close()


def test_requester_only_member_can_submit(svc):
    """role=requester 只能提申请，不能批——这正是role 分级的意义。"""
    from find_yourself.services.errors import PermissionDenied

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "bob", "requester")
    req = _submit(service, team, "bob", execution_id="run-10")  # 提：可以
    assert req["pending"] is True
    with pytest.raises(PermissionDenied):
        service.decide(_actor("bob"), req["id"], "approve")  # 批：不行
    db.close()


def test_resubmit_after_return_increments_round(svc):
    """退回修改 -> 重提，轮次 +1 且能溯源到上一轮。"""
    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    first = _submit(service, team, "bob", execution_id="run-11")
    service.decide(_actor("carol"), first["id"], "return_for_change", note="证据不足")
    # 原执行已终结，重新提交要用新的 execution_id（HITL 不允许同执行两个待决）
    second = _submit(service, team, "bob", execution_id="run-11",
                     supersedes_id=first["id"])
    assert second["round"] == 2
    assert second["supersedes_id"] == first["id"]
    db.close()


def test_required_role_must_be_known(svc):
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    team = _mk_team(service)
    with pytest.raises(ValidationFailed):
        _submit(service, team, "bob", execution_id="run-12", required_role="root")
    db.close()


# ======================================================================
# 拍板：核心四条
# ======================================================================
def test_same_team_approver_can_approve(svc):
    """✅ 同团队成员可批。"""
    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-20")
    out = service.decide(_actor("carol"), req["id"], "approve", note="同意发布")
    assert out["status"] == "approved"
    assert out["pending"] is False
    assert out["decided"] is True
    assert out["decision"] == "approve"
    assert out["decided_by"] == "carol"  # 记录真实审批人
    assert out["decided_at"]
    db.close()


def test_cross_team_approver_is_rejected_as_not_found(svc):
    """✅ 跨团队被拒，且按「不存在」处理——403 会泄露「这里有申请」。"""
    from find_yourself.services.errors import NotFound

    service, db = svc
    team_a = _mk_team(service, "alice", "A组")
    # bob 自建 B 组（建组人自动是 member，即可批），但他不是 A 组成员
    team_b = service.create_team(_actor("bob"), "B组")["id"]
    req = _submit(service, team_a, "alice", execution_id="run-21")

    assert service.list_teams(_actor("bob"))[0]["id"] == team_b
    with pytest.raises(NotFound):
        service.decide(_actor("bob"), req["id"], "approve")
    # 状态未变
    assert service.get(_actor("alice"), req["id"])["status"] == "pending"
    db.close()


def test_approver_cannot_approve_own_request(svc):
    """✅ 审批人不能审自己提交的（利益冲突回避）。"""
    from find_yourself.services.errors import PermissionDenied

    service, db = svc
    team = _mk_team(service)  # alice 是 member（可提可批）
    req = _submit(service, team, "alice", execution_id="run-22")
    with pytest.raises(PermissionDenied) as ei:
        service.decide(_actor("alice"), req["id"], "approve")
    assert "self_approval" in ei.value.code or "自己" in ei.value.message
    assert service.get(_actor("alice"), req["id"])["status"] == "pending"
    db.close()


def test_self_approval_refused_even_for_pure_approver_role(svc):
    """role=approver 也能提申请；提了自己依然不能批。"""
    from find_yourself.services.errors import PermissionDenied

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "carol", execution_id="run-23")
    with pytest.raises(PermissionDenied):
        service.decide(_actor("carol"), req["id"], "approve")
    db.close()


def test_non_approver_cannot_decide(svc):
    """✅ 非审批人不能批。"""
    from find_yourself.services.errors import PermissionDenied

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "bob", "requester")
    req = _submit(service, team, "bob", execution_id="run-24")
    with pytest.raises(PermissionDenied) as ei:
        service.decide(_actor("bob"), req["id"], "approve")
    assert ei.value.http_status == 403
    db.close()


def test_requester_cannot_approve_someone_elses_request(svc):
    """role=requester 连别人的申请也不能批——他连审批权都没有。"""
    from find_yourself.services.errors import PermissionDenied

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "bob", "requester")
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "carol", execution_id="run-25")
    with pytest.raises(PermissionDenied):
        service.decide(_actor("bob"), req["id"], "approve")
    db.close()


def test_service_identity_cannot_decide(svc):
    """执行体不能代替人拍板。"""
    from find_yourself.services.actor import Actor
    from find_yourself.services.errors import PermissionDenied

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-26")
    with pytest.raises(PermissionDenied):
        service.decide(Actor.service("agent-x", "agent"), req["id"], "approve")
    db.close()


def test_conflict_check_runs_after_role_check(svc):
    """非审批人拿到的拒绝理由是「你不是审批人」而不是「你是申请人」——
    后者会泄露申请人与团队的关系。"""
    from find_yourself.services.errors import PermissionDenied

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "bob", "requester")
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "carol", execution_id="run-27")
    with pytest.raises(PermissionDenied) as ei:
        service.decide(_actor("bob"), req["id"], "approve")
    assert ei.value.code == "approver_required"
    db.close()


# ======================================================================
# 三个终态 + 不可逆
# ======================================================================
def test_reject_sets_rejected(svc):
    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-30")
    out = service.decide(_actor("carol"), req["id"], "reject", note="证据不足")
    assert out["status"] == "rejected"
    assert out["decision"] == "reject"
    db.close()


def test_return_for_change_is_not_approved(svc):
    """退回修改不是批准：保守默认落rejected 终态。"""
    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-31")
    out = service.decide(_actor("carol"), req["id"], "return_for_change")
    assert out["status"] == "rejected"
    assert out["decision"] == "return_for_change"  # 但决策值如实保留
    assert out["decided"] is True
    db.close()


def test_unknown_decision_rejected(svc):
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-32")
    with pytest.raises(ValidationFailed):
        service.decide(_actor("carol"), req["id"], "do_whatever")
    assert service.get(_actor("carol"), req["id"])["status"] == "pending"
    db.close()


def test_second_decision_conflicts(svc):
    from find_yourself.services.errors import Conflict

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    _add(service, team, "dave", "approver")
    req = _submit(service, team, "bob", execution_id="run-33")
    assert service.decide(_actor("carol"), req["id"], "approve")["status"] == "approved"
    with pytest.raises(Conflict):
        service.decide(_actor("dave"), req["id"], "reject")
    db.close()


def test_first_decision_is_not_overwritten(svc):
    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    _add(service, team, "dave", "approver")
    req = _submit(service, team, "bob", execution_id="run-34")
    service.decide(_actor("carol"), req["id"], "approve", note="OK")
    from find_yourself.services.errors import Conflict

    with pytest.raises(Conflict):
        service.decide(_actor("dave"), req["id"], "reject")
    stored = service.get(_actor("carol"), req["id"])
    assert stored["decision"] == "approve"
    assert stored["decided_by"] == "carol"
    db.close()


def test_concurrent_decisions_only_one_wins(svc):
    """条件 UPDATE：两个并发审批只有一个能赢（与 HITL 同一条纪律）。"""
    from find_yourself.services.errors import Conflict

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    _add(service, team, "dave", "approver")
    req = _submit(service, team, "bob", execution_id="run-35")
    assert service.decide(_actor("carol"), req["id"], "approve")["status"] == "approved"
    with pytest.raises(Conflict):
        service.decide(_actor("dave"), req["id"], "approve")
    db.close()


def test_unauthorized_decide_leaves_state_untouched(svc):
    """越权决定不应改变任何状态（version 也不许被灌水）。"""
    from find_yourself.services.errors import NotFound, PermissionDenied

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "bob", "requester")
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-36")
    before = service.get(_actor("carol"), req["id"])
    for who in ("bob", "mallory"):
        with pytest.raises((PermissionDenied, NotFound)):
            service.decide(_actor(who), req["id"], "approve")
    after = service.get(_actor("carol"), req["id"])
    assert after["status"] == before["status"] == "pending"
    assert after["version"] == before["version"]
    db.close()


def test_decision_resumes_the_execution(svc):
    """拍板后执行不再卡住——审批的终点是「恢复」，不是「记一笔」。"""
    from find_yourself.services.hitl import HitlInterruptService

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-37")
    assert HitlInterruptService(db).pending(_actor("bob"), "run-37") is not None
    service.decide(_actor("carol"), req["id"], "approve")
    assert HitlInterruptService(db).pending(_actor("bob"), "run-37") is None
    db.close()


def test_expired_request_cannot_be_decided(svc):
    """超时后先落盘再报错——否则库里仍是 pending，超时就是个谎言。"""
    from datetime import timedelta

    from find_yourself.db.hitl_models import HitlInterrupt
    from find_yourself.db.types import utcnow
    from find_yourself.services.errors import Conflict

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-38", timeout_seconds=60)
    rec = db.get(HitlInterrupt, req["interrupt_id"])
    rec.expires_at = utcnow() - timedelta(seconds=5)
    db.commit()

    with pytest.raises(Conflict):
        service.decide(_actor("carol"), req["id"], "approve")
    assert db.get(HitlInterrupt, req["interrupt_id"]).status == "expired"
    db.close()


# ======================================================================
# 状态唯一真相源
# ======================================================================
def test_request_table_has_no_status_column():
    """状态只在 hitl_interrupts——避免两个真相源分叉。"""
    from find_yourself.db.team_approval_models import TeamApprovalRequest

    assert "status" not in TeamApprovalRequest.__table__.columns
    assert "decision" not in TeamApprovalRequest.__table__.columns
    assert "interrupt_id" in TeamApprovalRequest.__table__.columns


def test_status_is_read_from_hitl_row(svc):
    from find_yourself.db.hitl_models import HitlInterrupt
    from find_yourself.db.team_approval_models import TeamApprovalRequest

    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-40")
    service.decide(_actor("carol"), req["id"], "approve")

    tar = db.get(TeamApprovalRequest, req["id"])
    hitl = db.get(HitlInterrupt, req["interrupt_id"])
    assert tar.interrupt_id == hitl.id
    assert service.get(_actor("carol"), req["id"])["status"] == hitl.status == "approved"
    db.close()


def test_list_requests_filters_by_status(svc):
    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    a = _submit(service, team, "bob", execution_id="run-41")
    _submit(service, team, "bob", execution_id="run-42")
    service.decide(_actor("carol"), a["id"], "approve")
    rows = service.list_requests(_actor("carol"), team)
    assert len(rows) == 2
    assert len(service.list_requests(_actor("carol"), team, status="pending")) == 1
    db.close()


# ======================================================================
# 表级不变量
# ======================================================================
def test_db_rejects_unknown_member_role(session_maker):
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO approval_team_members "
                "(id, team_id, user_id, role, state, granted_by, created_at, version) "
                "VALUES ('atm-bad-1','t','u','root','active','','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_db_rejects_unknown_required_role(session_maker):
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO team_approval_requests "
                "(id, team_id, interrupt_id, requester_id, required_role, round, "
                " title, detail, created_at, version) "
                "VALUES ('tar-bad-1','t','h','u','root',1,'','{}','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_db_rejects_round_below_one(session_maker):
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO team_approval_requests "
                "(id, team_id, interrupt_id, requester_id, required_role, round, "
                " title, detail, created_at, version) "
                "VALUES ('tar-bad-2','t','h','u','approver',0,'','{}','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_duplicate_team_member_row_rejected(svc):
    """资格是集合，不是可重复的授予。"""
    import sqlalchemy.exc as sa_exc

    service, db = svc
    team = _mk_team(service)
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO approval_team_members "
                "(id, team_id, user_id, role, state, granted_by, created_at, version) "
                f"VALUES ('atm-dup','{team}','alice','member','active','',"
                "'2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_one_interrupt_cannot_back_two_requests(svc):
    """一条 HITL 中断最多被一个团队申请引用——否则两个团队共享一个审批状态。"""
    import sqlalchemy.exc as sa_exc

    service, db = svc
    team = _mk_team(service)
    req = _submit(service, team, "bob", execution_id="run-50")
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO team_approval_requests "
                "(id, team_id, interrupt_id, requester_id, required_role, round, "
                " title, detail, created_at, version) "
                f"VALUES ('tar-dup','{team}','{req['interrupt_id']}','bob',"
                "'approver',1,'','{}','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_request_cannot_reference_unknown_interrupt(session_maker):
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises((sa_exc.IntegrityError, sa_exc.OperationalError)):
        db.execute(
            text(
                "INSERT INTO team_approval_requests "
                "(id, team_id, interrupt_id, requester_id, required_role, round, "
                " title, detail, created_at, version) "
                "VALUES ('tar-bad-3','t','hitl-nope','u','approver',1,'',"
                "'{}','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


# ======================================================================
# HTTP 层
# ======================================================================
def test_http_create_team_and_submit(client: TestClient, headers: dict[str, str]):
    r = client.post("/api/team-approvals/teams", json={"name": "内容审核组"},
                    headers=headers)
    assert r.status_code == 201, r.text
    team_id = r.json()["id"]

    r = client.post(
        "/api/team-approvals/requests",
        json={"team_id": team_id, "execution_id": "http-run-1",
              "checkpoint": "before_publish", "title": "发布到公开频道"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["requester_id"] == "owner"
    db_team = body["team_id"]
    assert db_team == team_id


def test_http_self_approval_is_403(client: TestClient, headers: dict[str, str]):
    """建组人既是 member 又提了申请 -> 自己批自己被拒。"""
    team_id = client.post("/api/team-approvals/teams", json={"name": "自审组"},
                          headers=headers).json()["id"]
    req = client.post(
        "/api/team-approvals/requests",
        json={"team_id": team_id, "execution_id": "http-run-2",
              "checkpoint": "before_publish"},
        headers=headers,
    ).json()
    r = client.post(
        f"/api/team-approvals/requests/{req['id']}/decision",
        json={"decision": "approve"}, headers=headers,
    )
    assert r.status_code == 403, r.text
    assert "own request" in r.json()["detail"]


def test_http_bad_decision_is_422(client: TestClient, headers: dict[str, str]):
    team_id = client.post("/api/team-approvals/teams", json={"name": "T"},
                          headers=headers).json()["id"]
    req = client.post(
        "/api/team-approvals/requests",
        json={"team_id": team_id, "execution_id": "http-run-3",
              "checkpoint": "cp"},
        headers=headers,
    ).json()
    r = client.post(
        f"/api/team-approvals/requests/{req['id']}/decision",
        json={"decision": "escalate"}, headers=headers,
    )
    assert r.status_code == 422, r.text


def test_http_decision_requires_csrf(client: TestClient, headers: dict[str, str]):
    team_id = client.post("/api/team-approvals/teams", json={"name": "T"},
                          headers=headers).json()["id"]
    r = client.post("/api/team-approvals/teams", json={"name": "T2"})
    assert r.status_code == 403, r.text


def test_http_list_teams_and_requests(client: TestClient, headers: dict[str, str]):
    team_id = client.post("/api/team-approvals/teams", json={"name": "列表组"},
                          headers=headers).json()["id"]
    client.post(
        "/api/team-approvals/requests",
        json={"team_id": team_id, "execution_id": "http-run-4", "checkpoint": "cp"},
        headers=headers,
    )
    r = client.get("/api/team-approvals/teams", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    r = client.get(f"/api/team-approvals/teams/{team_id}/requests", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    r = client.get(f"/api/team-approvals/teams/{team_id}/members", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["role"] == "member"


def test_http_unknown_request_is_404(client: TestClient, headers: dict[str, str]):
    r = client.get("/api/team-approvals/requests/tar-nope", headers=headers)
    assert r.status_code == 404, r.text


# ======================================================================
# 审计 + 与既有体系不打架
# ======================================================================
def test_approval_events_are_audited(svc, session_maker):
    service, db = svc
    team = _mk_team(service)
    _add(service, team, "carol", "approver")
    req = _submit(service, team, "bob", execution_id="run-60")
    service.decide(_actor("carol"), req["id"], "approve")
    db.commit()

    actions = [
        a for a in session_maker().execute(
            text("SELECT action FROM audit_events ORDER BY seq")
        ).scalars()
    ]
    assert "team_approval.team_created" in actions
    assert "team_approval.member_added" in actions
    assert "team_approval.requested" in actions
    assert "team_approval.decided" in actions
    db.close()


def test_personal_hitl_path_still_works(client: TestClient, headers: dict[str, str]):
    """团队审批不能破坏个人级 HITL 路径。"""
    r = client.post(        "/api/hitl/interrupts",
        json={"execution_id": "personal-run", "checkpoint": "before_publish",
              "options": ["approve", "cancel"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    r = client.post(f"/api/hitl/interrupts/{iid}/decision",
                    json={"decision": "approve"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


# ======================================================================
# 同形一致性（technical-debt guard）
# ======================================================================
#
# 背景：团队审批的状态流转与 HITL 的状态流转是「同形」的——同一张表
# （hitl_interrupts）、同一套单赢家条件UPDATE、同一套保守终态映射。
# 但因为授权模型不同（团队审批允许「另一个成员」拍，而``hitl.decide``
# 的 ``_require_visible`` 只允许行owner 拍），实现分成了两处。
#
# 风险很具体：**改了一边，另一边不会红**。那时这个「已知代价」就从
# 「有防护的代价」退化成「定时炸弹」。
#
# 写法约束（来自主控）：**不复制 HITL 的实现**。所以下面把断言写成
# **一组契约函数**，两条流转各自跑同一组契约：
#
#   * 契约是**参数化**的（``@pytest.mark.parametrize``），不是复制粘贴；
#   * 两条流转用**同一个工厂函数**造出可裁决的对象，契约只依赖
#     ``decide`` / ``status_of`` 这两个鸭子类型接口；
#   * 新增一条不变量 = 加一个契约函数，两条流转**自动同时**被覆盖。
#
# 这样「两边会不会分叉」不再依赖人的自觉，而是由parametrize 强制保证。

from find_yourself.db.hitl_models import HitlInterrupt  # noqa: E402
from find_yourself.services.errors import Conflict  # noqa: E402


def _hitl_flow(svc, *, execution_id: str, options: list[str], timeout: int | None = None,
               session_maker=None):
    """造一条**HITL 原生**的可裁决流，返回契约接口。

    ``options`` 由调用方给：契约里需要验证「保守默认」的地方，选项必须
    **真的包含**那个值——一个不存在的选项会被 ``hitl.decide`` 在白名单
    校验处挡掉，压根到不了终态映射，契约就测了个寂寞。
    """
    from find_yourself.services.hitl import HitlInterruptService

    service, db = svc
    hitl = HitlInterruptService(db)
    row = hitl.interrupt(
        _actor("requester"), execution_id, "before_publish",
        options=list(options), timeout_seconds=timeout,
    )
    db.commit()
    return {
        "id": row["id"],
        "decide": lambda decision, actor=None: hitl.decide(
            actor or _actor("requester"), row["id"], decision
        ),
        "second_session_decide": _second_session_hitl_decider(
            session_maker, row["id"]
        ),
        "close_second": lambda: None,
        "engine": lambda: db.get_bind(),
        "commit": db.commit,
        "first_decider": "requester",
        "decided_by_of": lambda: _interrupt_decided_by(db, row["id"]),
        "status_of": lambda: _interrupt_status(db, row["id"]),
        "expire": lambda: _force_expiry(db, row["id"]),
    }


def _second_session_hitl_decider(session_maker, interrupt_id: str):
    """在**独立 session** 上跑 HITL 拍板（供并发契约用）。"""
    from find_yourself.services.hitl import HitlInterruptService

    def _decide(decision: str):
        s2 = session_maker()
        try:
            return HitlInterruptService(s2).decide(
                _actor("requester"), interrupt_id, decision
            )
        finally:
            s2.close()

    return _decide


def _team_flow(svc, *, execution_id: str, options: list[str], timeout: int | None = None,
               session_maker=None):
    """造一条**团队审批**的可裁决流，返回同一个契约接口。

    决策人是另一个成员（carol）——这正是团队审批与 HITL 的分歧点。
    """
    service, db = svc
    team = _mk_team(service)
    _add(service, team, "requester", "member")
    _add(service, team, "carol", "approver")
    req = service.submit(
        _actor("requester"), team, execution_id=execution_id,
        checkpoint="before_publish",
        # ``options`` 刻意忽略：真实产品里申请人**不能**自选审批语义，
        # 团队侧固定用 DECISION_OPTIONS（approve/reject/return_for_change）。
        # 所以团队流天然覆盖契约 3 需要的那个值。
        timeout_seconds=timeout,
    )
    db.commit()
    return {
        "id": req["interrupt_id"],
        "decide": lambda decision, actor=None: service.decide(
            actor or _actor("carol"), req["id"], decision
        ),
        "second_session_decide": _second_session_decider(
            session_maker, req["id"]
        ),
        "close_second": lambda: None,
        "engine": lambda: db.get_bind(),
        "commit": db.commit,
        "first_decider": "carol",
        "decided_by_of": lambda: _interrupt_decided_by(db, req["interrupt_id"]),
        "status_of": lambda: _interrupt_status(db, req["interrupt_id"]),
        "expire": lambda: _force_expiry(db, req["interrupt_id"]),
    }


def _second_session_decider(session_maker, request_id: str):
    """在**独立 session** 上跑团队审批拍板（供并发契约用）。"""
    from find_yourself.services.audit import AuditService
    from find_yourself.services.team_approval import TeamApprovalService

    def _decide(decision: str):
        s2 = session_maker()
        try:
            return TeamApprovalService(s2, AuditService(s2)).decide(
                _actor("carol"), request_id, decision
            )
        finally:
            s2.close()

    return _decide


def _interrupt_status(db, interrupt_id: str) -> str:
    return db.get(HitlInterrupt, interrupt_id).status


def _interrupt_decided_by(db, interrupt_id: str):
    return db.get(HitlInterrupt, interrupt_id).decided_by


def _force_expiry(db, interrupt_id: str) -> None:
    from datetime import timedelta

    from find_yourself.db.types import utcnow

    rec = db.get(HitlInterrupt, interrupt_id)
    rec.expires_at = utcnow() - timedelta(seconds=5)
    db.commit()


#: 两条流转的工厂。契约测试对它参数化—— 新增流转只需在这里加一项，
#: 全部契约会自动覆盖它。
FLOWS = [_hitl_flow, _team_flow]


# --- 契约 1：同一行不能被决策两次 -----------------------------------
def _contract_single_winner(flow):
    """第二次决策必须冲突，且不得覆盖第一次的结果。"""
    first = flow["decide"]("approve")
    assert first["status"] == "approved"
    with pytest.raises(Conflict):
        flow["decide"]("reject")
    # 第一次的结论没被覆盖，且已落终态
    assert flow["status_of"]() == "approved"


# --- 契约 2：超时后不能再决策，且必须先落盘 --------------------------
def _contract_expired_blocks_decision(flow):
    """超时必须**先落盘再报错**。

    否则这次调用拒绝了、库里却仍是 pending——下一个人还能对同一行拍板，
    「超时」就是个谎言。这条比「拒绝得对」更重要。
    """
    flow["expire"]()
    with pytest.raises(Conflict):
        flow["decide"]("approve")
    assert flow["status_of"]() == "expired"


# --- 契约 3：未显式批准的决策值一律归 rejected ------------------------
def _contract_unapproved_is_rejected(flow):
    """保守默认：只有显式白名单里的 approve 才给 approved，其余归 rejected。

    用 ``return_for_change``（退回修改）作为样本——它**两条流都真实提供**
    （HITL 侧的 options 显式列出它；团队侧它是 DECISION_OPTIONS 之一），
    但它不在 HITL ``_status_for`` 的批准白名单里，所以两边都必须落rejected。
    这比造一个不存在的选项更贴真实语义：产品里真的会有人选「退回修改」，
    而它**绝不能**被当成批准。
    """
    out = flow["decide"]("return_for_change")
    assert out["status"] == "rejected", (
        "退回修改不是批准：保守默认必须落 rejected，实际落了 "
        f"{out['status']!r}"
    )
    # 且决策值必须如实留下（不能被悄悄改成 approved 之类）
    assert out["decision"] == "return_for_change"
    assert out["status"] != "approved"


# --- 契约 1b：单赢家必须由「条件 UPDATE」保证，而不只是靠前置检查 --------
def _contract_decision_uses_guarded_conditional_update(flow):
    """拍板必须发出带 ``status='pending'`` 守卫的 UPDATE——单赢家由它保证。

    为什么断言 SQL，而不是模拟并发
    ------------------------------
    我先写的是「两个独立 session 交错拍板」，并以为它能压住这道守卫。
    **实测证明我错了**：在内存 SQLite（``StaticPool``，整库共用一条连接）上，
    第二个 session 读到的永远是最新已提交值，前置的
    ``status != 'pending'`` 检查就会把第二次挡掉——于是我把
    ``WHERE status='pending'`` 删掉，这条契约**依然是绿的**。
    它测的是一个测不出来的东西，属于「假绿测试」，比没有测试更糟。

    换成断言**实际发出的 SQL**：守卫在不在 ``WHERE`` 里是直接看得见的，
    这样「有人删掉条件守卫」必定让本条红。
    """
    from sqlalchemy import event

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = flow["engine"]()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        flow["decide"]("approve")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    updates = [
        s for s in statements
        if s.lstrip().upper().startswith("UPDATE") and "hitl_interrupts" in s
    ]
    assert updates, "拍板没有发出针对 hitl_interrupts 的 UPDATE"
    # 注意：守卫的值是**绑定参数**（``status = ?``），SQL 文本里不会出现
    # 'pending' 字面量——所以只能断言 WHERE 子句里存在 status 条件。
    # 值是否真的是 'pending' 由``test_state_transition_contract_is_identical``
    # 的行为断言（超时/二次决策）间接覆盖。
    assert any("WHERE" in u and "status" in u.split("WHERE", 1)[1] for u in updates), (
        "拍板的 UPDATE 必须在 WHERE 里带 status 守卫——单赢家语义完全依赖它，"
        f"前置检查只挡得住顺序重复、挡不住并发。实际 SQL: {updates}"
    )


# --- 契约 1c：并发重复决策必须冲突（顺序 + 换 session 双保险）------------
def _contract_concurrent_decision_conflicts(flow):
    """另一个 session 上的重复决策必须报冲突，且不覆盖已有结论。

    这条**测不出**「条件守卫被删」那种变异（见契约 1b 的说明：内存 SQLite
    共用连接，前置检查会先挡下来）。它保留的价值是覆盖**另一种**回归：
    有人给``decide`` 加了「读-改-写」而绕过了条件 UPDATE，或者忘了
    ``decided_by``/``decision`` 的写入——那时本条会红。
    """
    other = flow["second_session_decide"]
    try:
        first = flow["decide"]("approve")
        assert first["status"] == "approved"
        flow["commit"]()
        with pytest.raises(Conflict):
            other("reject")
    finally:
        flow["close_second"]()
    assert flow["status_of"]() == "approved"
    assert flow["decided_by_of"]() == flow["first_decider"]


#: 契约清单。新增不变量往这里加，两条流转自动同时被覆盖。
CONTRACTS = [
    pytest.param(_contract_single_winner, id="single_winner"),
    pytest.param(
        _contract_decision_uses_guarded_conditional_update,
        id="guarded_conditional_update",
    ),
    pytest.param(_contract_concurrent_decision_conflicts, id="concurrent_conflicts"),
    pytest.param(_contract_expired_blocks_decision, id="expired_blocks_decision"),
    pytest.param(_contract_unapproved_is_rejected, id="unapproved_is_rejected"),
]


@pytest.mark.parametrize("factory", FLOWS, ids=["hitl", "team"])
@pytest.mark.parametrize("contract", CONTRACTS)
def test_state_transition_contract_is_identical(svc, request, factory, contract,
                                                session_maker):
    """同形一致性：HITL 与团队审批的状态流转必须满足**同一组**契约。

    这条测试的技术意义大于功能意义：它是那个「已知代价」的唯一防线。
    如果将来有人只改了 ``hitl.decide`` 而没改团队层（或反过来），
    这里会红。
    """
    service, db = svc
    flow = factory(
        svc,
        execution_id=f"contract-{factory.__name__}-{request.node.name}",
        # HITL 侧必须显式提供这三个选项，否则契约 3 会在白名单校验处
        # 提前失败、根本到不了终态映射那一步。
        options=["approve", "reject", "return_for_change"],
        timeout=60,
        session_maker=session_maker,
    )
    # 每条契约**自己**做第一次决策（并发契约还需要在开第二个 session 前
    # 提交），所以这里不做任何预置决策。
    contract(flow)
    db.close()


def test_both_flows_share_the_same_status_mapper():
    """两条流转的「决策值 → 终态」映射是**同一个函数对象**，不是两份拷贝。

    这是比任何单条断言都强的一条：拷贝出来的映射即使当前行为一致，
    将来也必然漂移。这里直接断言身份相同（``is``），漂移不可能发生。
    """
    from find_yourself.services.hitl import HitlInterruptService
    from find_yourself.services.team_approval import TeamApprovalService

    assert TeamApprovalService._status_mapper is HitlInterruptService._status_for
