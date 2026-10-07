"""人工介入投票表决测试（A-人工介入-03 · P17）。

覆盖六条硬规矩，每条至少一条用例（逐条对应 ``services/hitl_vote.py`` 模块
docstring）：

1. 投票必须挂在真实 pending 中断上 —— :class:`TestOpenVote`
2. 候选必须是该中断 offered 选项的子集 —— :class:`TestCandidatesMustBeOffered`
3. 票只能投给已登记候选；权重必须是整数且 1..1000 —— :class:`TestBallots`
4. 一人一票，改票走 UPDATE —— :class:`TestOneBallotPerVoter`
5. 平手不许自动破，必须用户定音 —— :class:`TestTieRequiresUser`
6. 只有 owner 能收口 —— :class:`TestOwnerOnly`

外加迁移结构测试 :class:`TestMigration`，钉住「两条建表路径不漂移」。

⚠️ 本文件 import ``hitl_vote_models`` 就会把三张表注册到共享 ``Base`` metadata，
于是 conftest 的 ``create_all`` 建得出它们——与 ``hitl_models.py`` /
``dsl_canvas.py`` 的做法一致，**不需要**改共享的 ``tests/conftest.py``。
"""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

import find_yourself.db.hitl_vote_models  # noqa: F401  （见上）
from find_yourself.db.base import Base
from find_yourself.db.types import utcnow
from find_yourself.db.hitl_models import HitlInterrupt
from find_yourself.db.hitl_vote_models import (
    HitlVoteBallot,
    HitlVoteCandidate,
    HitlVoteSession,
)
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from find_yourself.services.hitl import HitlInterruptService
from find_yourself.services.hitl_vote import HitlVoteService

OWNER = "owner-vote-1"
OTHER = "owner-vote-2"

CANDIDATES = [
    {"value": "refactor", "label": "大重构", "proposer": "agent-a", "weight": 3},
    {"value": "hotfix", "label": "快修", "proposer": "agent-b", "weight": 1},
]


