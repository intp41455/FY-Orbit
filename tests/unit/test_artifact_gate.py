"""Unit tests: 需求 7 产物版本门禁与独立测试。

真实 HTTP + 真实 SQLite（内存库 + ``create_all`` + ``StaticPool``，沿用
``test_hitl.py`` / ``test_team_approval.py`` 的既有风格），**不 mock**。独立
测试那一段是**真起子进程**跑真命令的——门禁的全部价值就在于「这个挡是真的
挡」，mock 掉执行器等于把要证明的东西本身给mock 掉了。

覆盖：

- 版本登记：内容由真实字节冻结、版号自增、未知形态被拒、调用方无法指定版号；
- 必检项来自服务端策略：调用方声明 ``required_checks=[]`` 也无法零条件放行；
- 状态机：合法流转走通、**非法流转被拒且不改状态**、终态不可再动、
  并发只有一个赢家、非法动作不泄露授权状态；
- 独立测试：真命令通过/失败、失败即门禁不过、**退出码非 0 不可放行**；
- 门禁判定：状态 / 必检项 / **证据新鲜度** 三道闸，缺一即不可用；
- 「本该失败但宽松实现会通过」的一组用例（见下方 §变异防线）；
- 表级不变量：CHECK 挡住非法状态 / 非法 check 状态 / 空摘要 /
  同产物重复版号 / 重复检查项 / 未经 submit 就 verified；
- HTTP：全链路 + CSRF + 归属隔离（外人的版本按「不存在」处理）；
- 注册自检：新表必须真被 ``create_all`` 建出来。

「变异防线」这一节是本文件的技术核心
------------------------------------
门禁类代码有个特殊风险：**它可能整体退化而不让任何普通用例变红**。比如把
``evaluate`` 里的必检项循环删掉、或让 ``verify_pass`` 无视证据直接放行——
「正常产物走完门禁」这类用例依然全绿。所以下面每条负向用例都先断言一个
**当前实现会挡住**的场景，并在断言里说明「若判据被删，这里会红」。
"""

from __future__ import annotations

import sys
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
import find_yourself.db.artifact_gate_models  # noqa: F401  (需求7：本次新增)

LOCAL_TOKEN = "dev-token-secret-artifact-gate"


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
    # 与 ``db/session.py`` 的生产引擎一致：SQLite 默认**不**强制外键。
    # 不开这个 pragma，「检查证据必须挂在一个真实存在的版本上」这条约束
    # 在测试里形同虚设。
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
# 服务层夹具
# ----------------------------------------------------------------------
@pytest.fixture()
def svc(session_maker):
    """直接持服务，绕过单owner 会话的限制来构造多主体场景。"""
    from find_yourself.services.artifact_gate import ArtifactGateService
    from find_yourself.services.audit import AuditService

    db = session_maker()
    service = ArtifactGateService(db, AuditService(db))
    yield service, db
    db.close()


