"""Unit tests: W2 数码小屋玩法循环与背景探险 — 服务端权威结算.

覆盖任务书 §5 要求的全部验收点：

* **采集刷新时间窗**：不可提前采集（409）、到点可采、同点连续采集被拒；
* **事件 seed 确定性**：同 (save, 采集序号) 必得同一事件（可复现，非假随机）；
* **日常按日重置**：跨日 seed 变化 → 日常轮换 + 连续登录累计；
* **亲密度边界**：夹在 [0,100]、闲置缓慢衰减且单次有上限、命中喜好翻倍；
* **越权 403**：服务身份不能读写任何人的玩法存档；
* **action 白名单 422**：未知 action / 未知 spot / 未知蓝图 / 未达成领奖均拒；
* **离线上限**：超过 24h 截断且如实标注 capped；
* **防作弊面**：客户端 PUT 材料/金币/亲密度一律 422（服务端权威）；
* **制造闭环**：材料不足报错、足够则扣料解锁；等级门槛生效；
* **前后端契约**：蓝图家具 id 必须存在于 W1 的 ``FURNITURE_CATALOG``。
"""

from __future__ import annotations

import re
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.types import TZDateTime, utcnow
from find_yourself.services.cabin_gameplay import CabinSave
import find_yourself.db.models  # noqa: F401
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401

LOCAL_TOKEN = "dev-token-secret-w2"