@pytest.fixture()
def engine():
    eng = sa.create_engine("sqlite://")

    @sa.event.listens_for(eng, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover - driver callback
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner(OWNER)


@pytest.fixture()
def svc(session) -> HitlVoteService:
    audit = AuditService(session)
    return HitlVoteService(session, audit, hitl=HitlInterruptService(session, audit))


def _interrupt(
    session: Session,
    interrupt_id: str = "it-1",
    *,
    owner_id: str = OWNER,
    options: list[str] | None = None,
    status: str = "pending",
) -> HitlInterrupt:
    """造一条中断。

    ⚠️ 已拍板的状态必须同时带 ``decision``/``decided_at``——``hitl_models`` 上有
    ``ck_hitl_interrupts_ck_hitl_decided_shape`` 这条既有 CHECK：resolved 行缺结论
    直接拒绝入库。所以「已拍板」不是把 status 改掉就行。
    """
    values = options if options is not None else ["refactor", "hotfix"]
    decided = status in ("approved", "rejected", "expired")
    row = HitlInterrupt(
        id=interrupt_id,
        owner_id=owner_id,
        execution_id="exec-1",
        checkpoint="plan_review",
        context={"question": "which way"},
        options=[{"value": v, "label": v} for v in values],
        status=status,
        decision="refactor" if decided else None,
        decided_by=OWNER if decided else None,
        decided_at=utcnow() if decided else None,
        reason="",
    )
    session.add(row)
    session.flush()
    return row


def _open_id(session, svc, owner, *, interrupt_id: str = "it-1") -> str:
    """Open the standard two-candidate vote and return its **generated** id.

    The service mints the id (``vote-<uuid>``); tests must not assume a literal,
    or every "not found" failure looks identical to a real bug.
    """
    _interrupt(session, interrupt_id)
    return svc.open_vote(
        owner, interrupt_id, question="哪条路", candidates=CANDIDATES,
    )["id"]





# --------------------------------------------------------------------------- #
# 规矩 1 · 投票必须挂在真实 pending 中断上
# --------------------------------------------------------------------------- #
class TestOpenVote:
    def test_opens_on_a_pending_interrupt(self, svc, session, owner):
        vid = _open_id(session, svc, owner)
        vote = svc.view(owner, vid)
        assert vote["status"] == "open"
        assert vote["interrupt_id"] == "it-1"
        assert vote["execution_id"] == "exec-1"
        assert vote["winner_option"] is None
        assert vote["resolved_by"] is None
        assert [c["option_value"] for c in vote["candidates"]] == ["hotfix", "refactor"]
        weights = {c["option_value"]: c["weight"] for c in vote["candidates"]}
        assert weights == {"refactor": 3, "hotfix": 1}

    def test_unknown_interrupt_is_not_found(self, svc, owner):
        with pytest.raises(NotFound) as exc:
            svc.open_vote(owner, "no-such-interrupt", question="q",
                          candidates=[{"value": "a"}])
        assert exc.value.code == "interrupt_not_found"

    def test_already_decided_interrupt_is_rejected(self, svc, session, owner):
        """给一条已有人拍板的 interrupt 开票 = 想绕开 HITL 的单点决策。"""
        _interrupt(session, status="approved")
        with pytest.raises(Conflict) as exc:
            svc.open_vote(owner, "it-1", question="q",
                          candidates=[{"value": "refactor"}])
        assert exc.value.code == "interrupt_not_pending"

    def test_another_owners_interrupt_is_invisible(self, svc, session):
        """不泄露存在性：按 not found 回，而不是 403。"""
        _interrupt(session, owner_id=OTHER)
        with pytest.raises(NotFound):
            svc.open_vote(Actor.owner(OWNER), "it-1", question="q",
                          candidates=[{"value": "refactor"}])

    def test_two_open_votes_on_one_interrupt_are_rejected(self, svc, session, owner):
        _open_id(session, svc, owner)
        with pytest.raises(Conflict) as exc:
            svc.open_vote(owner, "it-1", question="再来一次",
                          candidates=[{"value": "refactor"}])
        assert exc.value.code == "vote_already_open"


# --------------------------------------------------------------------------- #
# 规矩 2 · 候选必须来自该中断 offered 的选项
# --------------------------------------------------------------------------- #
class TestCandidatesMustBeOffered:
    def test_candidate_outside_the_offered_options_is_rejected(self, svc, session, owner):
        """投票不得引入中断没 offer 过的路——那就不是在回答同一个问题。"""
        _interrupt(session)
        with pytest.raises(ValidationFailed) as exc:
            svc.open_vote(owner, "it-1", question="q",
                          candidates=[{"value": "refactor"},
                                      {"value": "rewrite-everything"}])
        assert exc.value.code == "candidate_not_offered"
        assert "rewrite-everything" in exc.value.message

    def test_empty_candidate_list_is_rejected(self, svc, session, owner):
        _interrupt(session)
        with pytest.raises(ValidationFailed) as exc:
            svc.open_vote(owner, "it-1", question="q", candidates=[])
        assert exc.value.code == "candidates_required"

    def test_duplicate_candidate_is_rejected(self, svc, session, owner):
        _interrupt(session)
        with pytest.raises(ValidationFailed) as exc:
            svc.open_vote(owner, "it-1", question="q",
                          candidates=[{"value": "refactor"}, {"value": "refactor"}])
        assert exc.value.code == "duplicate_candidate"

    def test_blank_candidate_value_is_rejected(self, svc, session, owner):
        _interrupt(session)
        with pytest.raises(ValidationFailed) as exc:
            svc.open_vote(owner, "it-1", question="q", candidates=[{"value": "  "}])
        assert exc.value.code == "candidate_value_required"


# --------------------------------------------------------------------------- #
# 规矩 3 · 票根与权重
# --------------------------------------------------------------------------- #
class TestBallots:
    def test_ballot_for_an_unregistered_candidate_is_rejected(self, svc, session, owner):
        """候选必须在开票时登记过；开完票临时加一个选项不行。"""
        vid = _open_id(session, svc, owner)
        with pytest.raises(ValidationFailed) as exc:
            svc.cast_ballot(owner, vid, voter="agent-c", option="third-way")
        assert exc.value.code == "candidate_not_registered"

    @pytest.mark.parametrize("bad", [0, -1, 1001])
    def test_out_of_range_weights_are_rejected(self, svc, session, owner, bad):
        vid = _open_id(session, svc, owner)
        with pytest.raises(ValidationFailed) as exc:
            svc.cast_ballot(owner, vid, voter="agent-a", option="refactor", weight=bad)
        assert exc.value.code == "weight_out_of_range"

    @pytest.mark.parametrize("bad", [3.5, "heavy", None, True])
    def test_non_integer_weights_are_rejected_rather_than_truncated(
        self, svc, session, owner, bad,
    ):
        """🔴 ``int(3.5)`` 会静默变成 3——那等于伪造票数。

        权重是决定平手与否的关键量，所以只收 int，浮点/字符串/bool 一律拒。
        （HTTP 侧另有 pydantic 的 int 校验兜底，但服务层不能依赖调用方先过一遍。）
        """
        vid = _open_id(session, svc, owner)
        with pytest.raises(ValidationFailed) as exc:
            svc.cast_ballot(owner, vid, voter="agent-a", option="refactor", weight=bad)
        assert exc.value.code == "weight_invalid"

    def test_blank_voter_is_rejected(self, svc, session, owner):
        vid = _open_id(session, svc, owner)
        with pytest.raises(ValidationFailed) as exc:
            svc.cast_ballot(owner, vid, voter="", option="refactor")
        assert exc.value.code == "voter_required"

    def test_voting_on_a_closed_vote_is_rejected(self, svc, session, owner):
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="agent-a", option="refactor", weight=5)
        svc.close(owner, vid)
        with pytest.raises(Conflict) as exc:
            svc.cast_ballot(owner, vid, voter="agent-b", option="hotfix")
        assert exc.value.code == "vote_closed"


