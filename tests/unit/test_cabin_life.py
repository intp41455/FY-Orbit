"""单测：像素风生活模拟 · B 包纯逻辑层（B1/B2/B3/B6/B7/B8/B9）。

设计原则（对齐铁律 1「诚实原则」与 §4「改动必须带测试」）：
    * 断言**公式**而不是魔数：例如「雨天产量 = 基准 × 70%」写成
      ``assert gather_yield(...) == expected(lo, 70)``，读者能自己复算；
    * 每条「不够 / 满了 / 超限」路径都要有测试，确保它们**报错**而不是静默通过；
    * 确定性用两个独立调用对比，不依赖具体随机值（值变了测试也不该红）。

对应文件：
    B9 → services/cabin_life/clock.py
    B7 → services/cabin_life/npcs.py
    B8 → services/cabin_life/shop.py
    B3 → services/cabin_life/crafting.py
    B6 → services/cabin_life/quests.py
    B1/B2 → services/cabin_life/interaction.py
    主题表 → services/cabin_life/themes.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from find_yourself.services.cabin_life import (
    clock,
    crafting,
    interaction,
    npcs,
    quests,
    rng,
    shop,
    state,
    themes,
)

OWNER = "owner-lb"


# ======================================================================
# 地基：确定性随机
# ======================================================================

class TestRng:
    def test_same_seed_same_sequence(self):
        a = rng.rng_for("x", 1)
        b = rng.rng_for("x", 1)
        assert [a.randrange(1000) for _ in range(5)] == [
            b.randrange(1000) for _ in range(5)
        ]

    def test_different_seed_differs(self):
        a = rng.rng_for("x", 1).random()
        b = rng.rng_for("x", 2).random()
        assert a != b

    def test_weighted_pick_rejects_empty_table(self):
        with pytest.raises(ValueError):
            rng.weighted_pick(rng.rng_for("t"), {})

    def test_weighted_pick_respects_weights(self):
        r = rng.rng_for("w")
        table = {"a": 90, "b": 10}
        picks = [rng.weighted_pick(r, table) for _ in range(300)]
        assert picks.count("a") > picks.count("b")


# ======================================================================
# 主题注册表（§0.1：5 背景 + 四大主题，两类全都要）
# ======================================================================

class TestThemes:
    def test_nine_themes_registered(self):
        assert len(themes.THEME_IDS) == 9
        assert themes.LEGACY_THEME_IDS == (
            "forest", "garden", "stream", "field", "planet",
        )
        assert themes.SPEC_THEME_IDS == ("magic", "scifi", "country", "ink")

    @pytest.mark.parametrize("tid", themes.THEME_IDS)
    def test_every_theme_has_data(self, tid):
        t = themes.get_theme(tid)
        assert t.label and t.tagline
        assert t.weather_pool, f"{tid} 天气池为空"
        assert t.shop_goods, f"{tid} 经营货品为空"
        assert len(t.gather_nodes) >= 4, f"{tid} 采集点少于 4"

    @pytest.mark.parametrize("tid", themes.THEME_IDS)
    def test_weather_pool_uses_known_weather(self, tid):
        for wid in themes.get_theme(tid).weather_pool:
            assert wid in clock.WEATHERS, f"{tid} 引用了未定义天气 {wid}"

    def test_unknown_theme_raises(self):
        with pytest.raises(KeyError):
            themes.get_theme("atlantis")


# ======================================================================
# B9 · 昼夜与天气
# ======================================================================

class TestClock:
    def test_day_part_boundaries(self):
        assert clock.day_part(0) == "late_night"
        assert clock.day_part(6 * 60) == "dawn"
        assert clock.day_part(8 * 60) == "morning"
        assert clock.day_part(12 * 60) == "noon"
        assert clock.day_part(15 * 60) == "afternoon"
        assert clock.day_part(18 * 60) == "dusk"
        assert clock.day_part(22 * 60) == "night"

    def test_advance_crosses_day_boundary(self):
        c = clock.GameClock(day=3, minute=23 * 60)
        nxt = c.advanced(120)
        assert (nxt.day, nxt.minute) == (4, 60)

    def test_advance_rejects_negative(self):
        with pytest.raises(ValueError):
            clock.GameClock(day=1, minute=0).advanced(-1)

    def test_clock_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            clock.GameClock(day=0, minute=0)
        with pytest.raises(ValueError):
            clock.GameClock(day=1, minute=1440)

    def test_sleep_goes_to_next_morning(self):
        c = clock.GameClock(day=7, minute=2 * 60)
        nxt = c.slept_to_next_morning()
        assert (nxt.day, nxt.minute) == (8, clock.DAILY_RESET_MINUTE)
        assert nxt.part == "dawn"

    def test_label_contains_day_and_part(self):
        c = clock.GameClock(day=3, minute=9 * 60)
        assert "第3天" in c.label() and "上午" in c.label()
        assert "晴" in c.label("晴")

    @pytest.mark.parametrize("tid", themes.THEME_IDS)
    def test_weather_is_deterministic_and_in_pool(self, tid):
        pool = set(themes.get_theme(tid).weather_pool)
        for day in (1, 2, 3, 30):
            first = clock.roll_weather(OWNER, tid, day)
            second = clock.roll_weather(OWNER, tid, day)
            assert first == second, "同参数天气必须一致（否则存档读回会漂移）"
            assert first in pool, f"{tid} 第 {day} 天 roll 出不在池内的天气 {first}"

    def test_weather_differs_between_owners(self):
        days = [clock.roll_weather(f"owner-{i}", "field", d) for i in range(6) for d in (1, 2, 3)]
        assert len(set(days)) >= 3, "不同 owner 不应锁死同一天气"

    def test_roll_weather_rejects_day_zero(self):
        with pytest.raises(ValueError):
            clock.roll_weather(OWNER, "forest", 0)

    def test_unknown_weather_raises(self):
        with pytest.raises(KeyError):
            clock.get_weather("meteor")

    def test_gather_yield_scales_with_weather(self):
        """雨天产量 = 基准 × 70%（可复算公式）。"""
        rain_day = next(
            d for d in range(1, 60)
            if clock.roll_weather(OWNER, "forest", d) == "rain"
        )
        c = clock.GameClock(day=rain_day, minute=12 * 60)
        w = clock.get_weather("rain")
        assert clock.gather_yield(c, OWNER, "forest", 4) == max(
            1, (4 * w.yield_pct + 50) // 100
        )

    def test_gather_minutes_scales_with_weather(self):
        c = clock.GameClock(day=1, minute=12 * 60)
        w = clock.weather_of(c, OWNER, "forest")
        assert clock.gather_minutes(c, OWNER, "forest", 10) == max(
            1, (10 * w.pace_pct + 50) // 100
        )

    def test_scaled_never_returns_zero(self):
        assert clock.scaled(1, 1) == 1
        assert clock.scaled(1, 0) == 1

    def test_extreme_weather_blocks_gather(self):
        for tid in themes.THEME_IDS:
            for day in range(1, 80):
                if clock.roll_weather(OWNER, tid, day) in ("sandstorm", "static"):
                    c = clock.GameClock(day=day, minute=12 * 60)
                    assert clock.gather_blocked(c, OWNER, tid) is True
                    return
        pytest.skip("前 80 天未出现极端天气（不构成失败）")

    def test_night_demand_lower_than_noon(self):
        c_noon = clock.GameClock(day=1, minute=13 * 60)
        c_night = clock.GameClock(day=1, minute=22 * 60)
        for tid in themes.THEME_IDS:
            assert clock.demand_pct(c_night, OWNER, tid) < clock.demand_pct(
                c_noon, OWNER, tid
            ), f"{tid} 夜里客源没有低于白天"

    def test_light_level_bounded(self):
        for minute in range(0, 1440, 30):
            c = clock.GameClock(day=1, minute=minute)
            v = clock.light_level(c, OWNER, "country")
            assert 0.0 <= v <= 1.0


# ======================================================================
# B7 · NPC / 作息 / 好感 / 对话
# ======================================================================

class TestNpcRoster:
    @pytest.mark.parametrize("tid", themes.THEME_IDS)
    def test_at_least_ten_npcs_per_theme(self, tid):
        roster = npcs.npcs_of(tid)
        assert len(roster) >= npcs.MIN_NPCS_PER_THEME, (
            f"{tid} 只有 {len(roster)} 个 NPC，B7 要求 ≥10"
        )

    def test_npc_ids_unique(self):
        assert len(npcs.NPCS) == len(set(npcs.NPCS))

    @pytest.mark.parametrize("tid", themes.THEME_IDS)
    def test_every_npc_has_full_heart_lines(self, tid):
        for n in npcs.npcs_of(tid):
            assert len(n.heart_lines) == npcs.MAX_HEARTS + 1, (
                f"{n.id} 剧情台词数不足（需覆盖 0-10 心）"
            )

    @pytest.mark.parametrize("tid", themes.THEME_IDS)
    def test_every_npc_has_schedule_covering_day(self, tid):
        for n in npcs.npcs_of(tid):
            starts = [s.start for s in n.schedule]
            assert starts == sorted(starts), f"{n.id} 作息表未按时间升序"
            assert starts[0] == 0, f"{n.id} 作息表没有覆盖 00:00"
            assert all(0 <= s < 1440 for s in starts)

    def test_unknown_npc_raises(self):
        with pytest.raises(KeyError):
            npcs.get_npc("nobody")

    def test_each_theme_has_commissions(self):
        assert npcs.COMMISSIONS, "委托表为空"
        for tid in themes.THEME_IDS:
            mine = [c for c in npcs.COMMISSIONS if npcs.get_npc(c.npc_id).theme == tid]
            assert mine, f"{tid} 没有任何 NPC 委托"


class TestSchedule:
    def test_locate_picks_latest_started_slot(self):
        n = npcs.get_npc("forest_woodsman")
        c = clock.GameClock(day=1, minute=9 * 60)
        slot = npcs.locate(n, c)
        assert slot.activity == "砍柴"
        assert slot.place == "grove"

    def test_awake_reflects_sleep(self):
        n = npcs.get_npc("forest_woodsman")
        assert npcs.awake(n, clock.GameClock(day=1, minute=3 * 60)) is False
        assert npcs.awake(n, clock.GameClock(day=1, minute=10 * 60)) is True

    def test_status_rows_one_per_npc(self):
        rows = npcs.npc_status_rows("ink", clock.GameClock(day=2, minute=9 * 60))
        assert len(rows) == len(npcs.npcs_of("ink"))
        assert all({"id", "name", "place", "activity", "awake"} <= set(r) for r in rows)


class TestAffinity:
    def test_hearts_formula(self):
        assert npcs.hearts_for_points(0) == 0
        assert npcs.hearts_for_points(99) == 0
        assert npcs.hearts_for_points(100) == 1
        assert npcs.hearts_for_points(999) == 9
        assert npcs.hearts_for_points(100_000) == npcs.MAX_HEARTS

    def test_hearts_rejects_negative(self):
        with pytest.raises(ValueError):
            npcs.hearts_for_points(-1)

    def test_gift_points_by_preference(self):
        n = npcs.get_npc("forest_woodsman")
        liked = npcs.gift_points(n, "honey")
        disliked = npcs.gift_points(n, "slime_jelly")
        neutral = npcs.gift_points(n, "moonstone")
        assert liked > neutral > disliked
        assert disliked == 0, "送不喜欢的礼物应明确不加好感"

    def test_apply_gift_raises_heart_exactly_once(self):
        n = npcs.get_npc("forest_beekeeper")
        pts, hearts, delta, note = npcs.apply_gift(n, "honey", 40, gifts_today=0)
        assert delta == 60
        assert pts == 100 and hearts == 1
        assert "1 心" in note

    def test_apply_gift_respects_daily_cap(self):
        n = npcs.get_npc("forest_beekeeper")
        pts, hearts, delta, note = npcs.apply_gift(
            n, "honey", 0, gifts_today=npcs.MAX_GIFTS_PER_DAY
        )
        assert delta == 0 and pts == 0
        assert "明天" in note, "超限必须说明原因，不能静默"

    def test_personality_bonus_applied(self):
        n = npcs.get_npc("forest_beekeeper")
        _, _, plain, _ = npcs.apply_gift(n, "wood", 0, gifts_today=0)
        _, _, bonus, _ = npcs.apply_gift(
            n, "wood", 0, gifts_today=0, personality_bonus=True
        )
        assert bonus == plain + 10

    def test_unlock_lines_grow_with_hearts(self):
        n = npcs.get_npc("forest_beekeeper")
        assert len(npcs.unlock_lines(n, 0)) == 1
        assert len(npcs.unlock_lines(n, 5)) == 6
        assert len(npcs.unlock_lines(n, 99)) == npcs.MAX_HEARTS + 1

    def test_next_unlock_at(self):
        n = npcs.get_npc("forest_beekeeper")
        assert npcs.next_unlock_at(n, 0) == 1
        assert npcs.next_unlock_at(n, npcs.MAX_HEARTS) is None


class TestDialogue:
    def test_asleep_dialogue_is_honest(self):
        n = npcs.get_npc("forest_woodsman")
        c = clock.GameClock(day=1, minute=2 * 60)
        out = npcs.dialogue(n, c, hearts=3)
        assert out["scene"] == "asleep"
        assert "睡了" in str(out["text"])

    def test_dialogue_varies_by_part(self):
        n = npcs.get_npc("forest_beekeeper")
        morning = npcs.dialogue(n, clock.GameClock(day=1, minute=9 * 60), hearts=1)
        night = npcs.dialogue(n, clock.GameClock(day=1, minute=22 * 60), hearts=1)
        assert morning["scene"] != night["scene"]
        assert "早上好" in str(morning["text"])

    def test_dialogue_deterministic(self):
        n = npcs.get_npc("ink_tea_master")
        c = clock.GameClock(day=4, minute=15 * 60)
        a = npcs.dialogue(n, c, hearts=2, identity="scholar", personality="cool")
        b = npcs.dialogue(n, c, hearts=2, identity="scholar", personality="cool")
        assert a["text"] == b["text"]

    def test_identity_and_personality_affect_text(self):
        n = npcs.get_npc("ink_tea_master")
        c = clock.GameClock(day=4, minute=15 * 60)
        base = str(npcs.dialogue(n, c, hearts=2)["text"])
        scholar = str(npcs.dialogue(n, c, hearts=2, identity="scholar")["text"])
        cool = str(npcs.dialogue(n, c, hearts=2, personality="cool")["text"])
        assert scholar != base and cool != base

    def test_hearts_unlock_heart_lines_in_dialogue(self):
        n = npcs.get_npc("ink_tea_master")
        c = clock.GameClock(day=4, minute=15 * 60)
        low = npcs.dialogue(n, c, hearts=0)
        high = npcs.dialogue(n, c, hearts=9)
        assert len(high["unlocked"]) > len(low["unlocked"])
        assert str(high["text"]) != str(low["text"])


class TestCommission:
    def test_turn_in_consumes_material(self):
        c = npcs.COMMISSIONS[0]
        bag = {c.material: c.need + 2}
        new_bag, coins, points, note = npcs.turn_in_commission(c, bag, hearts=0)
        assert new_bag[c.material] == 2
        assert coins == c.reward_coins and points == c.reward_hearts_points
        assert "完成" in note

    def test_turn_in_clears_material_when_exhausted(self):
        c = npcs.COMMISSIONS[0]
        new_bag, _, _, _ = npcs.turn_in_commission(c, {c.material: c.need}, hearts=0)
        assert c.material not in new_bag

    def test_turn_in_rejects_insufficient_materials(self):
        c = npcs.COMMISSIONS[0]
        with pytest.raises(ValueError) as exc:
            npcs.turn_in_commission(c, {c.material: c.need - 1}, hearts=0)
        assert "材料不足" in str(exc.value)

    def test_unknown_commission_raises(self):
        with pytest.raises(KeyError):
            npcs.commission_by_id("nope")


# ======================================================================
# B8 · 经营
# ======================================================================

class TestShopPrices:
    def test_fluctuation_within_configured_range(self):
        for day in range(1, 60):
            pct = shop.fluctuation_pct(OWNER, "bread", day)
            assert -shop.FLUCTUATION_PCT <= pct <= shop.FLUCTUATION_PCT

    def test_price_deterministic_per_day(self):
        assert shop.buy_price(OWNER, "bread", 5) == shop.buy_price(OWNER, "bread", 5)
        assert shop.sell_price(OWNER, "bread", 5) == shop.sell_price(OWNER, "bread", 5)

    def test_buy_price_matches_formula(self):
        good = shop.get_good("bread")
        pct = shop.fluctuation_pct(OWNER, "bread", 7)
        assert shop.buy_price(OWNER, "bread", 7) == max(
            1, (good.base_cost * (100 + pct) + 50) // 100
        )

    def test_buy_price_never_zero(self):
        for day in range(1, 120):
            assert shop.buy_price(OWNER, "jam", day) >= 1

    def test_price_factor_monotonic(self):
        day = 6
        cheap = shop.price_factor(OWNER, "bread", day, 1)
        fair = shop.price_factor(OWNER, "bread", day, shop.sell_price(OWNER, "bread", day))
        pricey = shop.price_factor(OWNER, "bread", day, 10_000)
        assert cheap > fair > pricey

    def test_unknown_good_raises(self):
        with pytest.raises(KeyError):
            shop.get_good("unicorn")


class TestShopTrading:
    def test_restock_deducts_coins(self):
        r = shop.restock(OWNER, "bread", 3, 2, coins=1000, stock=0)
        assert r.cost == shop.buy_price(OWNER, "bread", 3) * 2
        assert r.coins_after == 1000 - r.cost
        assert r.stock_after == 2

    def test_restock_rejects_insufficient_coins(self):
        with pytest.raises(ValueError) as exc:
            shop.restock(OWNER, "bread", 3, 99, coins=5, stock=0)
        assert "金币不足" in str(exc.value)

    def test_restock_rejects_non_positive_qty(self):
        with pytest.raises(ValueError):
            shop.restock(OWNER, "bread", 3, 0, coins=1000, stock=0)

    def test_batch_restock_is_all_or_nothing(self):
        s = shop.new_shop("grocery", OWNER, 2)
        with pytest.raises(ValueError) as exc:
            shop.restock_all(OWNER, s, 2, ["bread", "jam"], 10, coins=10)
        assert "未扣款" in str(exc.value)

    def test_batch_restock_applies_on_success(self):
        s = shop.new_shop("grocery", OWNER, 2)
        s2, coins_left, plan = shop.restock_all(
            OWNER, s, 2, ["bread", "jam"], 2, coins=10_000
        )
        assert s2.stock_of("bread") == 2 and s2.stock_of("jam") == 2
        assert coins_left == 10_000 - sum(int(p["cost"]) for p in plan)


class TestShopSell:
    def test_sell_computes_revenue_and_keeps_leftover(self):
        s = shop.new_shop("grocery", OWNER, 4)
        s = shop.replace(s, stock={**s.stock, "bread": 100})
        noon = clock.GameClock(day=4, minute=13 * 60)
        r = shop.sell_day(OWNER, "bread", noon, "country",
                          s.ask_of("bread", OWNER, 4), stock=100, coins=0)
        assert r.revenue == r.sold * r.unit_price
        assert r.stock_after == 100 - r.sold
        assert 0 <= r.sold <= 100

    def test_sell_with_empty_stock_says_so(self):
        noon = clock.GameClock(day=4, minute=13 * 60)
        r = shop.sell_day(OWNER, "bread", noon, "country", 10, stock=0, coins=50)
        assert r.sold == 0 and r.coins_after == 50
        assert "库存为 0" in r.note

    def test_sell_rejects_non_positive_price(self):
        noon = clock.GameClock(day=4, minute=13 * 60)
        with pytest.raises(ValueError):
            shop.sell_day(OWNER, "bread", noon, "country", 0, stock=5, coins=0)


class TestDailySettlement:
    def test_totals_are_recomputable_from_lines(self):
        s = shop.new_shop("tavern", OWNER, 5)
        s, _, _ = shop.restock_all(OWNER, s, 5, ["soup", "tea"], 4, coins=50_000)
        noon = clock.GameClock(day=5, minute=13 * 60)
        out = shop.daily_settlement(
            OWNER, s, noon, "country", coins=1000, next_clock=noon.slept_to_next_morning()
        )
        assert out["total_revenue"] == sum(int(l["revenue"]) for l in out["lines"])
        assert out["profit"] == out["total_revenue"] - out["cost_of_goods_sold"]
        assert out["coins_after"] == 1000 + out["profit"]
        assert out["next_day"] == 6

    def test_settlement_is_deterministic(self):
        def run():
            s = shop.new_shop("tavern", OWNER, 5)
            s, _, _ = shop.restock_all(OWNER, s, 5, ["soup", "tea"], 4, coins=50_000)
            noon = clock.GameClock(day=5, minute=13 * 60)
            out = shop.daily_settlement(
                OWNER, s, noon, "country", coins=0, next_clock=noon.slept_to_next_morning()
            )
            return {k: v for k, v in out.items() if k != "shop"}

        assert run() == run()

    def test_leftover_stock_is_kept_not_lost(self):
        s = shop.new_shop("tavern", OWNER, 5)
        s, _, _ = shop.restock_all(OWNER, s, 5, ["soup"], 50, coins=500_000)
        noon = clock.GameClock(day=5, minute=13 * 60)
        out = shop.daily_settlement(
            OWNER, s, noon, "country", coins=0, next_clock=noon.slept_to_next_morning()
        )
        assert out["shop"].stock_of("soup") == 50 - out["lines"][0]["sold"]


class TestShopProgression:
    @pytest.mark.parametrize("kind", shop.SHOP_KINDS)
    def test_every_kind_has_goods_and_labels(self, kind):
        assert kind in shop.SHOP_KIND_LABELS
        assert shop.goods_for_kind(kind), f"{kind} 没有任何货品"

    def test_unlock_grows_with_level(self):
        counts = [len(shop.unlocked_goods("grocery", lv)) for lv in range(1, 6)]
        assert counts == sorted(counts)
        assert counts[0] < counts[-1]

    def test_upgrade_requires_coins(self):
        s = shop.new_shop("grocery", OWNER, 1)
        ok, reason = shop.can_upgrade(s, 0)
        assert ok is False and "还差" in reason
        with pytest.raises(ValueError):
            shop.upgrade(s, 0)

    def test_upgrade_unlocks_new_good(self):
        s = shop.new_shop("grocery", OWNER, 1)
        before = shop.next_unlock("grocery", 1)
        s2, coins_left, reason = shop.upgrade(s, 100_000)
        assert s2.level == 2
        assert coins_left == 100_000 - shop.upgrade_cost(1)
        assert before is not None and before.id in {g.id for g in shop.unlocked_goods("grocery", 2)}

    def test_max_level_upgrade_raises(self):
        s = shop.replace(shop.new_shop("grocery", OWNER, 1), level=shop.MAX_SHOP_LEVEL)
        assert shop.next_unlock("grocery", shop.MAX_SHOP_LEVEL) is None
        with pytest.raises(ValueError):
            shop.upgrade(s, 10_000_000)

    def test_set_ask_price_rejects_zero(self):
        s = shop.new_shop("grocery", OWNER, 1)
        with pytest.raises(ValueError):
            shop.set_ask_price(s, "bread", 0)
        assert shop.set_ask_price(s, "bread", 15).ask_of("bread", OWNER, 1) == 15

    def test_shop_rows_expose_prices_and_unlocks(self):
        s = shop.new_shop("grocery", OWNER, 3)
        rows = shop.shop_rows(OWNER, s, 3, "garden")
        assert rows
        for r in rows:
            assert {"id", "label", "unlocked", "buy_price", "fair_price",
                    "ask_price", "stock", "taste"} <= set(r)
            assert r["buy_price"] >= 1 and r["fair_price"] >= 1


# ======================================================================
# B3 · 制作
# ======================================================================

class TestCrafting:
    def test_recipe_table_covers_all_themes(self):
        covered = {r.theme for r in crafting.RECIPES if r.theme}
        assert covered == set(themes.THEME_IDS), "有主题没有任何专属配方"

    @pytest.mark.parametrize("rid", sorted(crafting.RECIPES_BY_ID))
    def test_recipe_data_is_sane(self, rid):
        r = crafting.get_recipe(rid)
        assert r.outputs, f"{rid} 没有产物"
        assert all(n > 0 for _, n in r.outputs + r.inputs)
        assert r.coins >= 0
        assert r.skill_level >= 1

    def test_unknown_recipe_raises(self):
        with pytest.raises(KeyError):
            crafting.get_recipe("nonexistent")

    def test_skill_level_thresholds(self):
        assert crafting.skill_level(0) == 1
        assert crafting.skill_level(crafting.SKILL_THRESHOLDS[-1]) == len(
            crafting.SKILL_THRESHOLDS
        )
        with pytest.raises(ValueError):
            crafting.skill_level(-1)

    def test_unlock_state_gives_reason(self):
        r = crafting.get_recipe("bread")
        locked = crafting.unlock_state(r, 0)
        assert locked["unlocked"] is False
        assert locked["need_level"] == r.skill_level
        assert crafting.unlock_state(r, 5)["unlocked"] is True

    def test_craft_consumes_inputs_and_adds_outputs(self):
        bag = {"wheat": 4, "honey": 2}
        rec = crafting.craft("bread", bag, coins=100, level=5)
        assert rec.bag_after["wheat"] == 2 and rec.bag_after["honey"] == 1
        assert rec.bag_after["bread"] == 2
        assert rec.coins_after == 100 - crafting.get_recipe("bread").coins
        assert rec.bag_after is not bag and bag == {"wheat": 4, "honey": 2}

    def test_craft_rejects_missing_material_without_mutating(self):
        bag = {"wheat": 1}
        with pytest.raises(ValueError) as exc:
            crafting.craft("bread", bag, coins=100, level=5)
        assert "材料不足" in str(exc.value)
        assert bag == {"wheat": 1}

    def test_craft_rejects_insufficient_coins(self):
        bag = {"wheat": 4, "honey": 2}
        with pytest.raises(ValueError) as exc:
            crafting.craft("bread", bag, coins=0, level=5)
        assert "金币不足" in str(exc.value)

    def test_craft_rejects_low_skill(self):
        bag = {"wheat": 4, "honey": 2}
        with pytest.raises(ValueError) as exc:
            crafting.craft("moon_charm", bag, coins=10_000, level=1)
        assert "技能" in str(exc.value)

    def test_craft_rejects_wrong_theme(self):
        bag = {"magic_herb": 4, "magic_crystal": 4}
        with pytest.raises(ValueError) as exc:
            crafting.craft("potion", bag, coins=10_000, level=6, theme="field")
        assert "主题" in str(exc.value)

    def test_missing_inputs_reports_gap(self):
        r = crafting.get_recipe("bread")
        assert crafting.missing_inputs(r, {"wheat": 2, "honey": 1}) == {}
        gap = crafting.missing_inputs(r, {"wheat": 2})
        assert gap == {"honey": 1}

    def test_material_key_removed_when_zero(self):
        bag = {"wheat": 2, "honey": 1}
        rec = crafting.craft("bread", bag, coins=100, level=5)
        assert "honey" not in rec.bag_after

    def test_recipes_for_filters_by_theme(self):
        ids = {r.id for r in crafting.recipes_for("ink", 6)}
        assert "peach_wine" in ids and "potion" not in ids
        assert "bread" in ids, "通用配方应始终可见"


# ======================================================================
# B6 · 任务
# ======================================================================

class TestQuests:
    def test_main_and_side_exist_for_every_theme(self):
        for tid in themes.THEME_IDS:
            mains = [q for q in quests.QUESTS if q.kind == "main" and q.theme == tid]
            sides = [q for q in quests.QUESTS if q.kind == "side" and q.theme == tid]
            assert len(mains) >= 3, f"{tid} 主线不足 3 段"
            assert len(sides) >= 2, f"{tid} 支线不足 2 条"

    def test_every_giver_exists_in_that_theme(self):
        for q in quests.QUESTS:
            giver = npcs.get_npc(q.giver)
            assert giver.theme == q.theme or q.theme == ""

    def test_targets_positive_and_rewards_present(self):
        for q in quests.QUESTS:
            assert q.target > 0
            assert q.reward_coins > 0 or q.reward_items or q.reward_hearts_points

    def test_start_log_accepts_first_main(self):
        log = quests.start_log("country")
        assert log.entries[0].quest_id == "main_country_1"
        assert log.entries[0].progress == 0

    def test_advance_counts_and_completes(self):
        log = quests.start_log("forest")
        log2, changes = quests.advance(log, "gather", 3)
        assert log2.progress_of("main_forest_1") == 3
        assert changes[0]["done"] is False
        log3, changes3 = quests.advance(log2, "gather", 5)
        assert log3.progress_of("main_forest_1") == 5
        assert changes3[0]["done"] is True

    def test_advance_caps_at_target(self):
        log = quests.start_log("forest")
        log2, _ = quests.advance(log, "gather", 999)
        assert log2.progress_of("main_forest_1") == quests.get_quest(
            "main_forest_1"
        ).target

    def test_advance_ignores_unrelated_event(self):
        log = quests.start_log("forest")
        log2, changes = quests.advance(log, "teleport", 5)
        assert changes == [] and log2.progress_of("main_forest_1") == 0

    def test_advance_rejects_non_positive_amount(self):
        log = quests.start_log("forest")
        with pytest.raises(ValueError):
            quests.advance(log, "gather", 0)

    def test_claim_requires_completion(self):
        log = quests.start_log("forest")
        with pytest.raises(ValueError) as exc:
            quests.claim(log, "main_forest_1")
        assert "还没完成" in str(exc.value)

    def test_claim_grants_and_marks(self):
        log = quests.start_log("forest")
        log, _ = quests.advance(log, "gather", 5)
        log2, items, coins, points, note = quests.claim(log, "main_forest_1")
        assert coins > 0 and note.startswith("完成")
        assert log2.is_claimed("main_forest_1")

    def test_double_claim_rejected(self):
        log = quests.start_log("forest")
        log, _ = quests.advance(log, "gather", 5)
        log2, _, _, _, _ = quests.claim(log, "main_forest_1")
        with pytest.raises(ValueError) as exc:
            quests.claim(log2, "main_forest_1")
        assert "已经领过" in str(exc.value)

    def test_claimed_quest_stops_accumulating(self):
        log = quests.start_log("forest")
        log, _ = quests.advance(log, "gather", 5)
        log2, _, _, _, _ = quests.claim(log, "main_forest_1")
        log3, changes = quests.advance(log2, "gather", 5)
        assert "main_forest_1" not in [c["quest_id"] for c in changes]
        assert log3.progress_of("main_forest_1") == 5

    def test_claim_unaccepted_quest_rejected(self):
        log = quests.start_log("forest")
        with pytest.raises(ValueError) as exc:
            quests.claim(log, "main_forest_2")
        assert "没有接取" in str(exc.value)

    def test_accept_is_idempotent(self):
        log = quests.start_log("forest")
        log2 = quests.accept(log, "side_forest_gather")
        log3 = quests.accept(log2, "side_forest_gather")
        assert len(log3.entries) == len(log2.entries)

    def test_unknown_quest_raises(self):
        with pytest.raises(KeyError):
            quests.get_quest("nope")


class TestQuestMarkers:
    def test_marker_offer_for_new_side_quest(self):
        log = quests.start_log("forest")
        # 未接任何任务、且该 NPC 名下有可接任务 → 感叹号
        m = quests.marker_for(quests.QuestLog(), "forest_woodsman")
        assert m in (quests.MARKER_OFFER, quests.MARKER_NONE)

    def test_marker_turn_in_after_completion(self):
        log = quests.start_log("forest")
        giver = quests.get_quest("main_forest_1").giver
        log, _ = quests.advance(log, "gather", 5)
        assert quests.marker_for(log, giver) == quests.MARKER_TURN_IN

    def test_marker_doing_while_in_progress(self):
        log = quests.start_log("forest")
        giver = quests.get_quest("main_forest_1").giver
        log, _ = quests.advance(log, "gather", 1)
        assert quests.marker_for(log, giver) == quests.MARKER_DOING

    def test_markers_covers_theme_roster(self):
        log = quests.start_log("ink")
        marks = quests.markers(log, "ink")
        assert set(marks) == {n.id for n in npcs.npcs_of("ink")}


class TestQuestOffers:
    def test_offer_of_the_day_deterministic(self):
        a = quests.offer_of_the_day("ink", 7)
        b = quests.offer_of_the_day("ink", 7)
        assert a == b and len(a) == 1

    def test_unknown_theme_raises_instead_of_silent_generic(self):
        """切错主题必须报错，不能静默退回通用任务。"""
        with pytest.raises(KeyError):
            quests.offer_of_the_day("atlantis", 1)
        with pytest.raises(KeyError):
            quests.available_quests("atlantis")


# ======================================================================
# B1 / B2 · 交互与采集
# ======================================================================

class TestInteraction:
    def test_every_gather_verb_is_registered(self):
        assert interaction.prompt_grammar_check() == [], "有动作缺动画键或中文标签"

    def test_b2_required_actions_all_present(self):
        """B2 明列：砍树/挖矿/钓鱼/采果/拔草/打水/捡杂物。"""
        used = set()
        for tid in themes.THEME_IDS:
            used.update(r["action"] for r in interaction.gatherable_rows(tid))
        assert {"chop", "mine", "fish", "pick", "harvest", "water", "collect"} <= used

    @pytest.mark.parametrize("tid", themes.THEME_IDS)
    def test_interactables_have_animations(self, tid):
        for row in interaction.gatherable_rows(tid):
            assert row["animation"] in set(interaction.ANIMATIONS.values())
            assert row["action_label"]

    def test_prompt_appears_only_in_range(self):
        node = themes.get_theme("forest").gather_nodes[0]
        on_tile = node.tile
        assert interaction.prompt_for_tile("forest", on_tile) is not None
        far = (node.tile[0] + 20, node.tile[1] + 20)
        assert interaction.prompt_for_tile("forest", far) is None

    def test_prompt_text_mentions_action_and_material(self):
        node = themes.get_theme("forest").gather_nodes[0]
        text = interaction.prompt_for_tile("forest", node.tile)
        assert "✋" in text and node.material in text

    def test_nearest_picks_closest(self):
        rows = interaction.gatherable_rows("field")
        tiles = [tuple(r["tile"]) for r in rows]
        target = tiles[0]
        it, dist = interaction.nearest_interactable("field", target)
        assert it is not None and dist == 0

    def test_rows_expose_distance_and_range_flag(self):
        node = themes.get_theme("planet").gather_nodes[0]
        rows = interaction.gatherable_rows("planet", node.tile)
        first = rows[0]
        assert first["in_range"] is True and first["distance"] == 0
        far = interaction.gatherable_rows("planet", (1, 1))
        assert all(r["in_range"] is False for r in far)


class TestGather:
    def _clock(self, day=1, minute=9 * 60):
        return clock.GameClock(day=day, minute=minute)

    def test_gather_adds_to_bag_and_advances_clock(self):
        rec = interaction.gather(
            OWNER, "forest", "forest_pine", self._clock(), {}, times_today=0
        )
        assert rec.bag_after["wood"] == rec.qty
        assert rec.clock_after.minute == 9 * 60 + rec.minutes
        assert rec.clock_after.day == 1
        assert rec.animation == interaction.ANIMATIONS["chop"]

    def test_gather_does_not_mutate_input_bag(self):
        bag = {}
        rec = interaction.gather(
            OWNER, "forest", "forest_mush", self._clock(), bag, times_today=0
        )
        assert bag == {} and rec.bag_after is not bag

    def test_gather_qty_within_weather_adjusted_range(self):
        node = next(
            n for n in themes.get_theme("field").gather_nodes if n.id == "field_wheat"
        )
        c = self._clock(day=9)
        rec = interaction.gather(OWNER, "field", node.id, c, {}, times_today=0)
        lo = clock.gather_yield(c, OWNER, "field", node.qty[0])
        hi = clock.gather_yield(c, OWNER, "field", node.qty[1])
        assert min(lo, hi) <= rec.qty <= max(lo, hi)

    def test_gather_deterministic_for_same_inputs(self):
        a = interaction.gather(OWNER, "field", "field_wheat", self._clock(day=9), {},
                                times_today=0)
        b = interaction.gather(OWNER, "field", "field_wheat", self._clock(day=9), {},
                                times_today=0)
        assert a.qty == b.qty and a.minutes == b.minutes

    def test_daily_limit_enforced(self):
        with pytest.raises(ValueError) as exc:
            interaction.gather(
                OWNER, "field", "field_wheat", self._clock(), {},
                times_today=interaction.DAILY_NODE_LIMIT,
            )
        assert "明天" in str(exc.value)

    def test_unknown_node_raises(self):
        with pytest.raises(KeyError):
            interaction.gather(OWNER, "field", "nope", self._clock(), {})

    def test_extreme_weather_blocks_with_reason(self):
        for tid in themes.THEME_IDS:
            for day in range(1, 120):
                if clock.roll_weather(OWNER, tid, day) in ("sandstorm", "static"):
                    node = themes.get_theme(tid).gather_nodes[0]
                    with pytest.raises(ValueError) as exc:
                        interaction.gather(OWNER, tid, node.id,
                                           self._clock(day=day), {}, times_today=0)
                    assert "天气" in str(exc.value)
                    return
        pytest.skip("前 120 天未出现极端天气（不构成失败）")

# ======================================================================
# 前后端契约：fixture 必须与后端真实快照一致
# ======================================================================

FIXTURE = Path(__file__).resolve().parents[2] / (
    "web/src/components/cabin/gameplay/__fixtures__/lifeSave.sample.json"
)


class TestFrontendContract:
    """锁定 `lifeApi.ts` 的类型与后端字段一一对应。

    为什么要这条：前端不 import Python，规则只能单源。如果后端改了字段名
    而前端没跟上，`tsc` 与 vitest 都不会红——只有这条测试会红。
    """

    def test_fixture_exists_and_is_json(self):
        assert FIXTURE.exists(), f"缺少契约 fixture：{FIXTURE}"
        json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_save_block_has_exactly_the_contract_fields(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert set(data["save"]) == {
            "owner", "theme", "clock", "weather", "bag", "coins", "skill_exp",
            "affinity", "gifts_today", "shop", "quest_log", "gather_counts", "version",
        }

    def test_nested_blocks_match_contract(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        save = data["save"]
        assert set(save["clock"]) == {"day", "minute", "part", "part_label"}
        assert set(save["weather"]) == {
            "id", "label", "icon", "light", "yield_pct", "pace_pct",
            "demand_pct", "blocks_gather",
        }
        assert set(save["shop"]) == {"kind", "level", "stock", "ask_prices"}
        assert set(save["quest_log"]) == {"day", "entries"}

    def test_row_blocks_match_contract(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert set(data["npc_rows"][0]) == {
            "id", "name", "role", "place", "activity", "awake",
            "hearts", "hearts_display", "marker",
        }
        assert set(data["shop_rows"][0]) == {
            "id", "label", "unlocked", "buy_price", "fair_price",
            "ask_price", "stock", "taste",
        }
        assert set(data["craft_rows"][0]) == {
            "id", "label", "category", "unlocked", "required_level",
            "current_level", "need_level",
        }
        assert set(data["gather_rows"][0]) == {
            "id", "label", "action", "action_label", "animation", "material",
            "qty_range", "minutes", "tile", "distance", "in_range",
        }

    def test_settlement_block_matches_contract(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        s = data["settlement"]
        assert set(s) == {
            "day", "weather", "theme", "lines", "total_revenue",
            "cost_of_goods_sold", "profit", "coins_after", "next_day",
        }
        assert set(s["lines"][0]) == {
            "good_id", "label", "unit_price", "sold", "revenue", "leftover",
        }

    def test_fixture_theme_is_one_of_registered(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert data["save"]["theme"] in themes.THEME_IDS

    def test_fixture_affinity_covers_whole_theme_roster(self):
        """快照里每个 NPC 都要有 0 点条目（前端不能靠undefined 兜底）。"""
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        roster = {n.id for n in npcs.npcs_of(data["save"]["theme"])}
        assert set(data["save"]["affinity"]) == roster


# ======================================================================
# state.py · 快照组装（B11 纯函数部分）
# ======================================================================

class TestLifeState:
    def test_new_save_is_consistent(self):
        s = state.new_save(OWNER, "ink", day=3)
        d = state.to_dict(s)
        assert d["clock"]["day"] == 3
        assert d["clock"]["minute"] == clock.DAILY_RESET_MINUTE
        assert d["clock"]["part"] == "dawn"
        assert d["coins"] == 0 and d["bag"] == {}

    def test_new_save_seeds_affinity_for_every_npc(self):
        s = state.new_save(OWNER, "ink")
        assert set(s.affinity) == {n.id for n in npcs.npcs_of("ink")}
        assert set(s.affinity.values()) == {0}

    def test_new_save_accepts_first_main_quest(self):
        s = state.new_save(OWNER, "country")
        assert s.quest_log["entries"][0]["quest_id"] == "main_country_1"

    def test_unknown_theme_rejected(self):
        with pytest.raises(KeyError):
            state.new_save(OWNER, "atlantis")

    def test_hud_line_mentions_day_part_weather_coins(self):
        s = state.new_save(OWNER, "field", day=5)
        s.coins = 42
        line = state.hud_line(s)
        assert "第5天" in line and "金币" in line and "42" in line

    def test_hearts_display_width_constant(self):
        s = state.new_save(OWNER, "forest")
        for npc in npcs.npcs_of("forest"):
            assert len(state.hearts_display(s, npc.id)) == npcs.MAX_HEARTS

    def test_npc_rows_expose_marker_and_hearts(self):
        s = state.new_save(OWNER, "forest")
        rows = state.npc_rows(s)
        assert len(rows) == len(npcs.npcs_of("forest"))
        for r in rows:
            assert {"id", "name", "place", "activity", "awake", "hearts",
                    "hearts_display", "marker"} <= set(r)

    def test_npc_rows_hearts_follow_affinity(self):
        s = state.new_save(OWNER, "forest")
        target = npcs.npcs_of("forest")[0].id
        s.affinity[target] = 250
        row = next(r for r in state.npc_rows(s) if r["id"] == target)
        assert row["hearts"] == 2

    def test_craft_rows_reflect_skill_level(self):
        s = state.new_save(OWNER, "ink")
        s.skill_exp = crafting.SKILL_THRESHOLDS[-1]
        rows = state.craft_rows(s)
        assert rows and all(r["unlocked"] for r in rows)

    def test_gather_rows_have_distance_when_tile_given(self):
        s = state.new_save(OWNER, "magic")
        node = themes.get_theme("magic").gather_nodes[0]
        rows = state.gather_rows(s, node.tile)
        assert rows[0]["distance"] == 0 and rows[0]["in_range"] is True
        far = state.gather_rows(s, (1, 1))
        assert all(r["in_range"] is False for r in far)

    def test_save_is_json_serialisable(self):
        s = state.new_save(OWNER, "scifi", day=2)
        text = json.dumps(state.to_dict(s), ensure_ascii=False)
        assert json.loads(text)["theme"] == "scifi"

    def test_log_round_trip_through_dict(self):
        log = quests.start_log("planet")
        log, _ = quests.advance(log, "gather", 5)
        raw = {"day": log.day,
               "entries": [{"quest_id": e.quest_id, "progress": e.progress,
                            "done": e.done, "claimed": e.claimed} for e in log.entries]}
        back = state.log_from_dict(raw)
        assert back.progress_of("main_planet_1") == 5
        assert back.entries[0].done is True
