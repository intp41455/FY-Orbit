"""Unit tests: B11 持久化 + HTTP 通道（`life_saves` / `/api/cabin/life/*`）。

覆盖说明书对 B11 的全部要求，外加 §2.3 的两条迁移往返：

* **往返 A（全新库）**：0034 建表 → downgrade 回 base → 再 upgrade head；
* **往返 B（已有库）**：表已存在（模拟 `create_all` 抢先建过，且**缺列**）时，
  0034 走 `sa.inspect` 分支**补齐缺列**而不是撞「表已存在」报错；
* **约束名不带双重前缀**：`ck_life_saves_life_save_*`，且与 ORM metadata 反射**同名**
  （P7 在 0032 上踩过的坑，本迁移不得回归）；
* **损坏存档明确报错**：主题/时钟/金币/JSON 结构坏了都抛 `LifeSaveCorrupt` 并带原因，
  **绝不**静默回落成空档；
* **服务端权威**：`PUT` 提交 coins/bag/affinity → 422；只有 `/action` 能改数值；
* **权限**：服务身份读写一律 403；存档 owner 私有；
* **动作白名单**：未知 action → 422；规则层拒绝（材料/金币不足）→ 422 带原因且不落库。
"""

from __future__ import annotations

import importlib
from datetime import timezone
from pathlib import Path
import sys
from typing import Iterator

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.types import TZDateTime
from find_yourself.services.actor import Actor
from find_yourself.services.cabin_life import crafting, npcs, state, themes
from find_yourself.services.cabin_life.persistence import (
    LifeSaveCorrupt,
    LifeSaveRow,
    read_save,
)
from find_yourself.services.cabin_life.service import ACTIONS, LifeService
import find_yourself.db.models  # noqa: F401
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401

_proj_root = str(Path(__file__).resolve().parents[2])
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

m0034 = importlib.import_module("migrations.versions.0034_cabin_life_save")

LOCAL_TOKEN = "dev-token-secret-b11"

EXPECTED_COLUMNS = {
    "owner_id", "theme", "clock", "weather", "bag", "coins", "skill_exp",
    "affinity", "gifts_today", "shop", "quest_log", "gather_counts",
    "version", "created_at", "updated_at",
}


# --- Test-only SQLite TZ shim (same as test_cabin_gameplay.py) ---
def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(eng)
    # 显式注册 life_saves：模型故意不在 db/models 里（§2.3 选项 C），
    # 所以测试夹具必须自己 import 一次，create_all 才会建出这张表。
    LifeSaveRow.__table__.create(eng, checkfirst=True)
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