class TestOneBallotPerVoter:
    def test_recast_updates_instead_of_inserting_a_second_ballot(self, svc, session, owner):
        """一人一票：改票走 UPDATE，所以「票数」恒等于「投票人数」。"""
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="agent-a", option="refactor", weight=2)
        svc.cast_ballot(owner, vid, voter="agent-a", option="hotfix", weight=2)
        rows = session.execute(sa.select(HitlVoteBallot)).scalars().all()
        assert len(rows) == 1
        assert rows[0].option_value == "hotfix"
        tally = svc.tally(owner, vid)
        assert tally["ballot_count"] == 1
        assert tally["counts"] == {"hotfix": 2}

    def test_distinct_voters_each_get_a_row(self, svc, session, owner):
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="agent-a", option="refactor")
        svc.cast_ballot(owner, vid, voter="agent-b", option="hotfix")
        tally = svc.tally(owner, vid)
        assert tally["ballot_count"] == 2
        assert tally["total_weight"] == 2
        assert tally["tied"] is True


# --------------------------------------------------------------------------- #
# 计票
# --------------------------------------------------------------------------- #
class TestTally:
    def test_tally_is_weighted_not_a_headcount(self, svc, session, owner):
        """按权重算：2 票权重 3 的 refactor（合计 6）胜过 3 票权重 1 的 hotfix（合计 3）。"""
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="a1", option="refactor", weight=3)
        svc.cast_ballot(owner, vid, voter="a2", option="refactor", weight=3)
        for i in range(3):
            svc.cast_ballot(owner, vid, voter=f"b{i}", option="hotfix", weight=1)
        tally = svc.tally(owner, vid)
        assert tally["counts"] == {"refactor": 6, "hotfix": 3}
        assert tally["ballot_count"] == 5           # 人数
        assert tally["total_weight"] == 9           # 权重和
        assert tally["leader"] == "refactor"
        assert tally["tied"] is False

    def test_no_ballots_yet_has_no_leader(self, svc, session, owner):
        """还没人投就说「领先者是谁」是编数据。"""
        vid = _open_id(session, svc, owner)
        tally = svc.tally(owner, vid)
        assert tally["counts"] == {}
        assert tally["leader"] is None
        assert tally["tied"] is False