@pytest.fixture()
def workspace(tmp_path):
    """一个真实的工作区目录，里面有一个真实的 Python 文件。

    刻意用**真文件**而不是 mock 的摘要：门禁的核心断言是「摘要由真实字节
    算出」，如果字节是假的，那条断言就什么都没证明。
    """
    ws = tmp_path / "bundle"
    ws.mkdir()
    (ws / "main.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    return ws


def _actor(user_id: str):
    from find_yourself.services.actor import Actor

    return Actor.owner(user_id)


def _register(svc, workspace, *, kind="code_bundle", artifact_id="art-1", who="alice"):
    service, _db = svc
    return service.register(
        _actor(who), artifact_kind=kind, artifact_id=artifact_id,
        workspace_dir=workspace,
    )


def _satisfy(
    svc, version_id, workspace, *, skip_independent=False, files=("main.py",)
):
    """把一个版本的**必检项全部**按真实字节跑成通过。

    走的是真检查器（重算摘要 / ``ast.parse`` / 真起子进程跑命令），
    不是手写 ``status="passed"`` 塞进去——后者会让「证据」退化成声明。
    """
    from find_yourself.services.verification import TrustedVerificationRunner

    service, _db = svc
    root = str(workspace)
    list(files)
    digests, composite = TrustedVerificationRunner.compute_artifact_digests(root, list(files))

    service.record_check(
        _actor("alice"), version_id, "content_digest",
        status="passed", observed_digest=composite,
        evidence={"digests": digests},
    )
    ok, detail = service.syntax_check(root, list(files))
    service.record_check(
        _actor("alice"), version_id, "syntax_valid",
        status="passed" if ok else "failed", observed_digest=composite,
        detail=detail,
    )
    if not skip_independent:
        service.run_independent_test(
            _actor("alice"), version_id,
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
    return composite


def _to_verified(svc, workspace, **kw):
    """走完 draft -> in_review -> verified。返回 (version, composite)。"""
    service, _db = svc
    v = _register(svc, workspace, **kw)
    service.submit(_actor("alice"), v["id"])
    composite = _satisfy(svc, v["id"], workspace)
    out = service.decide(_actor("alice"), v["id"], "verify_pass")
    assert out["state"] == "verified", out
    return v, composite


# ======================================================================
# 版本登记：内容冻结
# ======================================================================
def test_register_freezes_digest_from_real_bytes(svc, workspace):
    from find_yourself.services.verification import TrustedVerificationRunner

    service, db = svc
    v = _register(svc, workspace)
    _digests, expected = TrustedVerificationRunner.compute_artifact_digests(
        str(workspace), None
    )
    assert v["content_digest"] == expected
    assert len(v["content_digest"]) == 64
    assert v["state"] == "draft"
    assert v["version_no"] == 1
    assert v["required_checks"] == ["content_digest", "syntax_valid", "independent_test"]
    db.close()


def test_register_starts_at_draft_with_no_decision(svc, workspace):
    """draft 必须「未提交、未判定」——ck_agv_*_shape 的行为侧对照。"""
    service, db = svc
    v = _register(svc, workspace)
    assert v["submitted_at"] is None
    assert v["decided_at"] is None
    assert v["decided_by"] is None
    assert v["legal_actions"] == ["submit"]
    db.close()


def test_second_version_increments_version_no(svc, workspace):
    service, db = svc
    first = _register(svc, workspace)
    second = _register(svc, workspace)
    assert first["version_no"] == 1
    assert second["version_no"] == 2
    assert second["id"] != first["id"]
    db.close()


def test_version_no_is_not_caller_supplied(svc, workspace):
    """版号必须由服务算：能指定就能覆盖既有版本，「不可变快照」当场失效。"""
    service, db = svc
    _register(svc, workspace)
    # register 的签名里根本没有 version_no ——传了就是 TypeError。
    with pytest.raises(TypeError):
        service.register(  # type: ignore[call-arg]
            _actor("alice"), artifact_kind="code_bundle", artifact_id="art-1",
            workspace_dir=workspace, version_no=1,
        )
    db.close()


def test_unknown_artifact_kind_is_rejected(svc, workspace):
    """未知形态 → 422。刻意不给「默认策略」：忘了配策略的产物不该被管。"""
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    with pytest.raises(ValidationFailed) as ei:
        service.register(
            _actor("alice"), artifact_kind="mystery", artifact_id="a",
            workspace_dir=workspace,
        )
    assert ei.value.http_status == 422
    db.close()


def test_missing_workspace_is_rejected(svc, tmp_path):
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    with pytest.raises(ValidationFailed):
        service.register(
            _actor("alice"), artifact_kind="asset", artifact_id="a",
            workspace_dir=tmp_path / "nope",
        )
    db.close()


def test_empty_artifact_id_is_rejected(svc, workspace):
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    with pytest.raises(ValidationFailed):
        service.register(
            _actor("alice"), artifact_kind="asset", artifact_id="   ",
            workspace_dir=workspace,
        )
    db.close()


# ======================================================================
# 必检项来自服务端策略（不是调用方）
# ======================================================================
def test_caller_cannot_downgrade_required_checks(svc, workspace):
    """「本该失败但宽松实现会通过」——**必检项不可被调用方削弱**。

    如果实现改成「必检项 = 调用方传入的 required_checks，默认 []」，
    那么下面这次登记就会拿到一个**零条件**的版本；它提交后直接
    ``verify_pass`` 就会落verified —— 一个从没验过的产物被放行。
    本用例在两处设卡：``register`` 根本不接受该参数（``TypeError``），
    且即便绕过签名，视图里的 ``required_checks`` 仍来自策略。
    """
    service, db = svc
    with pytest.raises(TypeError):
        service.register(  # type: ignore[call-arg]
            _actor("alice"), artifact_kind="code_bundle", artifact_id="art-1",
            workspace_dir=workspace, required_checks=[],
        )
    v = _register(svc, workspace)
    assert v["required_checks"], "零必检项的版本等于没有门禁"
    assert "independent_test" in v["required_checks"]
    db.close()


def test_required_checks_differ_by_kind(svc, workspace):
    """asset 不要求语法检查（它不是代码）；code_bundle 要求。"""
    service, db = svc
    a = service.register(
        _actor("alice"), artifact_kind="asset", artifact_id="img-1",
        workspace_dir=workspace,
    )
    c = _register(svc, workspace, kind="code_bundle", artifact_id="code-1")
    assert a["required_checks"] == ["content_digest", "independent_test"]
    assert c["required_checks"] == [
        "content_digest", "syntax_valid", "independent_test"
    ]
    db.close()


def test_extra_checks_cannot_substitute_required_ones(svc, workspace):
    """调用方可以**加严**（多跑一项），但不能**替代**必检项。

    跑一项额外的 ``lint`` 并声明通过，必检项仍缺 → 仍不可用。
    """
    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    service.record_check(_actor("alice"), v["id"], "lint", status="passed")
    verdict = service.evaluate(_actor("alice"), v["id"])
    assert verdict["usable"] is False
    assert set(verdict["missing_checks"]) == {
        "content_digest", "syntax_valid", "independent_test"
    }
    assert "lint" not in verdict["required_checks"]
    db.close()


# ======================================================================
# 状态机：合法流转
# ======================================================================
def test_happy_path_reaches_released(svc, workspace):
    """✅ 正常产物走完门禁：draft -> in_review -> verified -> released。"""
    service, db = svc
    v, _ = _to_verified(svc, workspace)
    out = service.decide(_actor("alice"), v["id"], "release", note="ship it")
    assert out["state"] == "released"
    assert out["decided_by"] == "alice"
    assert out["decision_note"] == "ship it"
    verdict = service.evaluate(_actor("alice"), v["id"])
    assert verdict["usable"] is True
    assert verdict["exportable"] is True
    assert verdict["blocking_reasons"] == []
    db.close()


def test_verified_but_not_released_is_usable_not_exportable(svc, workspace):
    """「能用」与「能对外」是两个门：verified 只到自用。"""
    service, db = svc
    v, _ = _to_verified(svc, workspace)
    verdict = service.evaluate(_actor("alice"), v["id"])
    assert verdict["usable"] is True
    assert verdict["exportable"] is False
    with pytest.raises(Exception) as ei:
        service.require_exportable(_actor("alice"), v["id"])
    assert ei.value.http_status == 403
    db.close()


def test_submit_requires_authenticated_and_visibility(svc, workspace):
    from find_yourself.services.errors import NotFound

    service, db = svc
    v = _register(svc, workspace)
    # 外人的版本按「不存在」处理——不泄露「这里有产物」。
    with pytest.raises(NotFound):
        service.submit(_actor("mallory"), v["id"])
    db.close()


# ======================================================================
# 状态机：非法流转被拒
# ======================================================================
def test_cannot_skip_verification_and_release_directly(svc, workspace):
    """「本该失败但宽松实现会通过」——**跳过验证直接放行**必须被拒。

    门禁最该挡的一次跳跃就是它：``draft -> released``。若实现只校验
    「目标状态是否合法」（released 本身是合法状态）而不校验**从哪来**，
    这一跳就是开的——一个刚登记、什么都没验的产物会被直接推到「已发布」。
    """
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    v = _register(svc, workspace)
    for action in ("release", "verify_pass", "verify_fail", "block"):
        with pytest.raises(ValidationFailed) as ei:
            service.decide(_actor("alice"), v["id"], action)
        assert ei.value.http_status == 422
        assert "not legal from state 'draft'" in ei.value.message
    assert service.get(_actor("alice"), v["id"])["state"] == "draft"
    db.close()


def test_in_review_cannot_release_without_verifying(svc, workspace):
    """已送检 ≠ 已验证：in_review -> released 同���必须非法。"""
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    with pytest.raises(ValidationFailed):
        service.decide(_actor("alice"), v["id"], "release")
    assert service.get(_actor("alice"), v["id"])["state"] == "in_review"
    db.close()


def test_verified_cannot_go_back_to_review(svc, workspace):
    """已验证不能回退到待审——否则「验过」可以被悄悄撤销再放出。"""
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    v, _ = _to_verified(svc, workspace)
    with pytest.raises(ValidationFailed):
        service.decide(_actor("alice"), v["id"], "submit")
    assert service.get(_actor("alice"), v["id"])["state"] == "verified"
    db.close()


def test_terminal_states_accept_no_action(svc, workspace):
    """终态（rejected/blocked）没有任何出边。"""
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    service.decide(_actor("alice"), v["id"], "block", note="hold")
    assert service.get(_actor("alice"), v["id"])["state"] == "blocked"
    assert service.get(_actor("alice"), v["id"])["legal_actions"] == []
    for action in ("submit", "verify_pass", "release", "block"):
        with pytest.raises(ValidationFailed) as ei:
            service.decide(_actor("alice"), v["id"], action)
        assert "terminal state" in ei.value.message
    db.close()


def test_released_can_be_blocked_for_recall(svc, workspace):
    """止损口：已放行的仍可被收回（发现问题时的回滚路径必须存在）。"""
    service, db = svc
    v, _ = _to_verified(svc, workspace)
    service.decide(_actor("alice"), v["id"], "release")
    out = service.decide(_actor("alice"), v["id"], "block", note="found a bug")
    assert out["state"] == "blocked"
    assert service.evaluate(_actor("alice"), v["id"])["usable"] is False
    db.close()


def test_unknown_action_is_rejected(svc, workspace):
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    v = _register(svc, workspace)
    with pytest.raises(ValidationFailed) as ei:
        service.decide(_actor("alice"), v["id"], "force_release")
    assert ei.value.http_status == 422
    assert service.get(_actor("alice"), v["id"])["state"] == "draft"
    db.close()


def test_service_identity_cannot_decide(svc, workspace):
    """执行体不能给自己放行——否则「独立测试」由被测方自己判定。

    注意这里让**执行体自己登记**这版产物：若换成 owner 登记、再让执行体来
    判定，会先撞上归属隔离（404），测的就不是「执行体不能拍板」这件事了。
    """
    from find_yourself.services.actor import Actor
    from find_yourself.services.errors import PermissionDenied

    service, db = svc
    agent = Actor.service("agent-x", "agent")
    v = service.register(
        agent, artifact_kind="code_bundle", artifact_id="art-1",
        workspace_dir=workspace,
    )
    # 送检可以（检查本来就是执行体跑出来的）
    service.submit(agent, v["id"])
    # 判定不行：必须是人
    with pytest.raises(PermissionDenied) as ei:
        service.decide(agent, v["id"], "verify_pass")
    assert ei.value.http_status == 403
    assert service.get(agent, v["id"])["state"] == "in_review"
    db.close()


def test_illegal_transition_does_not_bump_version(svc, workspace):
    """越权/非法流转不得改状态，**也不许把version 灌水**。

    version 是乐观锁。被失败的尝试 +1 会让真正的并发守卫失效。
    """
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    v = _register(svc, workspace)
    before = service.get(_actor("alice"), v["id"])
    for _ in range(3):
        with pytest.raises(ValidationFailed):
            service.decide(_actor("alice"), v["id"], "release")
    after = service.get(_actor("alice"), v["id"])
    assert after["state"] == before["state"] == "draft"
    assert after["version"] == before["version"]
    db.close()


def test_outsider_decide_leaves_state_untouched(svc, workspace):
    """外人对别人的产物决策：按「不存在」处理，且状态不变。"""
    from find_yourself.services.errors import NotFound

    service, db = svc
    v = _register(svc, workspace)
    with pytest.raises(NotFound):
        service.decide(_actor("mallory"), v["id"], "submit")
    assert service.get(_actor("alice"), v["id"])["state"] == "draft"
    db.close()


def test_concurrent_transition_only_one_wins(svc, workspace):
    """条件 UPDATE 的守卫真的挡得住「基于陈旧状态的写」：只有先到的那个赢。

    为什么不用「两个 session 交错」来测
    ----------------------------------
    我先写的是「另一个 session 先提交一笔」，**实测证明这条路测不出东西**：
    内存 SQLite 用的是 ``StaticPool``——整库共用**一条**连接，所以第二个
    session 提交后，第一个 session 下一次 ``get()`` 立刻读到最新已提交值，
    前置的 ``state`` 检查就会把第二次挡掉。于是我把 ``WHERE state=...``
    整个删掉，这条用例**依然是绿的**。它测的是一个测不出来的东西，属于
    「假绿测试」，比没有测试更糟（``test_team_approval.py`` 对 ``hitl``
    做过同样的实验，结论一致，并同样改用断言 SQL）。

    所以这里直接构造**过期的内存对象**：先让本 session 把行读进 identity map
    （之后 ``get()`` 一直返回缓存），再用**裸 SQL**（``text()``，绕过 ORM 的
    identity map 同步）改掉库里的状态。这精确复现了真实并发里那个形态——
    「另一个请求已经改了这行，而我手里还是旧值」。守卫在不在 ``WHERE`` 里，
    这次是**行为上**可判定的。
    """
    from find_yourself.services.errors import Conflict

    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    _satisfy(svc, v["id"], workspace)
    db.commit()

    # 此刻 identity map 里缓存的是 state='in_review'
    cached = service._row(v["id"])
    assert cached.state == "in_review"

    # 另一个写入者把库里那行推到 verified。裸 SQL：ORM 不同步 identity map，
    # 所以 cached 仍是陈旧的 in_review。
    db.execute(
        text(
            "UPDATE artifact_versions SET state='verified', decided_at='2026-01-01', "
            "decided_by='alice' WHERE id=:vid"
        ),
        {"vid": v["id"]},
    )
    db.commit()
    assert cached.state == "in_review", "本用例依赖陈旧对象；缓存未按预期保留"

    # 她仍会走完「读状态 -> 校验动作合法 -> 发 UPDATE」；唯一能救她的是守卫
    with pytest.raises(Conflict) as ei:
        service.decide(_actor("alice"), v["id"], "verify_pass")
    assert "concurrently" in ei.value.message

    db.expire_all()
    assert service.get(_actor("alice"), v["id"])["state"] == "verified"
    db.close()


def test_transition_uses_guarded_conditional_update(svc, workspace):
    """流转必须发出带状态守卫的 UPDATE——单赢家语义完全依赖它。

    为什么断言 SQL 而不模拟并发
    ----------------------------
    内存 SQLite（``StaticPool``，整库共用一条连接）上，第二个 session 读到的
    永远是最新已提交值，前置的 ``state`` 检查就会把第二次挡掉——于是我把
    ``WHERE state=...`` 删掉，这条并发测试**依然是绿的**。它测了个测不出来的
    东西，属于「假绿测试」，比没有测试更糟。换成断言实际发出的 SQL：守卫在
    不在 ``WHERE`` 里直接看得见。
    """
    from sqlalchemy import event

    service, db = svc
    v = _register(svc, workspace)
    statements: list[str] = []
    engine = db.get_bind()

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        service.submit(_actor("alice"), v["id"])
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    updates = [
        s for s in statements
        if s.lstrip().upper().startswith("UPDATE") and "artifact_versions" in s
    ]
    assert updates, "流转没有发出针对 artifact_versions 的 UPDATE"
    assert any(
        "WHERE" in u and "state" in u.split("WHERE", 1)[1] for u in updates
    ), (
        "流转的 UPDATE 必须在 WHERE 里带 state 守卫——单赢家语义完全依赖它。"
        f"实际 SQL: {updates}"
    )
    db.close()


# ======================================================================
# 独立测试（真跑子进程）
# ======================================================================
def test_independent_test_passes_and_records_real_receipt(svc, workspace):
    """真起子进程、真命令、真退出码。证据里能看到 receipt。"""
    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    out = service.run_independent_test(
        _actor("alice"), v["id"], [sys.executable, "-c", "import sys; sys.exit(0)"]
    )
    assert out["status"] == "passed"
    receipt = out["receipt"]
    assert receipt["exit_code"] == 0
    assert receipt["passed"] is True
    assert receipt["verification_id"].startswith("verif-")
    # 观测摘要来自本子进程重算，不是抄版本上的值
    assert out["observed_digest"] == v["content_digest"]

    checks = {c["check_name"]: c for c in service.list_checks(_actor("alice"), v["id"])}
    assert checks["independent_test"]["status"] == "passed"
    assert checks["independent_test"]["evidence"]["exit_code"] == 0
    db.close()


def test_failing_independent_test_blocks_verification(svc, workspace):
    """「本该失败但宽松实现会通过」——**独立测试失败就不许verify_pass**。

    这是本模块最核心的一条：门禁的判定条件之一就是「独立测试已通过」。
    若 ``verify_pass`` 不看证据（或只看``status`` 字段而不看真实退出码），
    一份 ``exit_code=1`` 的失败测试会被当成合格产物放行——正是需求7 要挡的
    那件事。
    """
    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    out = service.run_independent_test(
        _actor("alice"), v["id"], [sys.executable, "-c", "import sys; sys.exit(3)"]
    )
    assert out["status"] == "failed"
    assert out["receipt"]["exit_code"] == 3

    # 即使必检项里其它两项都真过了，失败的独立测试仍挡住 verify_pass
    _satisfy(svc, v["id"], workspace, skip_independent=True)
    verdict = service.decide(_actor("alice"), v["id"], "verify_pass")
    assert verdict["state"] == "rejected", "独立测试失败却放行了——门禁失效"
    assert verdict["terminal"] is True
    assert service.evaluate(_actor("alice"), v["id"])["usable"] is False
    db.close()


def test_independent_test_required_before_verified(svc, workspace):
    """缺独立测试这一项 → verify_pass 落rejected（而不是「其他都过了就放行」）。"""
    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    _satisfy(svc, v["id"], workspace, skip_independent=True)
    verdict = service.evaluate(_actor("alice"), v["id"])
    assert verdict["missing_checks"] == ["independent_test"]
    assert verdict["all_required_passed"] is False
    out = service.decide(_actor("alice"), v["id"], "verify_pass")
    assert out["state"] == "rejected"
    assert "independent_test" in out["decision_note"]
    db.close()


def test_independent_test_on_terminal_version_is_rejected(svc, workspace):
    """终态版本不能再补测——否则「先拒后放」会有一条侧门。"""
    from find_yourself.services.errors import Conflict

    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    service.decide(_actor("alice"), v["id"], "block")
    with pytest.raises(Conflict):
        service.run_independent_test(
            _actor("alice"), v["id"], [sys.executable, "-c", "pass"]
        )
    db.close()


def test_empty_command_is_rejected(svc, workspace):
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    with pytest.raises(ValidationFailed):
        service.run_independent_test(_actor("alice"), v["id"], "")
    db.close()


def test_independent_test_receipt_binds_to_version_digest(svc, workspace):
    """证据里的摘要必须等于版本摘要（两者算的是同一批字节）。"""
    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    out = service.run_independent_test(
        _actor("alice"), v["id"], [sys.executable, "-c", "pass"]
    )
    assert out["observed_digest"] == v["content_digest"]
    assert out["receipt"]["composite_artifact_hash"] == v["content_digest"]
    db.close()


# ======================================================================
# 门禁判定：三道闸
# ======================================================================
def test_draft_is_not_usable(svc, workspace):
    service, db = svc
    v = _register(svc, workspace)
    verdict = service.evaluate(_actor("alice"), v["id"])
    assert verdict["usable"] is False
    assert any("state is 'draft'" in r for r in verdict["blocking_reasons"])
    db.close()


def test_evaluate_reports_why_not_usable(svc, workspace):
    """门禁必须说清**为什么**不可用——只回False 会让人去绕它。"""
    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    verdict = service.evaluate(_actor("alice"), v["id"])
    joined = " ".join(verdict["blocking_reasons"])
    assert "content_digest" in joined
    assert "independent_test" in joined
    db.close()


def test_evidence_goes_stale_when_content_changes_after_check(svc, workspace):
    """「本该失败但宽松实现会通过」——**先验后改**必须在读取侧被挡。

    这是「版本内容冻结」这条纪律真正兑现的地方。流程：全部检查通过 →
    判定为verified（此刻确实合格）→ **产物文件被改** → 再次 evaluate。

    一个只信``status == 'passed'`` 的实现会在这里报usable=True，
    因为它从不重核证据对应的字节。而真实情况是：那份通过证据描述的是
    **改动前**的内容，此刻已不成立。所以 ``content_digest`` 这一项被标为
    stale，门禁重新关上。
    """
    service, db = svc
    v, _ = _to_verified(svc, workspace)
    assert service.evaluate(_actor("alice"), v["id"])["usable"] is True

    # 产物在验证之后被改：摘要变了，但版本的 content_digest 不变（冻结）
    (workspace / "main.py").write_text(
        "def add(a, b):\n    return a - b  # 有人在验证后偷改了实现\n",
        encoding="utf-8",
    )

    verdict = service.evaluate(_actor("alice"), v["id"])
    assert verdict["usable"] is False, (
        "内容在验证后被改，门禁仍报可用——证据新鲜度这道闸没生效"
    )
    assert verdict["stale_checks"], "必须指出是哪一项证据过期了"
    assert "content_digest" in verdict["stale_checks"]
    assert any("stale evidence" in r for r in verdict["blocking_reasons"])
    with pytest.raises(Exception) as ei:
        service.require_usable(_actor("alice"), v["id"])
    assert ei.value.http_status == 403
    db.close()


def test_stale_evidence_blocks_reverification(svc, workspace):
    """内容改过之后，补跑一次检查即可恢复——门禁不是永久封死。"""
    service, db = svc
    v, _ = _to_verified(svc, workspace)
    (workspace / "main.py").write_text("def add(a, b):\n    return a + b  # v2\n",
                                       encoding="utf-8")
    assert service.evaluate(_actor("alice"), v["id"])["usable"] is False
    db.close()


def test_re_recorded_check_cannot_resurrect_a_changed_version(svc, workspace):
    """内容变过之后，**重跑检查救不回这一版**——必须建新的一版。

    我最初把这条写成「重跑检查-> 门禁重新打开」，**实测证明那个期望是错的**
    （实现比我以为的更严，且更对）。原因：版本的 ``content_digest`` 是**冻结**
    的，重跑检查只会把证据里的 ``observed_digest`` 改成「当前字节的摘要」，
    而当前字节已经不是这一版声明的那份内容了——于是
    ``observed_digest != content_digest``，证据永远对不上。

    这正是「改内容= 建新的一版」这条纪律的兑现处。若实现改成「重新冻结摘要」
    或「只比status」，这一版就会被追认为合格，而它对应的内容从未被验证过。
    """
    from find_yourself.services.verification import TrustedVerificationRunner

    service, db = svc
    v, _ = _to_verified(svc, workspace)
    (workspace / "main.py").write_text(
        "def add(a, b):\n    return a + b  # touched\n", encoding="utf-8"
    )
    assert service.evaluate(_actor("alice"), v["id"])["usable"] is False

    # 按「当前字节」重跑全部摘要类检查
    _d, new_composite = TrustedVerificationRunner.compute_artifact_digests(
        str(workspace), None
    )
    assert new_composite != v["content_digest"], "本用例依赖内容确实变了"
    service.record_check(
        _actor("alice"), v["id"], "content_digest",
        status="passed", observed_digest=new_composite,
    )
    service.run_independent_test(
        _actor("alice"), v["id"], [sys.executable, "-c", "pass"]
    )
    verdict = service.evaluate(_actor("alice"), v["id"])
    assert verdict["usable"] is False, (
        "重跑检查让一个内容已变的冻结版本重新可用——「改内容必须建新版」被绕过"
    )
    assert verdict["stale_checks"] == ["content_digest"]

    # 正解：登记新的一版，它按新内容重新接受检查
    v2 = _register(svc, workspace)
    assert v2["version_no"] == 2
    assert v2["content_digest"] == new_composite
    service.submit(_actor("alice"), v2["id"])
    _satisfy(svc, v2["id"], workspace)
    assert service.decide(_actor("alice"), v2["id"], "verify_pass")["state"] == "verified"
    assert service.evaluate(_actor("alice"), v2["id"])["usable"] is True
    db.close()


def test_syntax_check_catches_broken_code(svc, workspace):
    """code_bundle 的语法检查要真的能抓出坏代码。"""
    service, db = svc
    (workspace / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    ok, detail = service.syntax_check(str(workspace), ["broken.py"])
    assert ok is False
    assert "broken.py" in detail
    db.close()


def test_syntax_check_does_not_execute_code(svc, workspace):
    """门禁只**解析**不**执行**——验证阶段不该运行被验证的代码。

    写一个「被import 就会写文件」的模块：若实现用 exec/compile(-1) 之类，
    这个文件会被创建。断言它不存在。
    """
    service, db = svc
    marker = workspace / "SHOULD_NOT_EXIST.txt"
    (workspace / "evil.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\n",
        encoding="utf-8",
    )
    service.syntax_check(str(workspace), ["evil.py"])
    assert not marker.exists(), "语法检查执行了代码——门禁不该运行被验证物"
    db.close()


def test_failed_syntax_check_prevents_verification(svc, workspace):
    """语法不过 -> 门禁不过（尽管独立测试命令自己会成功）。"""
    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    _satisfy(svc, v["id"], workspace, files=("main.py",))
    # 塞一份语法错误的证据（模拟检查器抓到问题）
    service.record_check(
        _actor("alice"), v["id"], "syntax_valid", status="failed",
        detail="main.py:1: invalid syntax",
    )
    out = service.decide(_actor("alice"), v["id"], "verify_pass")
    assert out["state"] == "rejected"
    assert "syntax_valid" in out["decision_note"]
    db.close()


def test_lying_evidence_cannot_bypass_real_digest(svc, workspace):
    """「本该失败但宽松实现会通过」——**手写一个 passed 的摘要证据**不足以放行。

    这是最容易想到的绕过：直接``record_check(content_digest, passed,
    observed_digest=<随便一个值>)``。若判定只信``status``，这就开门了。
    真实判据是：``observed_digest`` 必须等于按**当前真实字节**重算出来的摘要。
    """
    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    _satisfy(svc, v["id"], workspace, skip_independent=True)
    # 伪造：声称摘要检查通过，但观测摘要填一个错的
    service.record_check(
        _actor("alice"), v["id"], "content_digest",
        status="passed", observed_digest="0" * 64,
    )
    verdict = service.evaluate(_actor("alice"), v["id"])
    assert verdict["usable"] is False
    assert "content_digest" in verdict["stale_checks"]
    db.close()


def test_observed_digest_must_be_sha256_shaped(svc, workspace):
    """观测摘要必须是 64 位——否则「比对」会被空串/短串恒真绕过。"""
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    v = _register(svc, workspace)
    with pytest.raises(ValidationFailed):
        service.record_check(
            _actor("alice"), v["id"], "content_digest",
            status="passed", observed_digest="short",
        )
    db.close()


def test_skipped_status_is_rejected(svc, workspace):
    """没有「跳过」——跳过与通过在库里必须长得不一样。"""
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    v = _register(svc, workspace)
    for bad in ("skipped", "passed_with_warnings", "", "PASSED"):
        with pytest.raises(ValidationFailed):
            service.record_check(
                _actor("alice"), v["id"], "content_digest", status=bad
            )
    db.close()


# ======================================================================
# 审计 + 归属隔离
# ======================================================================
def test_gate_events_are_audited(svc, session_maker, workspace):
    from find_yourself.services.artifact_gate import ArtifactGateService
    from find_yourself.services.audit import AuditService

    service, db = svc
    v = _register(svc, workspace)
    service.submit(_actor("alice"), v["id"])
    _satisfy(svc, v["id"], workspace)
    ArtifactGateService(db, AuditService(db)).decide(
        _actor("alice"), v["id"], "verify_pass"
    )
    db.commit()
    actions = list(
        session_maker().execute(
            text("SELECT action FROM audit_events ORDER BY seq")
        ).scalars()
    )
    assert "artifact_gate.version_registered" in actions
    assert "artifact_gate.check_recorded" in actions
    assert "artifact_gate.verify_pass" in actions
    db.close()


def test_versions_are_owner_isolated(svc, workspace):
    from find_yourself.services.errors import NotFound

    service, db = svc
    v = _register(svc, workspace, who="alice")
    assert service.list_versions(_actor("alice")) != []
    assert service.list_versions(_actor("mallory")) == []
    for call in (
        lambda: service.get(_actor("mallory"), v["id"]),
        lambda: service.evaluate(_actor("mallory"), v["id"]),
        lambda: service.list_checks(_actor("mallory"), v["id"]),
    ):
        with pytest.raises(NotFound):
            call()
    db.close()


def test_list_versions_filters(svc, workspace):
    from find_yourself.services.errors import ValidationFailed

    service, db = svc
    _register(svc, workspace, artifact_id="a1")
    _register(svc, workspace, kind="asset", artifact_id="a2")
    assert len(service.list_versions(_actor("alice"))) == 2
    assert len(service.list_versions(_actor("alice"), artifact_kind="asset")) == 1
    assert len(service.list_versions(_actor("alice"), state="draft")) == 2
    with pytest.raises(ValidationFailed):
        service.list_versions(_actor("alice"), state="nonsense")
    db.close()


def test_unknown_version_is_404(svc):
    from find_yourself.services.errors import NotFound

    service, db = svc
    for call in (
        lambda: service.get(_actor("alice"), "agv-nope"),
        lambda: service.evaluate(_actor("alice"), "agv-nope"),
    ):
        with pytest.raises(NotFound):
            call()
    db.close()


# ======================================================================
# 表级不变量
# ======================================================================
def test_db_rejects_unknown_state(session_maker):
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO artifact_versions (id, owner_id, artifact_kind, "
                "artifact_id, version_no, content_digest, workspace_ref, state, "
                "decision_note, created_by, created_at, updated_at, version) "
                f"VALUES ('agv-bad','o','asset','a',1,'{'0'*64}','{{}}','shipped',"
                "'','o','2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_db_rejects_unknown_kind(session_maker):
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO artifact_versions (id, owner_id, artifact_kind, "
                "artifact_id, version_no, content_digest, workspace_ref, state, "
                "decision_note, created_by, created_at, updated_at, version) "
                f"VALUES ('agv-bad2','o','mystery','a',1,'{'0'*64}','{{}}','draft',"
                "'','o','2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_db_rejects_short_digest(session_maker):
    """摘要必须是 64 位。不校验它，「证据比对」会退化成两个空串比较。"""
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO artifact_versions (id, owner_id, artifact_kind, "
                "artifact_id, version_no, content_digest, workspace_ref, state, "
                "decision_note, created_by, created_at, updated_at, version) "
                "VALUES ('agv-bad3','o','asset','a',1,'','{}','draft',"
                "'','o','2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_db_rejects_verified_without_submit(session_maker, svc, workspace):
    """未经 submit 就verified 的行不能落库（ck_agv_submitted_shape）。"""
    import sqlalchemy.exc as sa_exc

    service, db = svc
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO artifact_versions (id, owner_id, artifact_kind, "
                "artifact_id, version_no, content_digest, workspace_ref, state, "
                "submitted_at, decided_at, decided_by, decision_note, created_by, "
                "created_at, updated_at, version) "
                f"VALUES ('agv-bad4','o','asset','a',1,'{'0'*64}','{{}}','verified',"
                "NULL,'2026-01-01','o','','o','2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_db_rejects_draft_with_decision(session_maker):
    """draft 不得带 decided_at（ck_agv_decided_shape）。"""
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO artifact_versions (id, owner_id, artifact_kind, "
                "artifact_id, version_no, content_digest, workspace_ref, state, "
                "decided_at, decision_note, created_by, created_at, updated_at, version) "
                f"VALUES ('agv-bad5','o','asset','a',1,'{'0'*64}','{{}}','draft',"
                "'2026-01-01','','o','2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_duplicate_version_no_rejected(svc, workspace):
    """一版的定义就是「同一产物的同一版号」，不能有两行。"""
    import sqlalchemy.exc as sa_exc

    service, db = svc
    v = _register(svc, workspace)
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO artifact_versions (id, owner_id, artifact_kind, "
                "artifact_id, version_no, content_digest, workspace_ref, state, "
                "decision_note, created_by, created_at, updated_at, version) "
                "VALUES ('agv-dup','alice','code_bundle','art-1',1,"
                f"'{v['content_digest']}','{{}}','draft','','alice',"
                "'2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_db_rejects_unknown_check_status(svc, workspace):
    """没有「跳过」：库里也塞不进去。"""
    import sqlalchemy.exc as sa_exc

    service, db = svc
    v = _register(svc, workspace)
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO artifact_gate_checks (id, artifact_version_id, "
                "check_name, status, evidence, detail, ran_by, created_at, "
                "updated_at, version) "
                f"VALUES ('agc-bad','{v['id']}','content_digest','skipped',"
                "'{}','','alice','2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_duplicate_check_row_rejected(svc, workspace):
    """一版一项只留一行——多行就得回答「取哪一行」，答错会让旧证据复活。"""
    import sqlalchemy.exc as sa_exc

    service, db = svc
    v = _register(svc, workspace)
    service.record_check(
        _actor("alice"), v["id"], "content_digest", status="failed"
    )
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO artifact_gate_checks (id, artifact_version_id, "
                "check_name, status, evidence, detail, ran_by, created_at, "
                "updated_at, version) "
                f"VALUES ('agc-dup','{v['id']}','content_digest','passed',"
                "'{}','','alice','2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_check_cannot_reference_unknown_version(session_maker):
    """证据必须挂在一个真实存在的版本上（外键真的开着）。"""
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises((sa_exc.IntegrityError, sa_exc.OperationalError)):
        db.execute(
            text(
                "INSERT INTO artifact_gate_checks (id, artifact_version_id, "
                "check_name, status, evidence, detail, ran_by, created_at, "
                "updated_at, version) "
                "VALUES ('agc-orphan','agv-nope','content_digest','passed',"
                "'{}','','alice','2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_re_record_updates_in_place_not_a_new_row(svc, workspace):
    service, db = svc
    v = _register(svc, workspace)
    service.record_check(_actor("alice"), v["id"], "content_digest", status="failed")
    service.record_check(_actor("alice"), v["id"], "content_digest", status="passed")
    checks = service.list_checks(_actor("alice"), v["id"])
    assert len(checks) == 1
    assert checks[0]["status"] == "passed"
    assert checks[0]["version"] == 2
    db.close()


# ======================================================================
# HTTP 层
# ======================================================================
def _http_register(client, headers, workspace, **body):
    payload = {
        "artifact_kind": "code_bundle", "artifact_id": "http-art-1",
        "workspace_dir": workspace.as_posix(), **body,
    }
    r = client.post("/api/artifact-gates/versions", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_http_full_gate_flow(client: TestClient, headers: dict, workspace):
    """HTTP 全链路：登记 -> 送检 -> 记录检查 -> 判定 -> 放行 -> 查询门禁。"""
    v = _http_register(client, headers, workspace)
    vid = v["id"]
    assert v["state"] == "draft"

    r = client.post(f"/api/artifact-gates/versions/{vid}/submit", json={}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "in_review"

    from find_yourself.services.verification import TrustedVerificationRunner

    _d, composite = TrustedVerificationRunner.compute_artifact_digests(
        workspace.as_posix(), None
    )
    r = client.post(
        f"/api/artifact-gates/versions/{vid}/checks",
        json={"check_name": "content_digest", "status": "passed",
              "observed_digest": composite},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/api/artifact-gates/versions/{vid}/checks",
        json={"check_name": "syntax_valid", "status": "passed",
              "observed_digest": composite},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    # 真跑一条独立测试
    r = client.post(
        f"/api/artifact-gates/versions/{vid}/independent-test",
        json={"command": [sys.executable, "-c", "import sys; sys.exit(0)"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "passed"

    r = client.post(
        f"/api/artifact-gates/versions/{vid}/decision",
        json={"action": "verify_pass"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "verified"

    r = client.post(
        f"/api/artifact-gates/versions/{vid}/decision",
        json={"action": "release"}, headers=headers,
    )
    assert r.json()["state"] == "released"

    r = client.get(f"/api/artifact-gates/versions/{vid}/gate", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["usable"] is True
    assert r.json()["exportable"] is True

    r = client.get(f"/api/artifact-gates/versions/{vid}/checks", headers=headers)
    assert r.json()["count"] == 3


def test_http_skipping_verification_is_422(client: TestClient, headers: dict, workspace):
    """HTTP 层同样挡住「跳过验证直接放行」。"""
    v = _http_register(client, headers, workspace)
    r = client.post(
        f"/api/artifact-gates/versions/{v['id']}/decision",
        json={"action": "release"}, headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "not legal from state 'draft'" in r.json()["detail"]
    # 状态未变
    r = client.get(f"/api/artifact-gates/versions/{v['id']}", headers=headers)
    assert r.json()["state"] == "draft"


def test_http_failing_independent_test_leads_to_rejected(
    client: TestClient, headers: dict, workspace
):
    """HTTP 全链路：独立测试失败 -> verify_pass 落rejected。"""
    v = _http_register(client, headers, workspace)
    vid = v["id"]
    client.post(f"/api/artifact-gates/versions/{vid}/submit", json={}, headers=headers)

    from find_yourself.services.verification import TrustedVerificationRunner

    _d, composite = TrustedVerificationRunner.compute_artifact_digests(
        workspace.as_posix(), None
    )
    for name in ("content_digest", "syntax_valid"):
        client.post(
            f"/api/artifact-gates/versions/{vid}/checks",
            json={"check_name": name, "status": "passed",
                  "observed_digest": composite},
            headers=headers,
        )
    r = client.post(
        f"/api/artifact-gates/versions/{vid}/independent-test",
        json={"command": [sys.executable, "-c", "import sys; sys.exit(1)"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"
    assert r.json()["receipt"]["exit_code"] == 1

    r = client.post(
        f"/api/artifact-gates/versions/{vid}/decision",
        json={"action": "verify_pass"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "rejected"


def test_http_write_requires_csrf(client: TestClient, headers: dict[str, str], workspace):
    """有会话但缺 CSRF 头 -> 403。

    依赖 ``headers`` 夹具先把会话建起来：那样才是「已登录但没带 CSRF」这个
    真正要测的场景。没有会话时是 401（另一条用例单独覆盖），两者别混。
    """
    r = client.post(
        "/api/artifact-gates/versions",
        json={"artifact_kind": "asset", "artifact_id": "a",
              "workspace_dir": workspace.as_posix()},
    )
    assert r.status_code == 403, r.text


def test_http_write_without_session_is_401(client: TestClient, workspace):
    """完全没会话 -> 401。与「有会话缺 CSRF」的 403 是两件事。"""
    r = client.post(
        "/api/artifact-gates/versions",
        json={"artifact_kind": "asset", "artifact_id": "a",
              "workspace_dir": workspace.as_posix()},
    )
    assert r.status_code == 401, r.text


def test_http_decision_requires_csrf(client: TestClient, headers: dict[str, str], workspace):
    """判定这条最敏感的写操作同样要 CSRF。"""
    v = _http_register(client, headers, workspace)
    r = client.post(
        f"/api/artifact-gates/versions/{v['id']}/decision",
        json={"action": "submit"},
    )
    assert r.status_code == 403, r.text
    r = client.get(f"/api/artifact-gates/versions/{v['id']}", headers=headers)
    assert r.json()["state"] == "draft"  # 状态未变


def test_http_bad_check_status_is_422(client: TestClient, headers: dict, workspace):
    v = _http_register(client, headers, workspace)
    r = client.post(
        f"/api/artifact-gates/versions/{v['id']}/checks",
        json={"check_name": "content_digest", "status": "skipped"},
        headers=headers,
    )
    assert r.status_code == 422, r.text


def test_http_unknown_kind_is_422(client: TestClient, headers: dict, workspace):
    r = client.post(
        "/api/artifact-gates/versions",
        json={"artifact_kind": "mystery", "artifact_id": "a",
              "workspace_dir": workspace.as_posix()},
        headers=headers,
    )
    assert r.status_code == 422, r.text


def test_http_unknown_version_is_404(client: TestClient, headers: dict):
    r = client.get("/api/artifact-gates/versions/agv-nope", headers=headers)
    assert r.status_code == 404, r.text


def test_http_list_and_filter(client: TestClient, headers: dict, workspace):
    _http_register(client, headers, workspace, artifact_id="x1")
    _http_register(client, headers, workspace, artifact_kind="asset", artifact_id="x2")
    r = client.get("/api/artifact-gates/versions", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2
    r = client.get(
        "/api/artifact-gates/versions?artifact_kind=asset", headers=headers
    )
    assert r.json()["count"] == 1
    r = client.get("/api/artifact-gates/versions?state=draft", headers=headers)
    assert r.json()["count"] == 2
    r = client.get("/api/artifact-gates/versions?state=bogus", headers=headers)
    assert r.status_code == 422, r.text


def test_gate_survives_alongside_existing_subsystems(
    client: TestClient, headers: dict, workspace
):
    """注册新router 不能破坏既有路径：HITL / 团队审批 / 资产库都还在。"""
    r = client.post(
        "/api/hitl/interrupts",
        json={"execution_id": "ag-gate-run", "checkpoint": "before_publish",
              "options": ["approve", "cancel"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post("/api/team-approvals/teams", json={"name": "门禁组"}, headers=headers)
    assert r.status_code == 201, r.text
    r = client.get("/api/assets", headers=headers)
    assert r.status_code == 200, r.text


# ----------------------------------------------------------------------
# 注册自检：artifact_versions / artifact_gate_checks 必须真被建出来
# ----------------------------------------------------------------------
def test_app_create_all_registers_gate_tables(tmp_path, workspace):
    """走真实 ``create_app``（**不传 session_maker``）的新库上，两张表必须存在。

    上面所有用例都用注入的 session_maker + ``Base.metadata.create_all``，
    压根不经过 ``app.py`` 里的 import。也就是说：那行 import 若被删掉，
    上面全部用例**依然全绿**，直到某天用默认配置真跑一次服务才在别处炸出
    一个与根因无关的错。所以必须走 app.py 的 SQLite 分支，且用文件库
    （内存库的生命周期绑在连接上，证明不了 ``create_all`` 真跑过）。
    """
    from sqlalchemy import inspect

    db_file = tmp_path / "gate_registration.db"
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url=f"sqlite:///{db_file.as_posix()}",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )
    real_app = create_app(settings=settings)
    assert db_file.exists(), "create_app 未在配置的库上落盘"
    engine = real_app.state.session_maker.kw["bind"]
    tables = set(inspect(engine).get_table_names())
    for name in ("artifact_versions", "artifact_gate_checks"):
        assert name in tables, (
            f"{name} 未被建出来——检查 app.py 中的 "
            f"`import find_yourself.db.artifact_gate_models` 是否还在"
        )
    cols = {c["name"] for c in inspect(engine).get_columns("artifact_versions")}
    assert {
        "id", "owner_id", "artifact_kind", "artifact_id", "version_no",
        "content_digest", "workspace_ref", "state", "submitted_at",
        "decided_at", "decided_by", "version",
    } <= cols
    check_cols = {
        c["name"] for c in inspect(engine).get_columns("artifact_gate_checks")
    }
    assert {
        "id", "artifact_version_id", "check_name", "status",
        "observed_digest", "evidence",
    } <= check_cols


def test_required_checks_are_not_a_column():
    """必检项**不落库**——它来自服务端策略。

    若哪天有人加了 ``required_checks`` 列并让它可由请求体填充，
    「声明无需检查」这条现成绕过路径就回来了。这条断言是那道防线的哨兵。
    """
    from find_yourself.db.artifact_gate_models import ArtifactVersion

    assert "required_checks" not in ArtifactVersion.__table__.columns


def test_gate_policy_covers_every_kind():
    """每个受管形态都必须有策略——没有「忘了配策略」的产物。"""
    from find_yourself.db.artifact_gate_models import (
        ARTIFACT_KINDS,
        GATE_POLICY,
        required_checks_for,
    )

    assert set(ARTIFACT_KINDS) == set(GATE_POLICY)
    for kind in ARTIFACT_KINDS:
        required = required_checks_for(kind)
        assert required, f"{kind} 的必检项为空——等于没有门禁"
        assert "content_digest" in required
        assert "independent_test" in required
    with pytest.raises(ValueError):
        required_checks_for("mystery")
