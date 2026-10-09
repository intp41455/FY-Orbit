"""Unit tests: 需求 12 Human-in-the-loop 执行中断与恢复。

覆盖真实 HTTP + 真实 SQLite（内存库 + ``create_all``，沿用项目既有风格）：

- 暂停：中断落库、状态为 ``pending``、可按执行查回、待决列表可见；
- 决策：approved / rejected / cancelled 三种终态各自落对；
- 非法决策被拒：不在options 里的decision、空 options、缺字段；
- 重复决策被拒：同一中断第二次 decide 报 already_decided；
- 归属隔离：别人的中断按「不存在」处理，不泄露存在性；
- 超时：过期后 pending 变 None、决策被拒；
- 表级不变量：pending 行带decision /已决策行不带 decision 都被 CHECK 拒绝；
- 并发：条件 UPDATE 只让第一个决策赢。

不 mock DB —— 中断的整个价值就在「状态真的落住、真的转得动」，
mock 掉 DB 的测试只能证明 mock 是这么配的。
"""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.hitl_models  # noqa: F401
import find_yourself.db.models  # noqa: F401
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.types import TZDateTime, utcnow

LOCAL_TOKEN = "dev-token-secret-hitl"


# --- Test-only SQLite TZ shim (same as tests/unit/test_preview_sources.py) -
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


def _pause(client: TestClient, headers: dict[str, str], **over):
    body = {
        "execution_id": "task-run-7",
        "checkpoint": "before_publish",
        "context": {"intent": "发布到公开频道", "risk": "high"},
        "options": [
            {"value": "approve", "label": "同意发布"},
            {"value": "cancel", "label": "取消"},
        ],
        "reason": "公开发布不可逆，需要人拍板",
    }
    body.update(over)
    return client.post("/api/hitl/interrupts", json=body, headers=headers)


def _decide(client: TestClient, headers: dict[str, str], interrupt_id: str, **over):
    body = {"decision": "approve"}
    body.update(over)
    return client.post(
        f"/api/hitl/interrupts/{interrupt_id}/decision", json=body, headers=headers
    )


# ----------------------------------------------------------------------
# 暂停
# ----------------------------------------------------------------------
def test_pause_creates_pending_interrupt(client: TestClient, headers: dict[str, str]):
    r = _pause(client, headers)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["status"] == "pending"
    assert row["pending"] is True
    assert row["decided"] is False
    assert row["decision"] is None
    assert row["execution_id"] == "task-run-7"
    assert row["checkpoint"] == "before_publish"
    assert row["id"]
    assert row["version"] == 1
    assert row["created_at"]


def test_pause_normalises_string_options(client: TestClient, headers: dict[str, str]):
    r = _pause(client, headers, options=["approve", "reject"])
    assert r.status_code == 201, r.text
    assert r.json()["options"] == [
        {"value": "approve", "label": "approve"},
        {"value": "reject", "label": "reject"},
    ]


def test_pause_requires_at_least_one_option(client: TestClient, headers: dict[str, str]):
    """没有可选项的中断不是提问，是 bug——必须被拒。"""
    r = _pause(client, headers, options=[])
    assert r.status_code == 422, r.text


def test_pause_rejects_empty_option_value(client: TestClient, headers: dict[str, str]):
    r = _pause(client, headers, options=[{"value": "  ", "label": "空"}])
    assert r.status_code == 422, r.text


def test_pause_rejects_missing_checkpoint(client: TestClient, headers: dict[str, str]):
    r = _pause(client, headers, checkpoint="")
    assert r.status_code == 422, r.text