# --------------------------------------------------------------------------- #
# 规矩 5 · 平手必须用户定音
# --------------------------------------------------------------------------- #
class TestTieRequiresUser:
    def test_even_tally_refuses_to_auto_resolve(self, svc, session, owner):
        """🔴 需求「用户定音」四个字真正落地的地方。

        票数平手时**不许**自动破——409 vote_tied，并把并列项返回给前端渲染二选一。
        拒绝之后投票必须仍是 open，没有被偷偷定音。
        """
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="agent-a", option="refactor", weight=1)
        svc.cast_ballot(owner, vid, voter="agent-b", option="hotfix", weight=1)
        with pytest.raises(Conflict) as exc:
            svc.close(owner, vid)
        assert exc.value.code == "vote_tied"
        assert exc.value.http_status == 409
        assert "hotfix" in exc.value.message
        assert "refactor" in exc.value.message
        assert svc.view(owner, vid)["status"] == "open"
        assert svc.view(owner, vid)["winner_option"] is None

    def test_owner_tiebreak_resolves_and_is_recorded_as_such(self, svc, session, owner):
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="agent-a", option="refactor")
        svc.cast_ballot(owner, vid, voter="agent-b", option="hotfix")
        out = svc.close(owner, vid, option="hotfix", reason="时间紧，走快修")
        assert out["status"] == "resolved"
        assert out["winner_option"] == "hotfix"
        # 事后能看出这一槌不是票数决定的
        assert out["resolved_by"] == "owner_tiebreak"
        assert out["tie_options"] == ["hotfix", "refactor"]
        assert out["reason"] == "时间紧，走快修"

    def test_tiebreak_for_an_unregistered_option_is_rejected(self, svc, session, owner):
        vid = _open_id(session, svc, owner)
        with pytest.raises(ValidationFailed) as exc:
            svc.close(owner, vid, option="third-way")
        assert exc.value.code == "candidate_not_registered"


# --------------------------------------------------------------------------- #
# 收口与回灌
# --------------------------------------------------------------------------- #
class TestClose:
    def test_unique_winner_resolves_by_tally_and_feeds_the_interrupt(self, svc, session, owner):
        """票数唯一最高 → resolved_by='tally'，且**真的**把结论写回 HITL 中断。"""
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="a1", option="refactor", weight=3)
        svc.cast_ballot(owner, vid, voter="a2", option="refactor", weight=3)
        svc.cast_ballot(owner, vid, voter="b0", option="hotfix", weight=1)

        out = svc.close(owner, vid)
        assert out["status"] == "resolved"
        assert out["winner_option"] == "refactor"
        assert out["resolved_by"] == "tally"

        interrupt = session.get(HitlInterrupt, "it-1")
        assert interrupt.status != "pending"
        assert interrupt.decision == "refactor"
        # 回灌带上计票明细，事后能追「为什么是这个结论」
        assert interrupt.resolution["via"] == "hitl_vote"
        assert interrupt.resolution["vote_id"] == vid
        assert interrupt.resolution["resolved_by"] == "tally"
        assert interrupt.resolution["counts"] == {"refactor": 6, "hotfix": 1}

    def test_closing_with_zero_votes_is_refused(self, svc, session, owner):
        """没票就收口 = 编一个结论。"""
        vid = _open_id(session, svc, owner)
        with pytest.raises(ValidationFailed) as exc:
            svc.close(owner, vid)
        assert exc.value.code == "no_votes"
        assert session.get(HitlInterrupt, "it-1").status == "pending"

    def test_closing_twice_is_rejected(self, svc, session, owner):
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="agent-a", option="refactor", weight=5)
        svc.close(owner, vid)
        with pytest.raises(Conflict) as exc:
            svc.close(owner, vid, option="hotfix")
        assert exc.value.code == "vote_closed"

    def test_cancel_leaves_the_interrupt_still_waiting(self, svc, session, owner):
        """作废投票 ≠ 解决了争执：中断仍在等一个真人的决定。"""
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="agent-a", option="refactor")
        out = svc.cancel(owner, vid, reason="投票开错了")
        assert out["status"] == "cancelled"
        assert out["winner_option"] is None        # 不编一个假的胜出选项
        assert out["resolved_by"] == "cancelled"
        assert session.get(HitlInterrupt, "it-1").status == "pending"