def _create(client: TestClient, headers: dict, theme: str = "forest") -> dict:
    r = client.post("/api/cabin/life/save", json={"theme": theme}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _action(client: TestClient, headers: dict, **body) -> dict:
    r = client.post("/api/cabin/life/action", json=body, headers=headers)
    return {"status": r.status_code, "body": r.json(), "text": r.text}


def _owner(client: TestClient, headers: dict) -> str:
    return client.get("/api/cabin/life/save", headers=headers).json()["save"]["owner"]


# ======================================================================
# 1. 迁移往返 A：全新库
# ======================================================================


def _mig_conn(existing_ddl: str | None = None):
    """一个只含 0034 所需上下文的连接，并绑定好 alembic `op`。

    用**带 target_metadata 的 MigrationContext**（与 `migrations/env.py` 的真实
    CLI 路径一致）—— 这正是 P7 的教训：裸 `MigrationContext.configure(conn)`
    不套用 NAMING_CONVENTION，会让约束名相关的 bug 在测试里隐形。
    """
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    conn = engine.connect()
    if existing_ddl:
        conn.execute(sa.text(existing_ddl))
    # env.py 的真实路径是 context.configure(..., target_metadata=Base.metadata)，
    # 它把 target_metadata 放进 MigrationContext.opts；alembic 的
    # SchemaObjects.metadata() 从 opts 里取 naming_convention 给 op.create_table
    # 构建的表。所以测试必须同步这一步，
    # 否则约束名的双重前缀等类 bug 会在测试里隐形。
    ctx = MigrationContext.configure(
        connection=conn, opts={"target_metadata": Base.metadata}
    )
    m0034.op = Operations(ctx)
    return engine, conn


def test_down_revision_matches_0033_actual_revision():
    """防止照着文件名猜父节点。"""
    m0033 = importlib.import_module(
        "migrations.versions.0033_preview_source_data_kind"
    )
    assert m0033.revision == "0033_preview_source_data_kind"
    assert m0034.down_revision == m0033.revision


def test_path_a_fresh_create_has_every_column():
    """往返 A 上行：全新库上 0034 建出全部 15 列。"""
    engine, conn = _mig_conn()
    try:
        m0034.upgrade()
        insp = sa.inspect(conn)
        assert insp.has_table("life_saves")
        assert {c["name"] for c in insp.get_columns("life_saves")} == EXPECTED_COLUMNS
    finally:
        conn.close()
        engine.dispose()


def test_path_a_upgrade_is_idempotent_when_run_twice():
    """同一连接上连跑两次 upgrade 不炸、不重复建列。"""
    engine, conn = _mig_conn()
    try:
        m0034.upgrade()
        m0034.upgrade()  # 第二次走 has_table 分支
        cols = {c["name"] for c in sa.inspect(conn).get_columns("life_saves")}
        assert cols == EXPECTED_COLUMNS
    finally:
        conn.close()
        engine.dispose()


def test_path_a_downgrade_drops_table_and_recreate_works():
    """往返 A 完整闭环：upgrade → downgrade → upgrade。"""
    engine, conn = _mig_conn()
    try:
        m0034.upgrade()
        assert sa.inspect(conn).has_table("life_saves")
        m0034.downgrade()
        assert not sa.inspect(conn).has_table("life_saves")
        # 再上一遍必须还能建回来（downgrade 没有留下半截状态）
        m0034.upgrade()
        assert {c["name"] for c in sa.inspect(conn).get_columns("life_saves")} == (
            EXPECTED_COLUMNS
        )
    finally:
        conn.close()
        engine.dispose()


# ======================================================================
# 2. 迁移往返 B：已有库（create_all 抢先建过 / 缺列）
# ======================================================================

#: 只建两列，模拟「0001 的 create_all 已经建过这张表、但列不全」的最坏情况。
PARTIAL_DDL = """
CREATE TABLE life_saves (
    owner_id VARCHAR(200) PRIMARY KEY,
    theme VARCHAR(32) NOT NULL
)
"""


def test_path_b_existing_table_is_repaired_not_rejected():
    """往返 B：`create_all` 抢先建过表时，0034 补列而不是撞「表已存在」。

    这是说明书 §2.3 点名的隐患：若不判存在，全新库上会直接报错、
    已有库上会静默变成 no-op。
    """
    engine, conn = _mig_conn(PARTIAL_DDL)
    try:
        before = {c["name"] for c in sa.inspect(conn).get_columns("life_saves")}
        assert before == {"owner_id", "theme"}, "前置条件：本测试确实从缺列表开始"

        m0034.upgrade()  # 不应抛异常

        after = {c["name"] for c in sa.inspect(conn).get_columns("life_saves")}
        assert after == EXPECTED_COLUMNS, f"缺列未补齐：{EXPECTED_COLUMNS - after}"
    finally:
        conn.close()
        engine.dispose()


def test_path_b_backfill_keeps_existing_rows():
    """补列不得丢已有数据（`ALTER ADD COLUMN` 而非重建表）。"""
    engine, conn = _mig_conn(PARTIAL_DDL)
    try:
        conn.execute(
            sa.text("INSERT INTO life_saves (owner_id, theme) VALUES ('o1', 'forest')")
        )
        m0034.upgrade()
        rows = conn.execute(
            sa.text("SELECT owner_id, theme FROM life_saves")
        ).fetchall()
        assert rows == [("o1", "forest")], "补列把既有存档弄丢了"
    finally:
        conn.close()
        engine.dispose()


def test_path_b_downgrade_then_upgrade_works():
    """往返 B 完整闭环。"""
    engine, conn = _mig_conn(PARTIAL_DDL)
    try:
        m0034.upgrade()
        m0034.downgrade()
        assert not sa.inspect(conn).has_table("life_saves")
        m0034.upgrade()
        assert {c["name"] for c in sa.inspect(conn).get_columns("life_saves")} == (
            EXPECTED_COLUMNS
        )
    finally:
        conn.close()
        engine.dispose()


# ======================================================================
# 3. 约束名：不得双重前缀，且与 ORM 同名
# ======================================================================


def test_constraint_names_have_no_double_prefix():
    """P7 同款回归守卫：不能出现 `ck_life_saves_ck_life_saves_*`。"""
    engine, conn = _mig_conn()
    try:
        m0034.upgrade()
        names = {c["name"] for c in sa.inspect(conn).get_check_constraints("life_saves")}
        assert names, "CHECK 约束一条都没有，迁移没生效"
        for n in names:
            assert "ck_life_saves_ck_" not in n, f"约束名双重前缀：{n}"
            assert n.startswith("ck_life_saves_"), f"约束名缺少表前缀：{n}"
    finally:
        conn.close()
        engine.dispose()


def test_constraint_names_match_orm_metadata_exactly():
    """迁移建出来的约束名必须与 ORM metadata 反射出来的**完全一致**。

    不一致意味着将来 `drop_constraint` / autogenerate 会找错名字 ——
    这类 bug 在功能测试里是隐形的，只有比对两边的名字才抓得到。
    """
    engine, conn = _mig_conn()
    try:
        m0034.upgrade()
        db_names = {c["name"] for c in sa.inspect(conn).get_check_constraints("life_saves")}
        orm_names = {
            c.name for c in LifeSaveRow.__table__.constraints
            if isinstance(c, sa.CheckConstraint)
        }
        assert db_names == orm_names, f"迁移/ORM 约束名不一致：{db_names ^ orm_names}"
    finally:
        conn.close()
        engine.dispose()


# ======================================================================
# 4. 损坏存档：明确报错，绝不静默回落空档
# ======================================================================


def _corrupt_row(**overrides) -> LifeSaveRow:
    """构造一行**合法**存档，再按需把某个字段改成坏数据。"""
    save = state.new_save("o1", "forest")
    row = LifeSaveRow(owner_id="o1")
    from find_yourself.services.cabin_life.persistence import apply_save

    apply_save(row, save)
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


#: 每个用例：(说明, 覆盖字段, 原因里必须出现的关键词)
CORRUPTION_CASES = [
    ("主题不存在", {"theme": "atlantis"}, "主题"),
    ("时钟不是对象", {"clock": ["day", 1]}, "clock"),
    ("时钟缺 day", {"clock": {"minute": 60}}, "clock"),
    ("时钟缺 minute", {"clock": {"day": 1}}, "clock"),
    ("时钟 minute 越界", {"clock": {"day": 1, "minute": 99999}}, "1440"),
    ("金币不是整数", {"coins": "999"}, "coins"),
    ("金币是 bool（int 子类陷阱）", {"coins": True}, "coins"),
    ("金币为负", {"coins": -5}, "coins"),
    ("经验为负", {"skill_exp": -1}, "skill_exp"),
    ("背包不是对象", {"bag": [1, 2]}, "bag"),
    ("背包值不是整数", {"bag": {"wood": "many"}}, "bag"),
    ("好感值是字符串", {"affinity": {"npc": "high"}}, "affinity"),
    ("天气不是对象", {"weather": None}, "weather"),
    ("店铺不是对象", {"shop": 3}, "shop"),
    ("任务日志缺 entries", {"quest_log": {"day": 1}}, "quest_log"),
    ("任务 entries 不是数组", {"quest_log": {"day": 1, "entries": {}}}, "entries"),
    ("任务条目缺 quest_id", {"quest_log": {"day": 1, "entries": [{"progress": 1}]}}, "quest_id"),
    ("version 为 0", {"version": 0}, "version"),
]


@pytest.mark.parametrize(
    "label,overrides,keyword",
    CORRUPTION_CASES,
    ids=[c[0] for c in CORRUPTION_CASES],
)
def test_corrupt_save_raises_with_reason(label, overrides, keyword):
    """坏存档必须抛 `LifeSaveCorrupt` 且原因里点出具体字段。"""
    row = _corrupt_row(**overrides)
    with pytest.raises(LifeSaveCorrupt) as exc:
        read_save(row)
    assert keyword in exc.value.reason, f"原因未点出字段：{exc.value.reason!r}"
    assert exc.value.code == "life_save_corrupt"
    # 关键：不能回落成新存档，也不能悄悄改成合法值
    assert exc.value.http_status == 409


def test_corrupt_save_never_silently_resets_to_blank():
    """回归守卫：损坏存档**绝不能**变成一份合法的空档。

    变异测试思路：若有人把 `read_save` 改成「try/except 后回落 new_save」，
    本测试会因为「没有抛异常」而失败。
    """
    row = _corrupt_row(theme="atlantis")
    with pytest.raises(LifeSaveCorrupt):
        read_save(row)
    # 且行本身没被就地改写（不能顺手「修复」掉玩家的数据）
    assert row.theme == "atlantis"


def test_valid_save_roundtrips_without_loss():
    """合法存档 row → LifeSave → row 往返无损。"""
    from find_yourself.services.cabin_life.persistence import apply_save

    save = state.new_save("o1", "magic")
    save.coins = 777
    save.bag = {"wood": 3, "herb": 1}
    save.skill_exp = 40
    row = LifeSaveRow(owner_id="o1")
    apply_save(row, save)

    back = read_save(row)
    assert back.theme == "magic"
    assert back.coins == 777
    assert back.bag == {"wood": 3, "herb": 1}
    assert back.skill_exp == 40
    assert back.clock["day"] == save.clock["day"]
    assert back.clock["minute"] == save.clock["minute"]
    # affinity 应覆盖该主题全部 NPC
    assert set(back.affinity) == {n.id for n in npcs.npcs_of("magic")}


def test_clock_part_is_recomputed_when_missing():
    """`part` 是派生值：存档里缺它时按 minute 重算，而不是让上层 KeyError。"""
    row = _corrupt_row(clock={"day": 3, "minute": 8 * 60})
    back = read_save(row)
    assert back.clock["part"], "part 应被重算出来"
    assert back.clock["part"] == "morning"


# ======================================================================
# 5. HTTP 通道：建档 / 读快照 / 权限
# ======================================================================


def test_get_save_before_create_is_404(client: TestClient, headers: dict):
    """没有存档就明确 404，不静默给一份空档。"""
    r = client.get("/api/cabin/life/save", headers=headers)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "life_save_missing"


def test_create_then_snapshot_has_all_panels(client: TestClient, headers: dict):
    """建档后一次 GET 就能拿到 HUD + 社交/经营/制作/采集四份面板数据。"""
    body = _create(client, headers, "forest")
    assert body["save"]["theme"] == "forest"
    assert body["hud_line"]

    r = client.get("/api/cabin/life/save", headers=headers)
    assert r.status_code == 200, r.text
    snap = r.json()
    for key in ("save", "hud_line", "npcs", "shop", "craft", "gather", "version"):
        assert key in snap, f"快照缺 {key}"
    assert snap["npcs"], "社交面板不能为空"
    assert snap["shop"], "经营面板不能为空"
    assert snap["craft"], "制作台不能为空"
    assert snap["gather"], "采集点不能为空"


def test_create_twice_is_rejected(client: TestClient, headers: dict):
    """重复建档 422，不覆盖已有进度。"""
    _create(client, headers, "forest")
    r = client.post("/api/cabin/life/save", json={"theme": "magic"}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "life_save_exists"


def test_create_unknown_theme_rejected(client: TestClient, headers: dict):
    r = client.post(
        "/api/cabin/life/save", json={"theme": "atlantis"}, headers=headers
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "life_unknown_theme"


def test_snapshot_honours_player_tile_for_distance(client: TestClient, headers: dict):
    """给了玩家坐标才算 in_range；不给则一律 false（不假装够得着）。"""
    _create(client, headers, "forest")
    snap = client.get(
        "/api/cabin/life/save?player_x=0&player_y=0", headers=headers
    ).json()
    assert all(row["in_range"] is False for row in snap["gather"])
    assert all(row["distance"] is not None for row in snap["gather"])

    near = snap["gather"][0]
    tx, ty = near["tile"]
    snap2 = client.get(
        f"/api/cabin/life/save?player_x={tx}&player_y={ty}", headers=headers
    ).json()
    hit = next(r for r in snap2["gather"] if r["id"] == near["id"])
    assert hit["in_range"] is True
    assert hit["distance"] == 0


def test_meta_lists_all_themes_and_actions(client: TestClient, headers: dict):
    """`/meta` 是前端单一真源：主题清单必须与后端常量一致。"""
    r = client.get("/api/cabin/life/meta", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["id"] for t in body["themes"]] == list(themes.theme_ids())
    assert body["actions"] == sorted(ACTIONS)
    # 常量不得在路由层拷贝，必须等于规则模块的值
    assert body["max_hearts"] == npcs.MAX_HEARTS
    assert body["max_gifts_per_day"] == npcs.MAX_GIFTS_PER_DAY


def test_service_actor_cannot_touch_saves(session_maker):
    """服务身份一律 403（存档是 owner 私有）。"""
    svc = Actor.service(service_id="svc-1", kind="internal")
    db = session_maker()
    try:
        for call in (
            lambda: LifeService(db).snapshot(svc),
            lambda: LifeService(db).create(svc, theme="forest"),
            lambda: LifeService(db).action(svc, {"action": "sleep"}),
            lambda: LifeService(db).delete(svc),
        ):
            with pytest.raises(Exception) as exc:
                call()
            assert getattr(exc.value, "http_status", None) == 403, exc.value
    finally:
        db.close()


def test_unauthenticated_actor_rejected(session_maker):
    """匿名身份连存档都不给看。"""
    from find_yourself.services.actor import Actor as A

    anon = A(subject_type="", owner_id="", csrf_token="")
    db = session_maker()
    try:
        with pytest.raises(Exception) as exc:
            LifeService(db).snapshot(anon)
        assert getattr(exc.value, "http_status", None) in (401, 403)
    finally:
        db.close()


# ======================================================================
# 6. 服务端权威：客户端不能直接改数值
# ======================================================================


@pytest.mark.parametrize(
    "field", ["coins", "bag", "affinity", "skill_exp", "gifts_today", "gather_counts"]
)
def test_put_rejects_numeric_fields(client: TestClient, headers: dict, field: str):
    """`PUT` 提交任何结算字段一律 422 —— 数值只能由 /action 算出。"""
    _create(client, headers, "forest")
    before = client.get("/api/cabin/life/save", headers=headers).json()["save"]
    r = client.put(
        "/api/cabin/life/save", json={field: {"a": 1}}, headers=headers
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "life_server_authoritative"
    # 并且存档真的没被改（真比对前后值）
    snap = client.get("/api/cabin/life/save", headers=headers).json()
    assert snap["save"][field] == before[field]


def test_put_can_switch_theme_and_keeps_progress(client: TestClient, headers: dict):
    """换主题保留玩家挣来的金币/背包/经验（换画风不清零）。"""
    _create(client, headers, "forest")
    owner = _owner(client, headers)

    # 先给点进度：通过 restock 需要金币，这里直接用 settings 之外的路径不便，
    # 故改用快照断言「换主题后 coins 仍等于换之前」。
    before = client.get("/api/cabin/life/save", headers=headers).json()["save"]

    r = client.put(
        "/api/cabin/life/save",
        json={"settings": {"active_theme": "ink"}},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    after = r.json()["save"]
    assert after["theme"] == "ink"
    assert after["owner"] == owner
    assert after["coins"] == before["coins"]
    assert after["bag"] == before["bag"]


def test_put_unknown_theme_rejected(client: TestClient, headers: dict):
    _create(client, headers, "forest")
    r = client.put(
        "/api/cabin/life/save",
        json={"settings": {"active_theme": "atlantis"}},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "life_unknown_theme"


def test_put_stale_version_rejected(client: TestClient, headers: dict):
    """乐观锁：基于旧版本的写要 422，而不是覆盖别人的改动。"""
    _create(client, headers, "forest")
    stale = client.get("/api/cabin/life/save", headers=headers).json()["version"]
    client.put(
        "/api/cabin/life/save",
        json={"settings": {"active_theme": "ink"}, "expected_version": stale},
        headers=headers,
    )
    r = client.put(
        "/api/cabin/life/save",
        json={"settings": {"active_theme": "magic"}, "expected_version": stale},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "life_version_conflict"


# ======================================================================
# 7. 动作通道：白名单 / 拒绝要带原因 / 不落库
# ======================================================================


def test_unknown_action_rejected(client: TestClient, headers: dict):
    """没有「直接改数值」的后门：未知 action 一律 422。"""
    _create(client, headers, "forest")
    r = _action(client, headers, action="give_me_coins", coins=9999)
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "life_unknown_action"

    snap = client.get("/api/cabin/life/save", headers=headers).json()["save"]
    assert snap["coins"] == 0, "被拒的动作竟然改了金币"


def test_action_on_missing_save_is_404(client: TestClient, headers: dict):
    r = _action(client, headers, action="sleep")
    assert r["status"] == 404, r["text"]


def test_gather_action_updates_bag_and_clock(client: TestClient, headers: dict):
    """采集：进背包、推进时钟、记次数，且带动画键供 B1/B2 播放。"""
    _create(client, headers, "forest")
    snap = client.get("/api/cabin/life/save", headers=headers).json()
    node = snap["gather"][0]

    before_minute = snap["save"]["clock"]["minute"]
    r = _action(client, headers, action="gather", node_id=node["id"])
    assert r["status"] == 200, r["text"]
    body = r["body"]
    assert body["material"] == node["material"]
    assert body["qty"] >= 1
    assert body["minutes"] > 0
    assert body["animation"], "必须回传动画键（B1/B2 表现层要用）"
    assert body["save"]["bag"][node["material"]] >= body["qty"]
    assert body["save"]["clock"]["minute"] != before_minute
    assert body["save"]["gather_counts"][node["id"]] == 1


def test_gather_rejected_leaves_save_untouched(client: TestClient, headers: dict):
    """规则层拒绝 → 422 带原因，且**不落任何改动**。"""
    _create(client, headers, "forest")
    before = client.get("/api/cabin/life/save", headers=headers).json()["save"]

    r = _action(client, headers, action="gather", node_id="not_a_real_node")
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "life_gather_rejected"

    after = client.get("/api/cabin/life/save", headers=headers).json()["save"]
    assert after["bag"] == before["bag"]
    assert after["clock"]["minute"] == before["clock"]["minute"]
    assert after["gather_counts"] == before["gather_counts"]


def test_craft_rejected_when_materials_missing(client: TestClient, headers: dict):
    """材料不足 → 422 且原因点出缺什么，不静默做一半。"""
    _create(client, headers, "forest")
    snap = client.get("/api/cabin/life/save", headers=headers).json()
    recipe = next(r for r in snap["craft"] if r["unlocked"])

    r = _action(client, headers, action="craft", recipe_id=recipe["id"])
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "life_craft_rejected"
    assert "材料不足" in r["body"]["error"]["message"]


def test_sleep_advances_day_and_resets_daily_counters(client: TestClient, headers: dict):
    """睡觉 = 日结算 + 跳到次日 06:00 + 清每日计数（好感/金币不清）。"""
    _create(client, headers, "forest")
    snap = client.get("/api/cabin/life/save", headers=headers).json()
    node = snap["gather"][0]
    assert _action(client, headers, action="gather", node_id=node["id"])["status"] == 200

    before = client.get("/api/cabin/life/save", headers=headers).json()["save"]
    r = _action(client, headers, action="sleep")
    assert r["status"] == 200, r["text"]
    body = r["body"]
    assert body["save"]["clock"]["day"] == before["clock"]["day"] + 1
    assert body["save"]["clock"]["minute"] == 360, "次日 06:00"
    assert body["save"]["gather_counts"] == {}, "每日计数应清零"
    assert body["save"]["affinity"] == before["affinity"], "好感不该被清"
    assert body["save"]["bag"] == before["bag"], "背包不该被清"
    assert body["settlement"]["lines"] == [], "没进货就没有售出明细"


def test_sleep_settles_stock_and_reports_leftovers(
    client: TestClient, headers: dict, session_maker
):
    """进货后睡觉要真的结算，未售出部分如实留在库存。"""
    _create(client, headers, "forest")
    snap = client.get("/api/cabin/life/save", headers=headers).json()
    good = next(g for g in snap["shop"] if g["unlocked"])

    # 1. 验证金币不足时被拒
    r = _action(client, headers, action="restock", good_id=good["id"], qty=1)
    assert r["status"] == 422, r["text"]
    assert "金币不足" in r["body"]["error"]["message"]
    st = client.get("/api/cabin/life/save", headers=headers).json()["save"]
    assert st["shop"]["stock"].get(good["id"], 0) == 0, "被拒的进货不该进库存"

    # 2. 给初始金币（1000），实测进货 5 件与隔日真实结算
    owner = _owner(client, headers)
    db = session_maker()
    try:
        row = db.get(LifeSaveRow, owner)
        row.coins = 1000
        db.commit()
    finally:
        db.close()

    r_restock = _action(client, headers, action="restock", good_id=good["id"], qty=5)
    assert r_restock["status"] == 200, r_restock["text"]
    assert r_restock["body"]["save"]["shop"]["stock"][good["id"]] == 5

    # 3. 睡觉触发隔日结算
    r_sleep = _action(client, headers, action="sleep")
    assert r_sleep["status"] == 200, r_sleep["text"]
    settlement = r_sleep["body"]["settlement"]
    assert "coins_after" in settlement
    assert "lines" in settlement
    line = next((l for l in settlement["lines"] if l["good_id"] == good["id"]), None)
    assert line is not None, "进货商品必须出现在结算明细中"
    sold = line["sold"]
    leftover = line["leftover"]
    assert sold + leftover == 5, f"售出({sold}) + 剩余({leftover}) 必须等于进货数(5)"

    st = client.get("/api/cabin/life/save", headers=headers).json()["save"]
    assert st["shop"]["stock"].get(good["id"], 0) == leftover, "未售出部分必须如实留在库存"
    assert st["coins"] == settlement["coins_after"]
    assert "06:00" in r_sleep["body"]["note"]


def test_corrupt_save_via_http_returns_409(
    client: TestClient, headers: dict, session_maker
):
    """坏存档写进 DB → 走 HTTP GET/action → 断言真返 409 (life_save_corrupt)，绝不静默兜底。"""
    _create(client, headers, "forest")
    owner = _owner(client, headers)

    db = session_maker()
    try:
        row = db.get(LifeSaveRow, owner)
        assert row is not None
        # 破坏主题字段为不存在的主题
        row.theme = "atlantis"
        db.commit()
    finally:
        db.close()

    # GET 请求必须真返 409
    r_get = client.get("/api/cabin/life/save", headers=headers)
    assert r_get.status_code == 409, r_get.text
    assert r_get.json()["error"]["code"] == "life_save_corrupt"
    assert "atlantis" in r_get.json()["error"]["message"]

    # POST action 请求同样真返 409
    r_act = client.post(
        "/api/cabin/life/action", json={"action": "sleep"}, headers=headers
    )
    assert r_act.status_code == 409, r_act.text
    assert r_act.json()["error"]["code"] == "life_save_corrupt"


def test_delete_save_via_http(client: TestClient, headers: dict):
    """DELETE /api/cabin/life/save 完整生命周期测试（建档 → DELETE → 200 → 再次 GET 返 404）。"""
    _create(client, headers, "forest")
    owner = _owner(client, headers)

    # 1. 显式删除存档
    r_del = client.delete("/api/cabin/life/save", headers=headers)
    assert r_del.status_code == 200, r_del.text
    assert r_del.json() == {"deleted": True, "owner": owner}

    # 2. 删后 GET 必须 404
    r_get = client.get("/api/cabin/life/save", headers=headers)
    assert r_get.status_code == 404, r_get.text
    assert r_get.json()["error"]["code"] == "life_save_missing"

    # 3. 再次 DELETE 必须 404
    r_del2 = client.delete("/api/cabin/life/save", headers=headers)
    assert r_del2.status_code == 404, r_del2.text
    assert r_del2.json()["error"]["code"] == "life_save_missing"

    # 4. 删档后可重新建档开新周目
    r_recreate = client.post(
        "/api/cabin/life/save", json={"theme": "magic"}, headers=headers
    )
    assert r_recreate.status_code == 200, r_recreate.text
    assert client.get("/api/cabin/life/save", headers=headers).json()["save"]["theme"] == "magic"


def test_version_is_monotonic_across_theme_switch(client: TestClient, headers: dict):
    """回归守卫：换主题**不得**让 version 倒退（倒退 = 乐观锁失效）。

    变异测试思路：若去掉 `save.version = old.version`，本测试会失败。
    """
    _create(client, headers, "forest")
    v0 = client.get("/api/cabin/life/save", headers=headers).json()["version"]
    r = client.put(
        "/api/cabin/life/save",
        json={"settings": {"active_theme": "ink"}},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    v1 = r.json()["save"]["version"]
    assert v1 > v0, f"version 倒退了：{v0} → {v1}"


def test_version_increments_on_every_action(client: TestClient, headers: dict):
    """每次成功动作都应推进版本号（并发写的最后一道防线）。"""
    _create(client, headers, "forest")
    snap = client.get("/api/cabin/life/save", headers=headers).json()
    node = snap["gather"][0]
    v0 = snap["version"]
    r = _action(client, headers, action="gather", node_id=node["id"])
    assert r["status"] == 200, r["text"]
    assert r["body"]["save"]["version"] > v0


# ======================================================================
# 8. 跨服务隔离：绝不碰 W2 的 /api/cabin/save
# ======================================================================


def test_w2_routes_still_owned_by_w2(client: TestClient, headers: dict):
    """回归守卫：`/api/cabin/save` 仍归 W2，不能被 B11 抢走。"""
    from find_yourself.api.routes import cabin_gameplay, cabin_life

    w2_paths = {r.path for r in cabin_gameplay.router.routes}
    b11_paths = {r.path for r in cabin_life.router.routes}

    assert "/api/cabin/save" in w2_paths, "W2 的裸 /save 路由不见了"
    assert "/api/cabin/save" not in b11_paths, "B11 不得占用 /api/cabin/save"
    assert "/api/cabin/save/action" in w2_paths
    assert "/api/cabin/save/action" not in b11_paths
    # B11 的全部路径都必须带 life/ 这一层
    assert all(p.startswith("/api/cabin/life/") for p in b11_paths), b11_paths
    assert not (w2_paths & b11_paths), f"路由撞车：{w2_paths & b11_paths}"


def test_w2_save_endpoint_still_works_alongside_b11(client: TestClient, headers: dict):
    """两套系统并存：B11 建档不影响 W2 的 `/api/cabin/save`。"""
    _create(client, headers, "forest")
    r = client.get("/api/cabin/save?theme=forest", headers=headers)
    assert r.status_code == 200, r.text
    assert "materials" in r.json(), "W2 存档结构应保持不变"