def test_execution_reports_pending(client: TestClient, headers: dict[str, str]):
    assert _pause(client, headers).status_code == 201
    r = client.get("/api/hitl/executions/task-run-7/interrupt", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending"] is True
    assert body["interrupt"]["checkpoint"] == "before_publish"


def test_execution_not_pending_when_never_paused(client: TestClient, headers: dict[str, str]):
    r = client.get("/api/hitl/executions/never-ran/interrupt", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"execution_id": "never-ran", "pending": False, "interrupt": None}


def test_list_pending_shows_interrupt(client: TestClient, headers: dict[str, str]):
    _pause(client, headers)
    r = client.get("/api/hitl/interrupts", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["status"] == "pending"


def test_second_pause_on_same_execution_conflicts(client: TestClient, headers: dict[str, str]):
    """一个执行同时只能卡在一处；两个并行等待路径不是 HITL 的形状。"""
    assert _pause(client, headers).status_code == 201
    again = _pause(client, headers, checkpoint="before_delete")
    assert again.status_code == 409, again.text


def test_get_interrupt_by_id(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    r = client.get(f"/api/hitl/interrupts/{interrupt_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == interrupt_id


def test_get_unknown_interrupt_is_404(client: TestClient, headers: dict[str, str]):
    r = client.get("/api/hitl/interrupts/hitl-does-not-exist", headers=headers)
    assert r.status_code == 404, r.text


# ----------------------------------------------------------------------
# 决策：三个终态
# ----------------------------------------------------------------------
def test_decide_approve_sets_approved(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    r = _decide(client, headers, interrupt_id, decision="approve")
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["status"] == "approved"
    assert row["pending"] is False
    assert row["decided"] is True
    assert row["decision"] == "approve"
    assert row["decided_at"]
    assert row["version"] == 2


def test_decide_cancel_sets_cancelled(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    r = _decide(client, headers, interrupt_id, decision="cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"


def test_decide_other_option_defaults_to_rejected(client: TestClient, headers: dict[str, str]):
    """语义未知的选项按保守默认记rejected，而不是猜成 approved。"""
    interrupt_id = _pause(
        client, headers, options=[{"value": "escalate", "label": "上报"}]
    ).json()["id"]
    r = _decide(client, headers, interrupt_id, decision="escalate")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"


def test_decide_carries_resolution(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    r = _decide(client, headers, interrupt_id, resolution={"edited_title": "新标题"})
    assert r.status_code == 200, r.text
    assert r.json()["resolution"] == {"edited_title": "新标题"}


def test_after_decision_execution_no_longer_pending(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    _decide(client, headers, interrupt_id)
    r = client.get("/api/hitl/executions/task-run-7/interrupt", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["pending"] is False


def test_after_decision_not_in_pending_list(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    _decide(client, headers, interrupt_id)
    r = client.get("/api/hitl/interrupts", headers=headers)
    assert r.json()["count"] == 0


# ----------------------------------------------------------------------
# 非法决策被拒
# ----------------------------------------------------------------------
def test_decision_outside_options_rejected(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    r = _decide(client, headers, interrupt_id, decision="do_whatever")
    assert r.status_code == 422, r.text
    # 被拒之后仍可正常拍板
    assert _decide(client, headers, interrupt_id, decision="approve").status_code == 200


def test_empty_decision_rejected(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    r = _decide(client, headers, interrupt_id, decision="")
    assert r.status_code == 422, r.text


def test_decision_on_unknown_interrupt_is_404(client: TestClient, headers: dict[str, str]):
    r = _decide(client, headers, "hitl-nope")
    assert r.status_code == 404, r.text


# ----------------------------------------------------------------------
# 重复决策被拒
# ----------------------------------------------------------------------
def test_second_decision_conflicts(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    assert _decide(client, headers, interrupt_id, decision="approve").status_code == 200
    again = _decide(client, headers, interrupt_id, decision="cancel")
    assert again.status_code == 409, again.text
    assert "already" in again.json()["detail"].lower()


def test_second_decision_does_not_overwrite_first(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    _decide(client, headers, interrupt_id, decision="approve", resolution={"n": 1})
    _decide(client, headers, interrupt_id, decision="cancel")
    stored = client.get(f"/api/hitl/interrupts/{interrupt_id}", headers=headers).json()
    assert stored["status"] == "approved"  #第一次的决定没被覆盖
    assert stored["decision"] == "approve"
    assert stored["resolution"] == {"n": 1}
    assert stored["version"] == 2  # 也没有被多余的版本号灌水


# ----------------------------------------------------------------------
# 归属隔离
# ----------------------------------------------------------------------
def test_other_owner_cannot_see_or_decide_interrupt(session_maker):
    """别人的中断按「不存在」处理——不确认它是否存在。"""
    from find_yourself.services.actor import Actor
    from find_yourself.services.errors import NotFound
    from find_yourself.services.hitl import HitlInterruptService

    db = session_maker()
    svc = HitlInterruptService(db)
    mine = svc.interrupt(Actor.owner("owner-one"), "task-run-7", "before_publish",
                         options=["approve", "cancel"])

    stranger = Actor.owner("owner-two")
    assert svc.list_pending(stranger) == []          # 待决列表不串户
    assert svc.pending(stranger, "task-run-7") is None
    with pytest.raises(NotFound):
        svc.get(stranger, mine["id"])
    with pytest.raises(NotFound):
        svc.decide(stranger, mine["id"], "approve")
    db.close()


def test_owner_can_decide_after_reauthenticating(session_maker):
    """同一个 owner换一条会话回来，仍能拍板（可见性按 owner 而非按会话）。"""
    from find_yourself.services.actor import Actor
    from find_yourself.services.hitl import HitlInterruptService

    db = session_maker()
    svc = HitlInterruptService(db)
    row = svc.interrupt(Actor.owner("owner-one"), "task-run-7", "before_publish",
                        options=["approve", "cancel"])
    decided = svc.decide(Actor.owner("owner-one"), row["id"], "approve")
    assert decided["status"] == "approved"
    db.close()


def test_service_identity_cannot_decide(app: FastAPI, session_maker):
    """执行体不能给自己放行——决策必须是人做的。"""
    from find_yourself.services.actor import Actor
    from find_yourself.services.errors import PermissionDenied
    from find_yourself.services.hitl import HitlInterruptService

    db = session_maker()
    svc = HitlInterruptService(db)
    worker = Actor.service("agent-x", "agent")
    row = svc.interrupt(worker, "task-run-9", "before_write",
                        options=["approve", "cancel"])
    with pytest.raises(PermissionDenied):
        svc.decide(worker, row["id"], "approve")
    db.close()


# ----------------------------------------------------------------------
# 超时
# ----------------------------------------------------------------------
def test_expired_interrupt_is_no_longer_pending(session_maker):
    from find_yourself.services.actor import Actor
    from find_yourself.services.errors import Conflict
    from find_yourself.services.hitl import HitlInterruptService

    db = session_maker()
    svc = HitlInterruptService(db)
    owner = Actor.owner("owner-1")
    row = svc.interrupt(owner, "task-run-x", "before_send",
                        options=["approve"], timeout_seconds=1)
    # 把截止时间推到过去，模拟「已超时」
    from find_yourself.db.hitl_models import HitlInterrupt

    rec = db.get(HitlInterrupt, row["id"])
    rec.expires_at = utcnow() - timedelta(seconds=5)
    db.commit()

    assert svc.pending(owner, "task-run-x") is None
    assert db.get(HitlInterrupt, row["id"]).status == "expired"
    # 超时后不允许再补拍板
    with pytest.raises(Conflict):
        svc.decide(owner, row["id"], "approve")
    db.close()


def test_not_yet_expired_still_pending(session_maker):
    from find_yourself.services.actor import Actor
    from find_yourself.services.hitl import HitlInterruptService

    db = session_maker()
    svc = HitlInterruptService(db)
    owner = Actor.owner("owner-1")
    svc.interrupt(owner, "task-run-y", "before_send",
                  options=["approve"], timeout_seconds=3600)
    assert svc.pending(owner, "task-run-y") is not None
    db.close()


# ----------------------------------------------------------------------
# 并发：只有第一个决策赢
# ----------------------------------------------------------------------
def test_concurrent_decisions_only_one_wins(session_maker):
    from find_yourself.services.actor import Actor
    from find_yourself.services.errors import Conflict
    from find_yourself.services.hitl import HitlInterruptService

    db = session_maker()
    svc = HitlInterruptService(db)
    owner = Actor.owner("owner-1")
    row = svc.interrupt(owner, "task-run-z", "before_publish",
                        options=["approve", "cancel"])
    db.commit()

    first = svc.decide(owner, row["id"], "approve")
    assert first["status"] == "approved"
    with pytest.raises(Conflict):
        svc.decide(owner, row["id"], "cancel")
    db.close()


# ----------------------------------------------------------------------
# 表级不变量：等待中 ↔ 已决策 的形状由schema 保证
# ----------------------------------------------------------------------
def test_db_rejects_pending_row_carrying_decision(session_maker):
    """半截决策落不了盘——CHECK 兜住，不依赖服务层永远记得写对。"""
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO hitl_interrupts "
                "(id, owner_id, execution_id, checkpoint, context, options, decision, "
                " status, reason, expires_at, decided_at, created_at, updated_at, version) "
                "VALUES ('bad-1','o','e','c','{}','[]','approve','pending','',"
                "NULL,NULL,'2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_db_rejects_decided_row_without_decision(session_maker):
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO hitl_interrupts "
                "(id, owner_id, execution_id, checkpoint, context, options, decision, "
                " status, reason, expires_at, decided_at, created_at, updated_at, version) "
                "VALUES ('bad-2','o','e','c','{}','[]',NULL,"
                "'approved','',NULL,'2026-01-01','2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


def test_db_rejects_unknown_status(session_maker):
    import sqlalchemy.exc as sa_exc

    db = session_maker()
    with pytest.raises(sa_exc.IntegrityError):
        db.execute(
            text(
                "INSERT INTO hitl_interrupts "
                "(id, owner_id, execution_id, checkpoint, context, options, decision, "
                " status, reason, expires_at, decided_at, created_at, updated_at, version) "
                "VALUES ('bad-3','o','e','c','{}','[]',NULL,"
                "'maybe','',NULL,NULL,'2026-01-01','2026-01-01',1)"
            )
        )
    db.rollback()
    db.close()


# ----------------------------------------------------------------------
# expected_version 乐观锁：人必须对他看过的那个版本拍板
# ----------------------------------------------------------------------
def test_expected_version_blocks_stale_decision(session_maker):
    """暂停后上下文被改过，人手上那版就过期了——必须挡，不能让他按旧版拍板。"""
    from find_yourself.services.actor import Actor
    from find_yourself.services.errors import Conflict
    from find_yourself.services.hitl import HitlInterruptService

    db = session_maker()
    svc = HitlInterruptService(db)
    owner = Actor.owner("owner-1")
    row = svc.interrupt(owner, "task-run-v", "before_publish", options=["approve", "cancel"])
    assert row["version"] == 1

    # 人看过 v1，此刻上下文被改到 v2
    db.get(__import__("find_yourself.db.hitl_models", fromlist=["HitlInterrupt"]).HitlInterrupt,
           row["id"]).version = 2
    db.commit()

    with pytest.raises(Conflict) as ei:
        svc.decide(owner, row["id"], "approve", expected_version=1)
    # DomainError 的 code 与 message 是分开的：断言 code 才是稳的
    assert ei.value.code == "version_changed"

    # 用当前 version 则通过
    ok = svc.decide(owner, row["id"], "approve", expected_version=2)
    assert ok["status"] == "approved"
    db.close()


def test_expected_version_matching_current_is_accepted(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    got = _decide(client, headers, interrupt_id, expected_version=1)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "approved"


def test_expected_version_mismatch_is_409_over_http(client: TestClient, headers: dict[str, str]):
    interrupt_id = _pause(client, headers).json()["id"]
    r = _decide(client, headers, interrupt_id, expected_version=99)
    assert r.status_code == 409, r.text
    assert "version" in r.json()["detail"].lower()


# ----------------------------------------------------------------------
# 注册自检：hitl_interrupts 必须真的被建出来
# ----------------------------------------------------------------------
def test_app_create_all_registers_hitl_table(tmp_path):
    """走真实 ``create_app``（**不传 session_maker**）的新库上，表必须存在。

    上面 32 个用例都用注入的 session_maker + ``Base.metadata.create_all``，
    压根不经过 ``app.py`` 里那行 ``import find_yourself.db.hitl_models``。
    也就是说：**那行 import 若被覆盖或删掉，上面 32 个用例依然全绿**，
    直到某天有人用默认配置真跑一次服务，才在别处炸出一个与根因无关的错。

    所以这里必须走 app.py 的 SQLite 分支，并用文件库（而非 :memory:）——
    内存库的生命周期绑在连接上，证明不了 ``create_all`` 真的执行过。
    """
    from sqlalchemy import inspect

    db_file = tmp_path / "hitl_registration.db"
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url=f"sqlite:///{db_file.as_posix()}",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )
    # 关键：不传 session_maker -> 走 app.py 的 engine + create_all 分支
    real_app = create_app(settings=settings)

    assert db_file.exists(), "create_app 未在配置的库上落盘"
    engine = real_app.state.session_maker.kw["bind"]
    tables = set(inspect(engine).get_table_names())
    assert "hitl_interrupts" in tables, (
        "hitl_interrupts 未被建出来——检查 app.py 中的 "
        "`import find_yourself.db.hitl_models` 是否还在"
    )
    # 关键列一并确认，避免「表在但结构不对」的假绿
    cols = {c["name"] for c in inspect(engine).get_columns("hitl_interrupts")}
    assert {
        "id", "owner_id", "execution_id", "checkpoint", "context", "options",
        "decision", "status", "decided_at", "expires_at", "version",
    } <= cols


def test_registration_is_guaranteed_transitively_not_only_by_app_py(tmp_path):
    """反向对照：证明上面那个断言不是永真，并说清它到底靠什么成立。

    实测结论（与最初的假设不同，值得写进用例）：

    ``app.py`` 里那行 ``import find_yourself.db.hitl_models`` **并非唯一保障**。
    ``api/routes/hitl.py`` 导入了 ``deps``，而 ``deps`` 又导入
    ``services.hitl``，后者 import 了 ``db.hitl_models``——所以只要
    ``api_router`` 被装配，模型就**必然**注册进 ``Base.metadata``。

    也就是说「import 被静默覆盖 → 表不再建」这个担心的前提不成立：
    真正断链时是 ``ImportError``（响亮失败），而不是安静地少一张表。

    本用例把这条真实不变量钉住：删掉 app.py 那行，表**依然**存在。
    若哪天有人重构掉 ``routes/hitl.py`` 对 ``deps`` 的依赖，这条会红，
    那时app.py 的显式 import 就从「冗余」变成了「唯一保障」。
    """
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    # 零缩进写成：dedent 只认所有行共同的空白前缀。
    script = textwrap.dedent(
        """\
import sys
import importlib.abc
from pathlib import Path
from sqlalchemy import inspect
from find_yourself.config import Settings

# 只导入 router 链，完全不碰 app.py —— 模拟那行 import 被删掉
import find_yourself.api.routes
from find_yourself.db.base import Base

assert "hitl_interrupts" in Base.metadata.tables, (
    "router 链已不能保证注册：app.py 那行 import 是当前唯一保障"
)

# 真起一次 create_all，证明 metadata 里的表真的落库
from sqlalchemy import create_engine
eng = create_engine("sqlite:///" + (Path(sys.argv[1]) / "chain.db").as_posix(), future=True)
Base.metadata.create_all(eng)
tables = set(inspect(eng).get_table_names())
assert "memories" in tables, "create_all 根本没执行，对照实验无意义"
assert "hitl_interrupts" in tables, "已注册却没建表，metadata 与 DDL 不一致"
print("OK")
"""
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True, text=True, cwd=str(Path.cwd()),
    )
    assert proc.returncode == 0, (
        f"传导链保证不成立——app.py 的显式 import 已成为唯一保障。\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "OK" in proc.stdout


def test_missing_hitl_models_fails_loudly_not_silently():
    """断链时必须是响亮的 ImportError，不能是「安静地少一张表」。

    静默失败是这里唯一真正危险的形态：测试照绿、服务照起，直到某天
    有人真去写库才发现表不存在。所以把「响亮」也钉成一个断言。
    """
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    script = textwrap.dedent(
        """\
import sys, importlib.abc

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "find_yourself.db.hitl_models":
            raise ImportError("simulated: hitl_models unreachable")
        return None

sys.meta_path.insert(0, Blocker())
for m in list(sys.modules):
    if "hitl" in m:
        del sys.modules[m]

try:
    import find_yourself.api.routes
    print("SILENT")
except ImportError:
    print("LOUD")
"""
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(Path.cwd()),
    )
    assert proc.returncode == 0, f"子进程异常: {proc.stderr}"
    assert "LOUD" in proc.stdout, (
        "模型不可达时 router 链仍能装配成功——这意味着将来若有人重构掉"
        " routes/hitl.py 对 deps 的依赖，就会退化成静默少表。趁现在钉住。"
    )


# ----------------------------------------------------------------------
# 审计：暂停与决策都留痕
# ----------------------------------------------------------------------
def test_pause_and_decision_are_audited(client: TestClient, headers: dict[str, str],
                                        session_maker):
    interrupt_id = _pause(client, headers).json()["id"]
    _decide(client, headers, interrupt_id)

    from find_yourself.db.models import AuditEvent

    db = session_maker()
    actions = [
        e for e in db.execute(
            text("SELECT action FROM audit_events ORDER BY seq")
        ).scalars()
    ]
    assert "hitl.interrupted" in actions
    assert "hitl.decided" in actions
    assert db.query(AuditEvent).count() >= 2
    db.close()