class TestOwnerOnly:
    """规矩 6：Agent 可以投票，但落槌必须是人。"""

    def test_a_service_actor_cannot_close(self, svc, session, owner):
        vid = _open_id(session, svc, owner)
        svc.cast_ballot(owner, vid, voter="agent-a", option="refactor", weight=5)
        with pytest.raises(PermissionDenied):
            svc.close(Actor.service("svc-1", "worker"), vid)

    def test_a_service_actor_cannot_cancel(self, svc, session, owner):
        vid = _open_id(session, svc, owner)
        with pytest.raises(PermissionDenied):
            svc.cancel(Actor.service("svc-1", "worker"), vid)

    def test_a_service_actor_may_open_and_vote(self, svc, session, owner):
        """service 身份**能开票、能投票**，但不能收口。

        投票功能存在的前提就是 Agent 能投票——否则用户只能绕过投票直接拍板，
        整个投票层是死代码。开门禁只落在 close/cancel 上。
        """
        worker = Actor.service("svc-1", "worker")
        _interrupt(session)
        vid = svc.open_vote(
            owner, "it-1", question="q", candidates=[{"value": "refactor"}],
        )["id"]

        svc.cast_ballot(worker, vid, voter="svc-1", option="refactor")
        tally = svc.tally(owner, vid)
        assert tally["counts"] == {"refactor": 1}
        assert tally["ballot_count"] == 1

        # 但收口依然只有 owner 能做
        with pytest.raises(PermissionDenied):
            svc.close(worker, vid)

    def test_listing_votes_requires_an_owner(self, svc, session):
        with pytest.raises(PermissionDenied):
            svc.list_votes(Actor.service("svc-1", "worker"))

    def test_listing_is_scoped_to_the_owner(self, svc, session, owner):
        _open_id(session, svc, owner)
        _interrupt(session, "it-2", owner_id=OTHER)
        svc.open_vote(Actor.owner(OTHER), "it-2", question="q",
                      candidates=[{"value": "refactor"}])
        mine = svc.list_votes(owner)["items"]
        assert [v["interrupt_id"] for v in mine] == ["it-1"]
        theirs = svc.list_votes(Actor.owner(OTHER))["items"]
        assert [v["interrupt_id"] for v in theirs] == ["it-2"]


def _only_vote_id(session) -> str:
    return session.execute(
        sa.select(HitlVoteSession.id).order_by(HitlVoteSession.id)
    ).scalars().first()


# --------------------------------------------------------------------------- #
# 迁移结构
# --------------------------------------------------------------------------- #
def _migration():
    return importlib.import_module("migrations.versions.0047_hitl_vote")


def _run(conn, mod) -> None:
    mod.op = Operations(MigrationContext.configure(conn))
    mod.upgrade()


TABLES = ("hitl_vote_sessions", "hitl_vote_candidates", "hitl_vote_ballots")


def _snapshot(conn) -> dict:
    insp = sa.inspect(conn)
    out = {}
    for table in TABLES:
        out[table] = None if not insp.has_table(table) else {
            "cols": sorted(c["name"] for c in insp.get_columns(table)),
            "idx": sorted(i["name"] for i in insp.get_indexes(table)),
        }
    return out