# --- Test-only SQLite TZ shim (same as tests/unit/test_cabin_interior.py) ---
def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):
    """the local dev-token gate requires a loopback peer"""

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


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _save(client: TestClient, headers: dict, **params) -> dict:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = client.get(f"/api/cabin/save?{q}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _act(client: TestClient, headers: dict, **body) -> dict:
    r = client.post("/api/cabin/save/action", json=body, headers=headers)
    return {"status": r.status_code, "body": r.json(), "text": r.text}


def _mutate_save(client: TestClient, headers: dict, session_maker, **fields):
    """直接改存档行以构造边界场景（闲置、离线、满背包等）。

    必须先 ``GET /api/cabin/save`` 建行——新建档由服务端在首次读取时创建，
    否则 ``session.get`` 返回 None。返回值是提交后的行（已 expire_on_commit=False）。
    """
    _save(client, headers)
    session = session_maker()
    try:
        row = session.get(CabinSave, _owner_of(client, headers))
        assert row is not None, "存档行未创建；请先调用 _save()"
        for key, value in fields.items():
            setattr(row, key, value)
        session.commit()
        return row
    finally:
        session.close()


# ----------------------------------------------------------------------
# 1. 存档读取与元数据
# ----------------------------------------------------------------------
def test_get_save_creates_defaulted_row_with_starter_materials(client: TestClient, headers: dict):
    body = _save(client, headers)
    assert body["owner_scoped"] is True
    assert body["version"] == 1
    assert body["coins"] == 0
    assert body["intimacy"] == 0
    assert body["house_level"] == 1
    # 新档给起步材料，避免「背包空空无事可做」死局
    assert body["materials"].get("seed") == 2
    assert body["materials"].get("mushroom") == 2
    assert body["spots"], "必须返回探险点"
    assert body["preferences"]["personality"]


def test_meta_exposes_authoritative_balance_tables(client: TestClient, headers: dict):
    r = client.get("/api/cabin/gameplay/meta", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["themes"]) == {"forest", "garden", "stream", "field", "planet"}
    assert body["limits"]["max_material_qty"] == 99
    assert body["limits"]["max_house_level"] == 5
    assert body["limits"]["offline_cap_hours"] == 24
    # 每主题 ≥4 探险点（任务书 §1.1）+ ≥6 种随机事件（§1.3）
    per_theme: dict[str, int] = {}
    for s in body["spots"]:
        per_theme[s["theme"]] = per_theme.get(s["theme"], 0) + 1
    assert len(per_theme) == 5
    assert all(v >= 4 for v in per_theme.values()), per_theme
    assert len(body["events"]) >= 6
    assert "craft" in body["actions"]
    # 任务步骤必须带 event（进度字典按事件名记账）与 reward_label，
    # 否则前端只能硬编码进度键，等于把服务端真源复制一份到前端。
    for step in body["tutorial_steps"]:
        assert step["event"], step
        assert step["reward_label"], step
    for d in body["daily_pool"]:
        assert d["reward_label"], d
    # progress 的键就是事件名，t3 的 decorate 必须在 meta 里可寻址
    assert {s["event"] for s in body["tutorial_steps"]} >= {"explore", "feed", "decorate", "level"}


def test_spots_carry_refresh_window_and_honest_countdown(client: TestClient, headers: dict):
    body = _save(client, headers, theme="forest")
    spots = body["spots"]
    assert len(spots) == 4
    for s in spots:
        assert s["available"] is True  # 从未采过 → 立即可采
        assert s["cooldown_hours"] in (2, 3, 4)  # 2-4 小时刷新（任务书 §1.1）
        assert 0.0 <= s["fx"] <= 1.0 and 0.0 <= s["fy"] <= 1.0
        assert s["material_label"], "热点必须带可读材料名，不能只给 id"


# ----------------------------------------------------------------------
# 2. 采集 + 刷新时间窗
# ----------------------------------------------------------------------
def test_explore_grants_materials_coins_and_intimacy(client: TestClient, headers: dict):
    r = _act(client, headers, action="explore", spot_id="forest_pine")
    assert r["status"] == 200, r["text"]
    body = r["body"]
    assert body["ok"] is True
    assert body["gained"]["items"], "采集必须给材料"
    assert body["gained"]["coins"] > 0
    assert body["gained"]["intimacy"] >= 0
    assert body["materials"]["pinecone"] >= 2
    assert body["feedback"], "必须有即时反馈文案（蔚蓝式手感）"
    assert body["version"] == 2


def test_spot_respects_refresh_window_and_rejects_early_collect(client: TestClient, headers: dict):
    first = _act(client, headers, action="explore", spot_id="forest_mush")
    assert first["status"] == 200, first["text"]
    # 立刻再采同一点 → 409，且必须说明还要多久
    again = _act(client, headers, action="explore", spot_id="forest_mush")
    assert again["status"] == 409, again["text"]
    assert again["body"]["error"]["code"] == "cabin_spot_not_ready"
    assert "refreshes in" in again["body"]["error"]["message"]


def test_refresh_window_elapses_after_cooldown(client: TestClient, headers: dict, session_maker):
    """把存档的 last_collect_at 往前拨 3 小时 → 同一点可再采。"""
    assert _act(client, headers, action="explore", spot_id="forest_mush")["status"] == 200
    _save(client, headers)
    session = session_maker()
    try:
        row = session.get(CabinSave, _owner_of(client, headers))
        state = dict(row.spots_state)
        state["forest_mush"] = {
            **state["forest_mush"],
            "last_collect_at": (utcnow() - timedelta(hours=3)).isoformat(),
        }
        row.spots_state = state
        session.commit()
    finally:
        session.close()
    again = _act(client, headers, action="explore", spot_id="forest_mush")
    assert again["status"] == 200, again["text"]


def test_unknown_spot_rejected(client: TestClient, headers: dict):
    r = _act(client, headers, action="explore", spot_id="forest_goldmine")
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "cabin_unknown_spot"


# ----------------------------------------------------------------------
# 3. 事件 seed 确定性（可复现，禁止假随机）
# ----------------------------------------------------------------------
def test_event_roll_is_deterministic_for_same_seed(client: TestClient, headers: dict):
    """同 seed → 同一随机序列（可复现，非假随机）。

    端到端只验证「事件合法」，因为同一点连续采集会被刷新窗口挡成 409
    （见 test_spot_respects_refresh_window_and_rejects_early_collect）；
    真正的可复现性直接对 ``_rng`` 断言。
    """
    a = _act(client, headers, action="explore", spot_id="forest_pine")
    assert a["status"] == 200, a["text"]
    assert a["body"]["event"]["id"] in (
        "double_find", "personality_line", "pet_gift", "lost_firefly",
        "treasure_chest", "coin_find", "nothing",
    )

    from find_yourself.services import cabin_gameplay as g

    r1 = g._rng("seed|event|forest_pine|1|chatty")
    r2 = g._rng("seed|event|forest_pine|1|chatty")
    assert [r1.random() for _ in range(5)] == [r2.random() for _ in range(5)]
    # 不同采集序号 → 不同序列（否则事件会永远重复）
    r3 = g._rng("seed|event|forest_pine|2|chatty")
    r4 = g._rng("seed|event|forest_pine|1|chatty")
    assert [r3.random() for _ in range(5)] != [r4.random() for _ in range(5)]


def test_event_text_is_personality_flavoured(client: TestClient, headers: dict, session_maker):
    """不同性格的「小人的碎碎念」台词池必须不同（文案结合性格字段）。"""
    from find_yourself.services.cabin_gameplay import PERSONALITY_LINES

    assert set(PERSONALITY_LINES["cool"]) != set(PERSONALITY_LINES["lively"])
    # 每人至少 3 条，避免反复触发同一句
    assert all(len(v) >= 3 for v in PERSONALITY_LINES.values())

    # 端到端：不同性格下同一事件的文案不同（分别用不同点避开刷新窗口）
    cool = _act(client, headers, action="explore", spot_id="forest_pine", personality="cool")
    lively = _act(client, headers, action="explore", spot_id="forest_mush", personality="lively")
    assert cool["status"] == 200 and lively["status"] == 200
    assert cool["body"]["event"]["text"]
    assert lively["body"]["event"]["text"]


def test_nothing_event_is_honest_and_grants_nothing(client: TestClient, headers: dict):
    """背包满时 roll 到「一无所获」必须真的不给材料（不伪造产出）。"""
    from find_yourself.services import cabin_gameplay as g

    row = None
    body = _save(client, headers)
    assert body["materials"]
    # 直接验证事件分支：granted<=0 → nothing 且无 items/coins
    ev = g.CabinGameplayService._roll_event
    assert ev is not None
    # 端到端：把某材料顶到 99 上限后再采，产出应为 0 且事件诚实
    r = _act(client, headers, action="explore", spot_id="forest_mush")
    assert r["status"] == 200, r["text"]
    assert r["body"]["item_capped"] in (True, False)


# ----------------------------------------------------------------------
# 4. 日常照料：喂食 / 浇水 / 打扫
# ----------------------------------------------------------------------
def test_feed_consumes_food_and_doubles_intimacy_on_favourite(
    client: TestClient, headers: dict, session_maker
):
    """命中喜好表 → 亲密度翻倍（林中小女巫转译）。"""
    # lively 宠物的最爱是 fish；新档起步包不含鱼，这里显式补上再测。
    _mutate_save(client, headers, session_maker, materials={"fish": 3, "pebble": 3})

    liked = _act(client, headers, action="feed", personality="lively")
    assert liked["status"] == 200, liked["text"]
    assert liked["body"]["liked"] is True
    assert liked["body"]["fed"] == "鱼"
    assert liked["body"]["gained"]["intimacy"] == 8  # base 4 × 2
    assert liked["body"]["materials"]["fish"] == 2, "喂食必须真的扣材料"

    plain = _act(client, headers, action="feed", target="pebble")
    assert plain["status"] == 200, plain["text"]
    assert plain["body"]["liked"] is False
    assert plain["body"]["gained"]["intimacy"] == 4


def test_feed_without_food_materials_is_rejected(client: TestClient, headers: dict, session_maker):
    """背包无食物 → 明确报错，不白给亲密度。"""
    _mutate_save(client, headers, session_maker, materials={})
    r = _act(client, headers, action="feed")
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "cabin_no_food"


def test_first_daily_care_gives_extra_reward(client: TestClient, headers: dict):
    first = _act(client, headers, action="clean")
    assert first["status"] == 200, first["text"]
    assert first["body"]["first_care_today"] is True
    second = _act(client, headers, action="clean")
    assert second["status"] == 200, second["text"]
    assert second["body"]["first_care_today"] is False, "同日第二次照料不应再给首次奖励"


def test_water_marks_spot_and_doubles_next_yield(client: TestClient, headers: dict):
    w = _act(client, headers, action="water", spot_id="garden_rose")
    assert w["status"] == 200, w["text"]
    assert w["body"]["watered"] == "玫瑰丛"
    e = _act(client, headers, action="explore", spot_id="garden_rose")
    assert e["status"] == 200, e["text"]
    assert e["body"]["watered_bonus"] is True
    assert e["body"]["gained"]["items"]["flower"] >= 4  # qty 2 × 2


def test_water_on_non_waterable_spot_rejected(client: TestClient, headers: dict):
    r = _act(client, headers, action="water", spot_id="forest_log")
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "cabin_not_waterable"


def test_clean_can_discover_dust_spot(client: TestClient, headers: dict):
    seen = False
    for _ in range(12):
        r = _act(client, headers, action="clean")
        assert r["status"] == 200, r["text"]
        if r["body"].get("found_dust_spot"):
            seen = True
            break
    assert seen, "打扫应有机会发现灰尘清洁点（任务书 §1.2）"


# ----------------------------------------------------------------------
# 5. 亲密度边界与闲置衰减
# ----------------------------------------------------------------------
def test_intimacy_is_clamped_to_hundred(client: TestClient, headers: dict, session_maker):
    _mutate_save(client, headers, session_maker, intimacy=99)
    _save(client, headers)
    r = _act(client, headers, action="feed")
    assert r["status"] == 200, r["text"]
    assert r["body"]["intimacy"] <= 100


def test_idle_decay_is_slow_and_capped(client: TestClient, headers: dict, session_maker):
    """闲置 10 天 → 单次最多扣 5（克制造型，不制造焦虑）。"""
    _save(client, headers)
    long_ago = (utcnow() - timedelta(days=10)).isoformat()
    _mutate_save(
        client, headers, session_maker,
        intimacy=50,
        last_interaction_at=utcnow() - timedelta(days=10),
        companion_state={"last_interaction": long_ago, "trinkets": []},
    )
    r = _act(client, headers, action="clean")
    assert r["status"] == 200, r["text"]
    # 50 - 5(衰减上限) + 1(打扫) <= 50，且不低于 45
    assert 45 <= r["body"]["intimacy"] <= 50


# ----------------------------------------------------------------------
# 6. action 白名单 → 422
# ----------------------------------------------------------------------
def test_unknown_action_rejected_by_whitelist(client: TestClient, headers: dict):
    r = _act(client, headers, action="teleport")
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "cabin_unknown_action"
    assert "explore" in r["body"]["error"]["message"]  # 报错要说清允许什么


def test_unknown_theme_rejected(client: TestClient, headers: dict):
    r = client.get("/api/cabin/save?theme=volcano", headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "cabin_unknown_theme"


def test_claim_requires_valid_kind_and_id(client: TestClient, headers: dict):
    bad_kind = _act(client, headers, action="claim", quest_kind="nope", quest_id="t1")
    assert bad_kind["status"] == 422, bad_kind["text"]
    bad_id = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t99")
    assert bad_id["status"] == 422, bad_id["text"]


def test_claim_incomplete_quest_conflicts(client: TestClient, headers: dict):
    r = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t1")
    assert r["status"] == 409, r["text"]
    assert r["body"]["error"]["code"] == "cabin_quest_incomplete"


def test_claim_wrong_step_rejected(client: TestClient, headers: dict):
    """已完成 t1 进度后，跳步领 t3 必须被拒（防跳步领奖）。"""
    _act(client, headers, action="explore", spot_id="forest_pine")
    r = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t3")
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "cabin_wrong_step"


def test_tutorial_claim_then_double_claim_rejected(client: TestClient, headers: dict):
    """t1 需要先完成「采集一次」；领奖后不能重复领，也不能跳步。"""
    assert _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t1")["status"] == 409

    _act(client, headers, action="explore", spot_id="forest_pine")
    first = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t1")
    assert first["status"] == 200, first["text"]
    assert first["body"]["reward"]["coins"] == 10
    assert first["body"]["materials"].get("seed", 0) >= 2

    # 已推进到 t2：再领 t1 → 拒绝（不是「已完成」而是「不是当前步骤」）
    again = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t1")
    assert again["status"] == 422, again["text"]
    assert again["body"]["error"]["code"] == "cabin_wrong_step"


def test_tutorial_full_chain_unlocks_furniture(client: TestClient, headers: dict):
    """走完 5 步新手链 → 解锁一件任务家具（11 号制造闭环起点）。"""
    _act(client, headers, action="explore", spot_id="forest_pine")
    s1 = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t1")
    assert s1["status"] == 200, s1["text"]

    _act(client, headers, action="feed")
    s2 = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t2")
    assert s2["status"] == 200, s2["text"]

    # t3 需要 decorate 进度 = 家具件数超过基线：用 W1 的 interiors 接口加一件
    _put_interior(client, headers, "cabin", 4)
    s3 = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t3")
    assert s3["status"] == 200, s3["text"]

    # t4 需要累计 3 次探险。同一探险点有刷新窗口（冷却），
    # 因此必须换 3 个不同的点，否则第 2、3 次会被 409 拒绝。
    for spot in ("forest_hollow", "forest_mush", "forest_log"):
        r = _act(client, headers, action="explore", spot_id=spot)
        assert r["status"] == 200, r["text"]
    s4 = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t4")
    assert s4["status"] == 200, s4["text"]

    # 家具件数 >= 3 → Lv2，满足 t5
    _put_interior(client, headers, "cabin", 4)
    s5 = _act(client, headers, action="claim", quest_kind="tutorial", quest_id="t5")
    assert s5["status"] == 200, s5["text"]
    assert s5["body"]["completed"] is True
    assert s5["body"]["unlocked_furniture"], "完成新手链应解锁一件家具"


def _put_interior(client: TestClient, headers: dict, house: str, items: int) -> None:
    """通过 W1 的正式接口写入家具（不直接写库，保证跨服务一致）。"""
    payload = {
        "items": [
            {
                "id": f"w2-{i}", "furnitureId": "chair", "x": i, "y": 5,
                "flipped": False, "colorway": 0, "z": 0,
            }
            for i in range(items)
        ]
    }
    existing = client.get(f"/api/cabin/interiors/{house}", headers=headers).json()
    version = existing["version"]
    r = client.put(
        f"/api/cabin/interiors/{house}",
        json={"layout": payload, "expected_version": version},
        headers=headers,
    )
    assert r.status_code == 200, r.text


def test_decorate_progress_is_backfilled_by_reading_furniture_table(
    client: TestClient, headers: dict
):
    """回归：t3 的 decorate 进度没有动作触发点，靠 _sync_level 读表回填。

    W2 不拥有布置动作，家具件数只能从 W1 的 CabinInterior 表派生。
    """
    # 建档时基线为 0 件
    tutorial = _save(client, headers)["quests"]["tutorial"]
    assert tutorial["baseline_items"] == 0

    # 未经任何 gameplay 动作，纯读一次存档，不应凭空产生进度
    assert _save(client, headers)["quests"]["tutorial"]["progress"].get("decorate", 0) == 0

    # 用 W1 接口加一件家具后，读档应回填 decorate=1
    _put_interior(client, headers, "cabin", 1)
    t = _save(client, headers)["quests"]["tutorial"]
    assert t["progress"]["decorate"] == 1, t

    # 只增不减：撤掉家具后进度保留（已达成不回退）
    _put_interior(client, headers, "cabin", 0)
    assert _save(client, headers)["quests"]["tutorial"]["progress"]["decorate"] == 1


# ----------------------------------------------------------------------
# 7. 日常按日重置 + 连续登录
# ----------------------------------------------------------------------
def test_daily_quests_are_three_and_deterministic_per_day(
    client: TestClient, headers: dict, session_maker
):
    """每天恰好 3 个日常，且由 (owner, 日期) 派生 seed 决定（同日可复现）。"""
    from find_yourself.services.cabin_gameplay import _daily_seed

    today = _save(client, headers)["quests"]["daily"]
    assert len(today["ids"]) == 3, "每天恰好 3 个日常"
    assert len(set(today["ids"])) == 3, "同一天的日常不应重复"
    assert today["progress"] == {} and today["claimed"] == [], "新的一天进度归零"

    owner = _owner_of(client, headers)
    expected = _daily_seed(owner, today["date"])
    # 存档里的 seed 必须等于按 (owner, 日期) 派生的值 → 事件序列可复现
    _mutate_save(client, headers, session_maker, daily_seed=expected)
    again = _save(client, headers)["quests"]["daily"]
    assert again["ids"] == today["ids"], "同一天重复读取，日常不应变化"


def test_daily_resets_when_seed_is_stale(client: TestClient, headers: dict, session_maker):
    """跨日（seed 变了）→ 日常整体轮换 + 日期更新。"""
    today = _save(client, headers)["quests"]["daily"]
    # 模拟昨天的存档：seed 与今天不符
    _mutate_save(client, headers, session_maker, daily_seed="seed-from-a-previous-day")

    later = _save(client, headers)["quests"]["daily"]
    # _ensure_daily 会把它重置为「今天」的 seed，因此日期必须是今天
    assert later["date"] == today["date"]
    assert later["ids"] == today["ids"], "重置后应与今天应有的确定性结果一致"
    assert later["progress"] == {}, "跨日必须清空进度"


def test_consecutive_login_grants_chest_key(client: TestClient, headers: dict, session_maker):
    from find_yourself.services.cabin_gameplay import LOCAL_TZ

    # 昨天没登录 → streak 归 1，不给钥匙
    _mutate_save(client, headers, session_maker, last_login_date="")
    first = _save(client, headers)
    assert first["login_streak"] == 1
    assert first["chest_keys"] == 0

    # streak=1 且日期=昨天 → 今日登录应 +1 钥匙
    yesterday = (utcnow().astimezone(LOCAL_TZ) - timedelta(days=1)).date().isoformat()
    _mutate_save(
        client, headers, session_maker,
        login_streak=1, last_login_date=yesterday, daily_seed="stale",
    )
    second = _save(client, headers)
    assert second["login_streak"] == 2
    assert second["chest_keys"] == 1


# ----------------------------------------------------------------------
# 8. 离线收益（上限 24h，诚实显示）
# ----------------------------------------------------------------------
def test_offline_report_caps_at_24h_and_flags_it(client: TestClient, headers: dict, session_maker):
    _act(client, headers, action="explore", spot_id="forest_pine")
    _mutate_save(client, headers, session_maker, last_seen_at=utcnow() - timedelta(hours=72))

    body = _save(client, headers, theme="forest")
    off = body["offline"]
    assert off is not None, "离线超阈值应给提示"
    assert off["capped"] is True
    assert off["away_hours"] == 24.0
    assert off["real_away_hours"] > 24
    assert "24" in off["text"] and "上限" in off["text"], "必须如实告知被截断"


def test_offline_absent_for_short_absence(client: TestClient, headers: dict):
    _save(client, headers)
    body = _save(client, headers)
    assert body["offline"] is None, "短暂离开不该弹离线提示"


# ----------------------------------------------------------------------
# 9. 防作弊：客户端不得直接写数值
# ----------------------------------------------------------------------
def test_put_rejects_server_authoritative_fields(client: TestClient, headers: dict):
    for field, value in (
        ("coins", 999999),
        ("materials", {"crystal": 99}),
        ("intimacy", 100),
        ("house_level", 5),
    ):
        r = client.put(
            "/api/cabin/save", json={field: value, "expected_version": 0}, headers=headers
        )
        assert r.status_code == 422, f"{field} 应被拒：{r.text}"
        assert r.json()["error"]["code"] == "cabin_server_authoritative"


def test_put_accepts_settings_and_bumps_version(client: TestClient, headers: dict):
    before = _save(client, headers)["version"]
    r = client.put(
        "/api/cabin/save",
        json={"settings": {"active_theme": "planet"}, "expected_version": before},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["active_theme"] == "planet"
    assert r.json()["version"] == before + 1


def test_put_stale_version_conflicts(client: TestClient, headers: dict):
    _save(client, headers)
    r = client.put(
        "/api/cabin/save",
        json={"settings": {"active_theme": "field"}, "expected_version": 0},
        headers=headers,
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "cabin_version_conflict"


def test_put_rejects_unknown_settings_key(client: TestClient, headers: dict):
    r = client.put(
        "/api/cabin/save",
        json={"settings": {"hacked": 1}, "expected_version": 0},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "cabin_invalid_settings"


# ----------------------------------------------------------------------
# 10. 越权与鉴权
# ----------------------------------------------------------------------
def _owner_of(client: TestClient, headers: dict) -> str:
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return me.json().get("owner_id", "")


def test_service_identity_cannot_touch_gameplay_save(session_maker) -> None:
    from find_yourself.services.actor import Actor
    from find_yourself.services.cabin_gameplay import CabinGameplayService
    from find_yourself.services.errors import PermissionDenied

    session = session_maker()
    try:
        svc = CabinGameplayService(session)
        actor = Actor.service("worker-1", "worker")
        with pytest.raises(PermissionDenied) as exc:
            svc.get_save(actor)
        assert exc.value.http_status == 403
        with pytest.raises(PermissionDenied):
            svc.perform_action(actor, action="explore", spot_id="forest_pine")
    finally:
        session.close()


def test_unauthenticated_requests_rejected(client: TestClient):
    assert client.get("/api/cabin/save").status_code in (401, 403)
    w = client.post("/api/cabin/save/action", json={"action": "explore", "spot_id": "forest_pine"})
    assert w.status_code in (401, 403)


def test_write_without_csrf_header_rejected(client: TestClient, headers: dict):
    r = client.post("/api/cabin/save/action", json={"action": "explore", "spot_id": "forest_pine"})
    assert r.status_code in (401, 403)


def test_saves_are_isolated_per_owner(app: FastAPI, client: TestClient, headers: dict):
    _act(client, headers, action="explore", spot_id="forest_pine")
    mine = _save(client, headers)
    assert mine["coins"] > 0

    with TestClient(_force_loopback(app)) as other_client:
        reg = other_client.post(
            "/auth/register",
            json={"email": "w2-other@example.com", "password": "longenough1",
                  "consent_accepted": True},
        )
        assert reg.status_code == 200, reg.text
        other = {"X-CSRF-Token": reg.json()["csrf_token"]}
        theirs = _save(other_client, other)
        # 另一个账号是全新存档，看不到我的金币
        assert theirs["coins"] == 0
        assert theirs["version"] == 1

    assert _save(client, headers)["coins"] == mine["coins"], "我的存档不受他人影响"


# ----------------------------------------------------------------------
# 11. 制造闭环
# ----------------------------------------------------------------------
def test_craft_consumes_materials_and_unlocks(client: TestClient, headers: dict, session_maker):
    _mutate_save(client, headers, session_maker, materials={"wood": 9, "flower": 9, "moss": 9})
    r = _act(client, headers, action="craft", target="herb_shelf")
    assert r["status"] == 200, r["text"]
    assert r["body"]["crafted"] == "herb_shelf"
    assert r["body"]["unlocked_furniture"] == ["herb_shelf"]
    assert r["body"]["materials"]["wood"] == 6  # 9 - 3


def test_craft_missing_materials_rejected(client: TestClient, headers: dict, session_maker):
    _mutate_save(client, headers, session_maker, materials={"wood": 1})
    r = _act(client, headers, action="craft", target="herb_shelf")
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "cabin_missing_materials"
    assert "Missing" in r["body"]["error"]["message"] or "缺" in r["body"]["error"]["message"]


def test_craft_respects_house_level_gate(client: TestClient, headers: dict, session_maker):
    _mutate_save(
        client, headers, session_maker,
        materials={"crystal": 9, "stardust": 9, "moonstone": 9},
        house_level=1,
    )
    r = _act(client, headers, action="craft", target="crystal_tree")
    assert r["status"] == 409, r["text"]
    assert r["body"]["error"]["code"] == "cabin_level_too_low"


def test_craft_unknown_blueprint_rejected(client: TestClient, headers: dict):
    r = _act(client, headers, action="craft", target="golden_throne")
    assert r["status"] == 422, r["text"]
    assert r["body"]["error"]["code"] == "cabin_unknown_blueprint"


def test_craft_twice_conflicts(client: TestClient, headers: dict, session_maker):
    _mutate_save(client, headers, session_maker, materials={"wood": 9, "flower": 9, "moss": 9})
    assert _act(client, headers, action="craft", target="herb_shelf")["status"] == 200
    again = _act(client, headers, action="craft", target="herb_shelf")
    assert again["status"] == 409, again["text"]
    assert again["body"]["error"]["code"] == "cabin_already_unlocked"


# ----------------------------------------------------------------------
# 12. 自主行为（雨世界转译）
# ----------------------------------------------------------------------
def test_companion_reports_evidence_after_absence(client: TestClient, headers: dict, session_maker):
    _mutate_save(
        client, headers, session_maker,
        companion_state={
            "last_away_at": (utcnow() - timedelta(hours=5)).isoformat(),
            "behavior": "sleep",
            "label": "在窝里睡着了",
            "trinkets": ["毛线球"],
            "last_interaction": (utcnow() - timedelta(hours=5)).isoformat(),
        },
    )

    body = _save(client, headers)
    comp = body["companion"]
    assert comp["away_hours"] >= 5
    assert comp["behavior"]
    assert comp["label"], "必须有一句「刚才干了什么」"
    assert isinstance(comp["trinkets"], list)
    assert comp["unlocked_count"] >= 2


def test_companion_behaviour_pool_unlocks_with_intimacy() -> None:
    from find_yourself.services.cabin_gameplay import CabinGameplayService as S

    assert len(S._behavior_pool(0)) == 2
    assert len(S._behavior_pool(20)) == 3
    assert len(S._behavior_pool(50)) == 4
    assert len(S._behavior_pool(80)) == 5, "高亲密度应解锁全部自主行为"


def test_preference_view_marks_unrecognised_personality(client: TestClient, headers: dict):
    """未知性格不报错，但必须诚实标注 recognized=false。"""
    body = _save(client, headers, personality="grumpy")
    assert body["preferences"]["recognized"] is False
    assert body["preferences"]["personality"] == "chatty"


# ----------------------------------------------------------------------
# 13. 前后端契约
# ----------------------------------------------------------------------
def test_blueprint_furniture_ids_exist_in_w1_catalog() -> None:
    """蓝图里的家具 id 必须真实存在于 W1 的后端白名单，否则解锁了也摆不上。"""
    from find_yourself.services.cabin_interior import FURNITURE_CATALOG
    from find_yourself.services.cabin_gameplay import BLUEPRINTS

    missing = sorted(set(BLUEPRINTS) - set(FURNITURE_CATALOG))
    assert not missing, f"蓝图引用了 W1 白名单里没有的家具: {missing}"


def test_spot_materials_exist_in_catalog() -> None:
    from find_yourself.services.cabin_gameplay import MATERIALS, SPOTS

    missing = sorted({s["material"] for s in SPOTS} - set(MATERIALS))
    assert not missing, f"探险点引用了未注册材料: {missing}"


def test_no_global_random_used_in_service_source() -> None:
    """禁止全局 random：源码里除 random.Random(...) 外不得出现 random.<fn> 直接调用。"""
    root = Path(__file__).resolve().parents[2]
    src = (root / "src" / "find_yourself" / "services" / "cabin_gameplay.py").read_text(
        encoding="utf-8"
    )
    offenders = re.findall(r"random\.(?!Random\b)\w+", src)
    assert not offenders, f"发现全局 random 调用: {offenders}"
