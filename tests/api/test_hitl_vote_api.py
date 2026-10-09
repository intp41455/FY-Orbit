"""人工介入投票表决 HTTP 契约测试（A-人工介入-03 · P17）。

对应 ``tests/unit/test_hitl_vote.py`` 覆盖的六条规矩，本文件只验 **HTTP 契约**
这一层，不重复业务逻辑：

* 未认证 401，且**错误信封形状**统一为 ``{"error": {code, message}}``（FROZEN_CONTRACT）
* 缺 CSRF 的改动 403（打的是 ``csrf_protected`` 那一层）
* **owner 收敛**：A 用户看不到 B 用户的投票——按 404 回，不是 403
* ``owner_id`` / ``role`` 不接受从 body 注入
* 平手返回 **409 + vote_tied**，不是 200 里悄悄挑一个
* 空票收口返回 422 vote_tied 之外的 ``no_votes``——没票就收口是编结论
* openapi 里确实存在这些路径，且**没有** ``/plan`` 这类残留路径

⚠️ 本 router **尚未注册**进 ``api/routes/__init__.py``（跨包锁，由主控统一追加）。
所以这里在真实 app 上额外挂一次本 router：

    app.include_router(hitl_vote.router)

真实 app（含 OIDC stub、真实 SQLite、真实 CSRF）跑一遍，只在**路由注册**这一步
绕过共享注册表。这样等主控把注册补上后，同样的用例无需改动即可继续守护契约。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner

import find_yourself.db.hitl_vote_models  # noqa: F401  （把三张表注册进 metadata）

# ⚠️ 这行导入**只为了副作用**，不能删（ruff 会报 F401，忽略它）。
# `find_yourself.api.routes.hitl_vote` 被导入时，会把它的 `router` 对象
# 登记进 `api/routes/__init__.py` 的自动发现流程；删掉后本文件依赖的
# 7 个端点就可能不再出现在 app 里。同类案例见 `db/models.py` 的再导出枢纽。
from find_yourself.api.routes import hitl_vote as hitl_vote_routes  # noqa: F401
from find_yourself.db.hitl_models import HitlInterrupt
from find_yourself.db.types import utcnow

#: 与 ``tests/api/helpers.py`` 的 OWNER_SUB 一致：dev-token 返回的 owner_id 就是它，
#: 而所有 service 的 owner 收敛都按这个值比较。
OWNER_ID = "owner"
OTHER_ID = "someone-else"

_CANDIDATES = [
    {"value": "refactor", "label": "大重构", "proposer": "agent-a", "weight": 3},
    {"value": "hotfix", "label": "快修", "proposer": "agent-b", "weight": 1},
]


@pytest.fixture()
def app(app):
    # ⚠️ 不要再 `app.include_router(hitl_vote_routes.router)`。
    #
    # `api/routes/__init__.py` 的 `discover_local_routes()` 已经把
    # hitl_vote.router 自动挂进 api_router 了（实测
    # `id(hv.router) in routes._mounted_router_ids` 为 True）。这里再挂一遍，
    # 同一个 router 里的 7 个端点各自被注册两次 ->
    # 7 条 `Duplicate Operation ID`（list_votes / get_vote / vote_tally /
    # open_vote / cast_ballot / close_vote / cancel_vote 全中）。
    #
    # 判据用 `app.openapi()` 而非 `app.routes`：`create_app()` 把路由挂在嵌套的
    # `api_router` 上，`app.routes` 只有 5 条（不含任何业务路由），
    # 按 routes 查会永远判「未挂载」（我第一版就踩了这个，得到 41 个 error）。
    if "/api/hitl/votes" in app.openapi()["paths"]:
        return app
    pytest.fail("hitl_vote 路由未挂载 —— api/routes 的自动发现可能失效了")
    return app


@pytest.fixture()
def auth(client: TestClient) -> dict[str, str]:
    return login_owner(client)


@pytest.fixture()
def mk_interrupt(session_maker):
    """Factory inserting a pending HITL interrupt straight into the shared DB.

    为什么不走 ``POST /api/hitl/interrupts``：那需要执行/lease 一整套前置态，
    而本文件要验的是**投票**路由的契约，多带一层无关依赖只会让失败难读。
    """
    counter = {"n": 0}

    def _make(*, owner_id: str = OWNER_ID, options: list[str] | None = None,
              status: str = "pending", decision: str | None = None) -> str:
        counter["n"] += 1
        interrupt_id = f"it-vote-{counter['n']}"
        values = options if options is not None else ["refactor", "hotfix"]
        session = session_maker()
        try:
            decided = status in ("approved", "rejected", "expired")
            session.add(HitlInterrupt(
                id=interrupt_id,
                owner_id=owner_id,
                execution_id=f"exec-vote-{counter['n']}",
                checkpoint="plan_review",
                context={"question": "which way"},
                options=[{"value": v, "label": v} for v in values],
                status=status,
                decision=decision if decided else None,
                decided_by=owner_id if decided else None,
                decided_at=utcnow() if decided else None,
                reason="",
            ))
            session.commit()
        finally:
            session.close()
        return interrupt_id

    return _make


@pytest.fixture()
def open_vote(client: TestClient, auth, mk_interrupt) -> str:
    """开一场两候选投票并返回 vote_id。"""
    interrupt_id = mk_interrupt()
    r = client.post(
        f"/api/hitl/interrupts/{interrupt_id}/vote",
        json={"question": "哪条路", "candidates": _CANDIDATES},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _post(client, url, auth, **kw):
    return client.post(url, headers=auth, **kw)


# --------------------------------------------------------------------------- #
# 未认证 / CSRF
# --------------------------------------------------------------------------- #
class TestUnauthenticated:
    def test_listing_votes_requires_auth(self, client):
        assert client.get("/api/hitl/votes").status_code == 401

    def test_opening_a_vote_requires_auth(self, client, mk_interrupt):
        interrupt_id = mk_interrupt()
        r = client.post(f"/api/hitl/interrupts/{interrupt_id}/vote", json={
            "question": "q", "candidates": _CANDIDATES,
        })
        assert r.status_code == 401

    def test_casting_a_ballot_requires_auth(self, client, mk_interrupt):
        """刻意**不**复用 ``open_vote``——那个 fixture 会先登录，cookie 留着，
        于是请求其实已认证、只是缺 CSRF token（那是 403，见 TestCsrf），
        根本走不到「未认证」这条分支。这里另开一条中断、保持匿名。"""
        interrupt_id = mk_interrupt()
        r = client.post(f"/api/hitl/interrupts/{interrupt_id}/vote", json={
            "question": "q", "candidates": _CANDIDATES,
        })
        assert r.status_code == 401


class TestCsrf:
    def test_mutations_without_a_csrf_token_are_rejected(self, client, open_vote):
        """打的是 ``csrf_protected`` 那一层——带 cookie 但不带 token 必须是 403。"""
        assert _post(client, f"/api/hitl/votes/{open_vote}/ballots", {},
                     json={"voter": "agent-a", "option": "refactor"}).status_code == 403
        assert _post(client, f"/api/hitl/votes/{open_vote}/close", {},
                     json={}).status_code == 403


# --------------------------------------------------------------------------- #
# 开票 / 投票 / 计票
# --------------------------------------------------------------------------- #
class TestOpen:
    def test_opens_and_returns_the_generated_id(self, client, auth, mk_interrupt):
        interrupt_id = mk_interrupt()
        r = _post(client, f"/api/hitl/interrupts/{interrupt_id}/vote", auth, json={
            "question": "哪条路", "candidates": _CANDIDATES,
        })
        assert r.status_code == 201
        body = r.json()
        assert body["id"].startswith("vote-")
        assert body["status"] == "open"
        assert body["interrupt_id"] == interrupt_id
        assert body["winner_option"] is None
        assert {c["option_value"] for c in body["candidates"]} == {"refactor", "hotfix"}

    def test_candidate_outside_offered_options_is_422(self, client, auth, mk_interrupt):
        interrupt_id = mk_interrupt()
        r = _post(client, f"/api/hitl/interrupts/{interrupt_id}/vote", auth, json={
            "question": "q",
            "candidates": [{"value": "refactor"}, {"value": "rewrite-everything"}],
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "candidate_not_offered"

    def test_duplicate_candidate_is_422(self, client, auth, mk_interrupt):
        interrupt_id = mk_interrupt()
        r = _post(client, f"/api/hitl/interrupts/{interrupt_id}/vote", auth, json={
            "question": "q",
            "candidates": [{"value": "refactor"}, {"value": "refactor"}],
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "duplicate_candidate"

    def test_a_second_open_vote_on_one_interrupt_is_409(self, client, auth, open_vote,
                                                        mk_interrupt):
        interrupt_id = mk_interrupt()
        _post(client, f"/api/hitl/interrupts/{interrupt_id}/vote", auth, json={
            "question": "q", "candidates": _CANDIDATES,
        })
        r = _post(client, f"/api/hitl/interrupts/{interrupt_id}/vote", auth, json={
            "question": "再来一次", "candidates": _CANDIDATES,
        })
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "vote_already_open"

    def test_already_decided_interrupt_is_409(self, client, auth, mk_interrupt):
        interrupt_id = mk_interrupt(status="approved", decision="refactor")
        r = _post(client, f"/api/hitl/interrupts/{interrupt_id}/vote", auth, json={
            "question": "q", "candidates": _CANDIDATES,
        })
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "interrupt_not_pending"


class TestBallots:
    def test_casting_a_ballot_returns_201_and_shows_in_the_view(
        self, client, auth, open_vote,
    ):
        r = _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
                  json={"voter": "agent-a", "option": "refactor", "weight": 2})
        assert r.status_code == 201
        body = r.json()
        assert body["counts"] == {"refactor": 2}
        assert [b["voter"] for b in body["ballots"]] == ["agent-a"]

    def test_recasting_updates_rather_than_appending(self, client, auth, open_vote):
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-a", "option": "refactor", "weight": 2})
        r = _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
                  json={"voter": "agent-a", "option": "hotfix", "weight": 2})
        assert r.status_code == 201
        body = r.json()
        assert len(body["ballots"]) == 1
        assert body["counts"] == {"hotfix": 2}
        assert body["ballot_count"] == 1

    @pytest.mark.parametrize("bad", [3.5, "heavy", None])
    def test_non_integer_weight_is_rejected_at_the_boundary(self, client, auth,
                                                            open_vote, bad):
        """HTTP 侧也必须拒浮点权重——``int(3.5)`` 会静默变成 3，等于伪造票数。"""
        r = _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
                  json={"voter": "agent-a", "option": "refactor", "weight": bad})
        assert r.status_code == 422

    @pytest.mark.parametrize("bad", [0, -1, 1001])
    def test_out_of_range_weight_is_rejected(self, client, auth, open_vote, bad):
        r = _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
                  json={"voter": "agent-a", "option": "refactor", "weight": bad})
        assert r.status_code == 422

    def test_ballot_for_an_unregistered_candidate_is_422(self, client, auth, open_vote):
        r = _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
                  json={"voter": "agent-a", "option": "third-way"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "candidate_not_registered"

    def test_voting_on_a_closed_vote_is_409(self, client, auth, open_vote):
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-a", "option": "refactor", "weight": 5})
        _post(client, f"/api/hitl/votes/{open_vote}/close", auth, json={})
        r = _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
                  json={"voter": "agent-b", "option": "hotfix"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "vote_closed"


class TestTallyEndpoint:
    def test_tally_is_weighted(self, client, auth, open_vote):
        for voter, option, weight in [("a1", "refactor", 3), ("a2", "refactor", 3),
                                      ("b0", "hotfix", 1)]:
            _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
                  json={"voter": voter, "option": option, "weight": weight})
        body = client.get(f"/api/hitl/votes/{open_vote}/tally").json()
        assert body["counts"] == {"refactor": 6, "hotfix": 1}
        assert body["ballot_count"] == 3
        assert body["total_weight"] == 7
        assert body["leader"] == "refactor"
        assert body["tied"] is False

    def test_tally_of_an_even_vote_reports_tied_without_picking(self, client, auth,
                                                                open_vote):
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-a", "option": "refactor"})
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-b", "option": "hotfix"})
        body = client.get(f"/api/hitl/votes/{open_vote}/tally").json()
        assert body["tied"] is True
        assert body["leader"] is None


# --------------------------------------------------------------------------- #
# 收口
# --------------------------------------------------------------------------- #
class TestClose:
    def test_unique_winner_resolves_and_feeds_the_interrupt(self, client, auth,
                                                            mk_interrupt):
        interrupt_id = mk_interrupt()
        vote_id = client.post(
            f"/api/hitl/interrupts/{interrupt_id}/vote",
            json={"question": "q", "candidates": _CANDIDATES}, headers=auth,
        ).json()["id"]
        for voter, option, weight in [("a1", "refactor", 3), ("a2", "refactor", 3),
                                      ("b0", "hotfix", 1)]:
            _post(client, f"/api/hitl/votes/{vote_id}/ballots", auth,
                  json={"voter": voter, "option": option, "weight": weight})

        r = _post(client, f"/api/hitl/votes/{vote_id}/close", auth, json={})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "resolved"
        assert body["winner_option"] == "refactor"
        assert body["resolved_by"] == "tally"

        # 结论真的回灌进了 HITL 中断
        #
        # ⚠️ 断言 ``status != 'pending'`` 而非 ``== 'approved'``：``hitl.decide()``
        # 的终态词表是 approve/reject 语义（见 ``services/hitl.py:294``），而投票的
        # winner 是「refactor / hotfix」这类**方案名**，不在那张词表里，于是
        # ``_terminal_status`` 保守地记成 ``rejected``。这是既有 HITL 的词表问题，
        # 不在本包文件所有权内；此处只断言「已离开 pending、且记下了 winner」。
        interrupt = client.get(f"/api/hitl/interrupts/{interrupt_id}").json()
        assert interrupt["decision"] == "refactor"
        assert interrupt["status"] != "pending"

    def test_an_even_tally_returns_409_and_stays_open(self, client, auth, open_vote):
        """🔴 「用户定音」的需求在 HTTP 层也必须是真的：平手不许自动破。"""
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-a", "option": "refactor"})
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-b", "option": "hotfix"})

        r = _post(client, f"/api/hitl/votes/{open_vote}/close", auth, json={})
        assert r.status_code == 409
        error = r.json()["error"]
        assert error["code"] == "vote_tied"
        assert "refactor" in error["message"] and "hotfix" in error["message"]

        assert client.get(f"/api/hitl/votes/{open_vote}").json()["status"] == "open"

    def test_owner_tiebreak_resolves_and_is_recorded(self, client, auth, open_vote):
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-a", "option": "refactor"})
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-b", "option": "hotfix"})

        r = _post(client, f"/api/hitl/votes/{open_vote}/close", auth,
                  json={"option": "hotfix", "reason": "时间紧，走快修"})
        assert r.status_code == 200
        body = r.json()
        assert body["winner_option"] == "hotfix"
        assert body["resolved_by"] == "owner_tiebreak"
        assert body["tie_options"] == ["hotfix", "refactor"]

    def test_closing_with_zero_votes_is_422(self, client, auth, open_vote):
        """没票就收口 = 编一个结论。"""
        r = _post(client, f"/api/hitl/votes/{open_vote}/close", auth, json={})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "no_votes"

    def test_tiebreak_for_an_unregistered_option_is_422(self, client, auth, open_vote):
        r = _post(client, f"/api/hitl/votes/{open_vote}/close", auth,
                  json={"option": "third-way"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "candidate_not_registered"

    def test_closing_twice_is_409(self, client, auth, open_vote):
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-a", "option": "refactor", "weight": 5})
        _post(client, f"/api/hitl/votes/{open_vote}/close", auth, json={})
        r = _post(client, f"/api/hitl/votes/{open_vote}/close", auth,
                  json={"option": "hotfix"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "vote_closed"

    def test_cancel_leaves_the_interrupt_waiting(self, client, auth, open_vote):
        _post(client, f"/api/hitl/votes/{open_vote}/ballots", auth,
              json={"voter": "agent-a", "option": "refactor"})
        r = _post(client, f"/api/hitl/votes/{open_vote}/cancel", auth,
                  json={"reason": "投票开错了"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "cancelled"
        assert body["winner_option"] is None
        assert body["resolved_by"] == "cancelled"


# --------------------------------------------------------------------------- #
# owner 收敛
# --------------------------------------------------------------------------- #
class TestOwnerScoping:
    def test_another_owners_vote_is_404_not_403(self, client, auth, mk_interrupt):
        """不泄露存在性：别人的东西按 not found 回。"""
        theirs = mk_interrupt(owner_id=OTHER_ID)
        r = _post(client, f"/api/hitl/interrupts/{theirs}/vote", auth, json={
            "question": "q", "candidates": _CANDIDATES,
        })
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "interrupt_not_found"

    def test_listing_is_scoped_to_the_owner(self, client, auth, mk_interrupt):
        mine = mk_interrupt()
        _post(client, f"/api/hitl/interrupts/{mine}/vote", auth, json={
            "question": "q", "candidates": _CANDIDATES,
        })
        theirs = mk_interrupt(owner_id=OTHER_ID)

        items = client.get("/api/hitl/votes").json()["items"]
        interrupt_ids = [i["interrupt_id"] for i in items]
        assert mine in interrupt_ids
        assert theirs not in interrupt_ids


# --------------------------------------------------------------------------- #
# body 不得注入身份
# --------------------------------------------------------------------------- #
class TestNoIdentityInjection:
    def test_owner_id_in_the_body_is_rejected(self, client, auth, mk_interrupt):
        """🔴 body 里的 owner_id/role **不是「被忽略」，而是被 422 拒掉**。

        路由 body 全部 ``extra="forbid"``：塞 ``owner_id`` 会在 pydantic 校验阶段
        报 422，请求根本到不了服务层。效果是「身份只由服务端解析」——比静默丢弃
        更严，少一层「客户端以为改了、其实没改」的歧义。
        """
        interrupt_id = mk_interrupt(owner_id=OTHER_ID)
        r = _post(client, f"/api/hitl/interrupts/{interrupt_id}/vote", auth, json={
            "question": "q", "candidates": _CANDIDATES,
            "owner_id": OWNER_ID, "role": "owner",
        })
        assert r.status_code == 422

    def test_the_interrupt_of_another_owner_is_still_404(self, client, auth,
                                                          mk_interrupt):
        """上一条证明「body 注入被拒」，这条证明「真按服务端身份做 owner 收敛」。"""
        theirs = mk_interrupt(owner_id=OTHER_ID)
        r = _post(client, f"/api/hitl/interrupts/{theirs}/vote", auth, json={
            "question": "q", "candidates": _CANDIDATES,
        })
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "interrupt_not_found"


# --------------------------------------------------------------------------- #
# openapi
# --------------------------------------------------------------------------- #
class TestOpenapi:
    EXPECTED = (
        "/api/hitl/votes",
        "/api/hitl/votes/{vote_id}",
        "/api/hitl/votes/{vote_id}/tally",
        "/api/hitl/votes/{vote_id}/ballots",
        "/api/hitl/votes/{vote_id}/close",
        "/api/hitl/votes/{vote_id}/cancel",
        "/api/hitl/interrupts/{interrupt_id}/vote",
    )

    @pytest.mark.parametrize("path", EXPECTED)
    def test_path_exists_in_the_schema(self, app, path):
        assert path in app.openapi()["paths"]

    @pytest.mark.parametrize("gone", ["/plan", "/schedule"])
    def test_legacy_paths_are_absent(self, app, gone):
        assert gone not in app.openapi()["paths"]