class TestMigration:
    def test_fresh_create_all_db_and_migrated_db_agree(self):
        """两条建表路径必须收敛。

        ``0001_initial.py:37`` 用 ``Base.metadata.create_all`` 建全表，所以**新建库**
        上本迁移的三张表在 0001 阶段就建好了、护栏全部跳过；**已迁移库**上本迁移
        才真正执行建表。两条路径的列与索引集合必须逐字相同。
        """
        mod = _migration()

        fresh = sa.create_engine("sqlite://")
        with fresh.begin() as conn:
            Base.metadata.create_all(bind=conn)
            _run(conn, mod)
            fresh_snap = _snapshot(conn)

        # 已迁移库：0047 之前不存在这三张表
        legacy = sa.create_engine("sqlite://")
        with legacy.begin() as conn:
            _run(conn, mod)
            legacy_snap = _snapshot(conn)

        assert fresh_snap == legacy_snap
        for snap in (fresh_snap, legacy_snap):
            for table in TABLES:
                assert snap[table] is not None, table

    def test_migration_index_names_match_the_orm(self):
        """索引名必须与 ORM 声明逐字一致，否则同一列上会留下两个重复索引。

        ``index=True`` 的列按 ``db/base.py`` 的 ``ix_%(table_name)s_%(column_0_N_name)s``
        推出；显式 ``Index(...)`` 用字面名。两边对不上就是漂移。
        """
        mod = _migration()
        declared = {name for name, _t, _cols in mod._INDEXES}
        orm = {
            i.name
            for t in (HitlVoteSession, HitlVoteCandidate, HitlVoteBallot)
            for i in t.__table__.indexes
        }
        assert declared == orm, f"migration={declared} orm={orm}"

    def test_migration_is_idempotent(self):
        mod = _migration()
        eng = sa.create_engine("sqlite://")
        with eng.begin() as conn:
            Base.metadata.create_all(bind=conn)
            _run(conn, mod)
            first = _snapshot(conn)
            _run(conn, mod)
            _run(conn, mod)
            assert _snapshot(conn) == first

    def test_downgrade_removes_all_three_tables_and_is_replayable(self):
        mod = _migration()
        eng = sa.create_engine("sqlite://")
        with eng.begin() as conn:
            Base.metadata.create_all(bind=conn)
            _run(conn, mod)
            before = _snapshot(conn)

            mod.op = Operations(MigrationContext.configure(conn))
            mod.downgrade()
            insp = sa.inspect(conn)
            for table in TABLES:
                assert not insp.has_table(table), table

            _run(conn, mod)
            assert _snapshot(conn) == before

    def test_resolution_shape_constraint_actually_bites(self):
        """「半写的结论」不得落盘：resolved 行缺 winner_option 必须被 CHECK 拒。"""
        eng = sa.create_engine("sqlite://")
        with Session(eng) as s:
            Base.metadata.create_all(bind=s.get_bind())
            s.add(HitlVoteSession(
                id="v1", owner_id=OWNER, interrupt_id="it-1", execution_id="e1",
                question="q", status="resolved", winner_option=None,
                resolved_by="tally", reason="", version=1,
            ))
            with pytest.raises(sa.exc.IntegrityError):
                s.flush()
            s.rollback()

    def test_cancelled_row_may_not_carry_a_winner(self):
        """作废的行若带一个胜出选项，就是把「撤销」伪装成「结论」。"""
        eng = sa.create_engine("sqlite://")
        with Session(eng) as s:
            Base.metadata.create_all(bind=s.get_bind())
            s.add(HitlVoteSession(
                id="v2", owner_id=OWNER, interrupt_id="it-1", execution_id="e1",
                question="q", status="cancelled", winner_option="refactor",
                resolved_by="cancelled", reason="", version=1,
            ))
            with pytest.raises(sa.exc.IntegrityError):
                s.flush()
            s.rollback()

    @pytest.mark.parametrize("bad", [0, 1001])
    def test_ballot_weight_bounds_are_enforced_in_the_database(self, bad):
        """服务层已经拒了越界权重；DB 层也要拒（防绕过服务直写）。"""
        eng = sa.create_engine("sqlite://")
        with Session(eng) as s:
            Base.metadata.create_all(bind=s.get_bind())
            s.add(HitlVoteBallot(
                session_id="v1", voter="a", option_value="refactor",
                weight=bad, reason="",
            ))
            with pytest.raises(sa.exc.IntegrityError):
                s.flush()
            s.rollback()