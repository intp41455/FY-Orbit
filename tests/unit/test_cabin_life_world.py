"""B10 · 大世界数据层单测（对应 ``services/cabin_life/world.py``）。

沿用同目录 ``test_cabin_life.py`` 的断言风格：
    * 断言**不变量与公式**（「可达格 = 可走格 × 100%」「事件空手率 > 0」），
      不写死具体随机值——换 seed 不该让这些测试红；
    * 每条「越界 / 未知 id / 被挡 / 缺钥匙」路径都要有测试，确保它们**报错**、
      不静默通过；
    * 确定性用两次独立调用对比断言，不比较具体地形值。
"""

from __future__ import annotations

import pytest

from find_yourself.services.cabin_life import themes, world

OWNER = "owner-lb"

#: 地图尺寸契约（对应 A1 冻结的 TILE=32 / WORLD.width=5120）
EXPECTED_COLS = 160
EXPECTED_ROWS = 100


@pytest.fixture(scope="module")
def magic_map() -> world.WorldMap:
    return world.generate_map(OWNER, "magic")


@pytest.fixture(scope="module")
def forest_map() -> world.WorldMap:
    return world.generate_map(OWNER, "forest")


# ======================================================================
# 尺寸与常量对齐（A1 冻结常量，B10 只引用）
# ======================================================================

class TestMapSize:
    def test_tile_matches_a1_frozen_value(self):
        assert world.TILE == 32

    def test_cols_derive_from_world_width(self):
        # A1 冻结 WORLD.width = 5120 虚拟像素；列数 = 5120 / 32
        assert world.MAP_COLS == 5120 // world.TILE == EXPECTED_COLS

    def test_meets_spec_minimum_100x100(self):
        assert world.MAP_COLS >= 100 and world.MAP_ROWS >= 100

    def test_pixel_size(self):
        assert world.MAP_WIDTH_PX == EXPECTED_COLS * 32
        assert world.MAP_HEIGHT_PX == EXPECTED_ROWS * 32

    def test_spawn_is_grid_aligned_with_a1_spawn_x(self):
        # A1 WORLD.spawnX = 2208 虚拟像素 → 2208 / 32 = 69
        assert world.SPAWN_TILE == (2208 // 32, 50)

    def test_generated_map_dimensions(self, magic_map):
        assert (magic_map.cols, magic_map.rows) == (EXPECTED_COLS, EXPECTED_ROWS)
        assert len(magic_map.tiles) == EXPECTED_COLS * EXPECTED_ROWS


# ======================================================================
# 地形表
# ======================================================================

class TestTerrainTable:
    def test_every_terrain_has_label(self):
        assert set(world.TERRAIN_LABELS) == set(world.TERRAIN)

    def test_non_walkable_has_zero_cost(self):
        for tid, t in world.TERRAIN.items():
            if not t.walkable:
                assert t.move_cost == 0, f"{tid} 不可通行却带移动代价"

    def test_walkable_has_positive_cost(self):
        for tid, t in world.TERRAIN.items():
            if t.walkable:
                assert t.move_cost >= 1, f"{tid} 可通行却走不动"

    def test_unknown_terrain_raises(self):
        with pytest.raises(KeyError):
            world.terrain("lava")

    def test_unknown_terrain_message_lists_known(self):
        with pytest.raises(KeyError) as ei:
            world.terrain("lava")
        assert "lava" in str(ei.value)


# ======================================================================
# 主题配方
# ======================================================================

class TestRecipes:
    def test_recipe_covers_every_theme(self):
        assert set(world.RECIPES) == set(themes.THEME_IDS)

    def test_base_terrain_is_walkable(self):
        # 基底不可通行会把整张图铺成死地（实测 scifi 曾因此 reach=12）
        for theme, r in world.RECIPES.items():
            assert world.TERRAIN[r.base].walkable, f"{theme} 基底不可通行"

    def test_unknown_theme_raises(self):
        with pytest.raises(KeyError):
            world.recipe_of("atlantis")

    def test_scatter_only_known_terrain(self):
        for theme, r in world.RECIPES.items():
            for tid, weight in r.scatter:
                assert tid in world.TERRAIN, f"{theme} 引用未知地形 {tid}"
                assert weight > 0

    def test_river_pct_in_range(self):
        for r in world.RECIPES.values():
            assert 0 <= r.river_pct <= 20
            assert 0 <= r.fog_pct <= 20


# ======================================================================
# 地图生成：确定性
# ======================================================================

class TestGenerationDeterminism:
    def test_same_owner_same_terrain(self):
        a = world.generate_map(OWNER, "magic")
        b = world.generate_map(OWNER, "magic")
        assert a.tiles == b.tiles

    def test_same_owner_same_chests(self):
        a = world.generate_map(OWNER, "magic")
        b = world.generate_map(OWNER, "magic")
        assert a.chests == b.chests and a.secrets == b.secrets

    def test_different_owner_different_terrain(self):
        a = world.generate_map(OWNER, "magic")
        b = world.generate_map("other-owner", "magic")
        assert a.tiles != b.tiles

    def test_size_changes_terrain(self):
        a = world.generate_map(OWNER, "magic", cols=64, rows=64)
        b = world.generate_map(OWNER, "magic", cols=80, rows=80)
        assert a.tiles != b.tiles

    def test_too_small_map_raises(self):
        with pytest.raises(ValueError):
            world.generate_map(OWNER, "magic", cols=4, rows=4)


# ======================================================================
# 地图生成：不变量（这是「能玩」的地基）
# ======================================================================

class TestMapInvariants:
    @pytest.mark.parametrize("theme", themes.THEME_IDS)
    def test_every_theme_generates(self, theme):
        wm = world.generate_map(OWNER, theme)
        assert len(wm.tiles) == EXPECTED_COLS * EXPECTED_ROWS

    @pytest.mark.parametrize("theme", themes.THEME_IDS)
    def test_no_island_deadlock(self, theme):
        """从出生点 flood-fill 必须覆盖几乎全部可走格（否则玩家被关在一角）。"""
        wm = world.generate_map(OWNER, theme)
        reach = len(wm.connected_from_spawn())
        assert reach >= wm.walkable_count() * 0.99, f"{theme} 有大量孤岛"

    @pytest.mark.parametrize("theme", themes.THEME_IDS)
    def test_spawn_is_walkable(self, theme):
        assert world.generate_map(OWNER, theme).is_walkable(
            world.generate_map(OWNER, theme).spawn
        )

    @pytest.mark.parametrize("theme", themes.THEME_IDS)
    def test_walkable_ratio_not_trivial(self, theme):
        """可走比例过低说明地图被障碍糊死（规范要的是「可探索」不是「迷宫」）。"""
        wm = world.generate_map(OWNER, theme)
        assert wm.walkable_ratio() >= 0.5, f"{theme} 可走比例仅 {wm.walkable_ratio():.2f}"

    @pytest.mark.parametrize("theme", themes.THEME_IDS)
    def test_all_tiles_use_known_terrain(self, theme):
        wm = world.generate_map(OWNER, theme)
        for tid in set(wm.tiles):
            assert tid in world.TERRAIN

    def test_histogram_sums_to_total(self, magic_map):
        assert sum(magic_map.histogram().values()) == len(magic_map.tiles)

    def test_row_of_matches_tiles(self, magic_map):
        y = 37
        assert magic_map.row_of(y) == magic_map.tiles[y * magic_map.cols:(y + 1) * magic_map.cols]

    def test_border_not_solid_wall(self, magic_map):
        """地图边缘不应整圈不可通行，否则「无缝滚动」到边会撞墙。"""
        solid = 0
        for x in range(magic_map.cols):
            solid += not magic_map.is_walkable((x, 0))
        assert solid < magic_map.cols * 0.9


# ======================================================================
# 取值与越界
# ======================================================================

class TestTileAccess:
    def test_at_returns_terrain(self, magic_map):
        assert magic_map.at((0, 0)) in world.TERRAIN

    def test_out_of_bounds_raises(self, magic_map):
        with pytest.raises(IndexError):
            magic_map.at((magic_map.cols, 0))

    def test_negative_raises(self, magic_map):
        with pytest.raises(IndexError):
            magic_map.at((-1, 5))

    def test_row_out_of_bounds_raises(self, magic_map):
        with pytest.raises(IndexError):
            magic_map.row_of(magic_map.rows)

    def test_in_bounds(self, magic_map):
        assert magic_map.in_bounds((0, 0))
        assert not magic_map.in_bounds((magic_map.cols, 0))

    def test_is_walkable_false_out_of_bounds(self, magic_map):
        assert magic_map.is_walkable((9999, 9999)) is False


# ======================================================================
# 秘密区域
# ======================================================================

class TestSecretAreas:
    def test_count_per_theme(self, magic_map):
        assert len(magic_map.secrets) == world.SECRETS_PER_THEME

    @pytest.mark.parametrize("theme", themes.THEME_IDS)
    def test_every_theme_has_enough_secrets(self, theme):
        wm = world.generate_map(OWNER, theme)
        assert len(wm.secrets) >= 5, f"{theme} 秘密区域过少"

    def test_ids_unique_and_prefixed(self, magic_map):
        ids = [s.id for s in magic_map.secrets]
        assert len(ids) == len(set(ids))
        assert all(i.startswith("magic_secret_") for i in ids)

    def test_terrain_id_known(self, magic_map):
        for s in magic_map.secrets:
            assert s.terrain_id in world.TERRAIN

    def test_tiles_in_bounds_and_walkable(self, magic_map):
        for s in magic_map.secrets:
            assert magic_map.in_bounds(s.tile)
            assert magic_map.is_walkable(s.tile)

    def test_secrets_are_spread_out(self, magic_map):
        tiles = [s.tile for s in magic_map.secrets]
        for i, a in enumerate(tiles):
            for b in tiles[i + 1:]:
                assert abs(a[0] - b[0]) + abs(a[1] - b[1]) >= 14

    def test_within_range_only_nearby(self, magic_map):
        s = magic_map.secrets[0]
        assert world.secrets_within(magic_map, s.tile) != []
        assert world.secrets_within(magic_map, (magic_map.cols - 1, 0)) == []

    def test_radius_positive(self, magic_map):
        assert all(s.radius >= 2 for s in magic_map.secrets)

    def test_secret_terrain_is_theme_specific(self):
        assert world.SECRET_TERRAIN["magic"] != world.SECRET_TERRAIN["scifi"]


# ======================================================================
# 隐藏宝箱
# ======================================================================

class TestChests:
    def test_counts_follow_table(self, magic_map):
        for tier, count in zip(world.CHEST_TIERS, world.CHESTS_PER_TIER["magic"]):
            got = len([c for c in magic_map.chests if c.tier == tier])
            assert got == count, f"{tier} 档宝箱数 {got} ≠ {count}"

    @pytest.mark.parametrize("theme", themes.THEME_IDS)
    def test_chests_per_tier_table_matches_theme_list(self, theme):
        assert theme in world.CHESTS_PER_TIER

    @pytest.mark.parametrize("theme", themes.THEME_IDS)
    def test_at_least_eight_chests(self, theme):
        wm = world.generate_map(OWNER, theme)
        assert len(wm.chests) >= 8, f"{theme} 宝箱过少"

    def test_ids_unique(self, magic_map):
        ids = [c.id for c in magic_map.chests]
        assert len(ids) == len(set(ids))

    def test_all_tiles_in_bounds(self, magic_map):
        for c in magic_map.chests:
            assert magic_map.in_bounds(c.tile)

    def test_no_duplicate_tiles(self, magic_map):
        tiles = [c.tile for c in magic_map.chests]
        assert len(tiles) == len(set(tiles))

    def test_gated_tiers_exist(self, magic_map):
        needs = {c.tier: c.needs for c in magic_map.chests}
        assert needs["t1"] is None
        assert needs["t2"] == "key"
        assert needs["t3"] == "tool"

    def test_chests_at(self, magic_map):
        c = magic_map.chests[0]
        assert world.chests_at(magic_map, c.tile) == [c]

    def test_chests_at_empty_far_away(self, magic_map):
        assert world.chests_at(magic_map, (0, 0)) in ([], world.chests_at(magic_map, (0, 0)))
        assert [c for c in magic_map.chests if c.tile == (0, 0)] == []

    def test_tier_rules_complete(self):
        for tier in world.CHEST_TIERS:
            rule = world.CHEST_TIER_RULES[tier]
            assert rule["label"]
            lo, hi = rule["coins"]
            assert 0 < lo <= hi

    def test_higher_tier_pays_more(self):
        r1 = world.CHEST_TIER_RULES["t1"]
        r2 = world.CHEST_TIER_RULES["t2"]
        r3 = world.CHEST_TIER_RULES["t3"]
        assert r1["coins"][1] < r2["coins"][0]
        assert r2["coins"][1] < r3["coins"][0]


class TestOpeningChests:
    def _chest(self, tier):
        return next(c for c in world.generate_map(OWNER, "magic").chests if c.tier == tier)

    def test_t1_opens_without_key(self):
        got = world.open_chest(self._chest("t1"), _rng(), bag_has_key=False, bag_has_tool=False)
        assert got["ok"] is True
        assert got["coins"] >= 0

    def test_t2_refused_without_key(self):
        got = world.open_chest(self._chest("t2"), _rng(), bag_has_key=False, bag_has_tool=False)
        assert got["ok"] is False
        assert "钥匙" in got["reason"]

    def test_t2_opens_with_key(self):
        got = world.open_chest(self._chest("t2"), _rng(), bag_has_key=True, bag_has_tool=False)
        assert got["ok"] is True

    def test_t3_refused_without_tool(self):
        got = world.open_chest(self._chest("t3"), _rng(), bag_has_key=False, bag_has_tool=False)
        assert got["ok"] is False and "工具" in got["reason"]

    def test_t3_opens_with_tool(self):
        got = world.open_chest(self._chest("t3"), _rng(), bag_has_key=True, bag_has_tool=True)
        assert got["ok"] is True

    def test_refusal_gives_no_loot(self):
        got = world.open_chest(self._chest("t3"), _rng(), bag_has_key=False, bag_has_tool=False)
        assert "coins" not in got and "materials" not in got

    def test_coins_within_declared_range(self):
        c = self._chest("t1")
        lo, hi = c.coins
        for i in range(20):
            got = world.open_chest(c, _rng(i), bag_has_key=True, bag_has_tool=True)
            assert lo <= got["coins"] <= hi

    def test_materials_scale_with_tier(self):
        r1 = world.CHEST_TIER_RULES["t1"]["materials"]
        r3 = world.CHEST_TIER_RULES["t3"]["materials"]
        assert r3 > r1

    def test_result_is_deterministic_per_seed(self):
        c = self._chest("t2")
        a = world.open_chest(c, _rng(7), bag_has_key=True, bag_has_tool=False)
        b = world.open_chest(c, _rng(7), bag_has_key=True, bag_has_tool=False)
        assert a == b


class TestChestHint:
    def test_hint_empty_when_nothing_nearby(self, magic_map):
        # 站到一个角落，且断言附近确实没宝箱
        far = _tile_without_chest(magic_map)
        assert world.chest_hint(magic_map, far) == ""

    def test_hint_non_empty_next_to_chest(self, magic_map):
        c = magic_map.chests[0]
        assert world.chest_hint(magic_map, c.tile) != ""

    def test_hint_does_not_leak_coordinates(self, magic_map):
        c = magic_map.chests[0]
        hint = world.chest_hint(magic_map, c.tile)
        assert str(c.tile[0]) not in hint or str(c.tile[1]) not in hint

    def test_hint_is_chinese_direction(self, magic_map):
        c = magic_map.chests[0]
        assert any(w in world.chest_hint(magic_map, c.tile) for w in ("北", "南", "东", "西"))


# ======================================================================
# 随机事件
# ======================================================================

class TestEvents:
    def test_all_ids_unique(self):
        assert len(world.EVENT_IDS) == len(set(world.EVENT_IDS))

    def test_weights_match_table(self):
        for e in world.EVENTS:
            assert world.EVENT_WEIGHTS[e.id] == e.weight

    def test_empty_events_exist(self):
        """规范明确要求「一无所获」的诚实态。"""
        assert len(world.EMPTY_EVENT_IDS) >= 2

    def test_empty_weight_share_is_significant(self):
        assert world.event_pct() >= 15, f"空手率仅 {world.event_pct()}%，惊喜感会被稀释"

    def test_every_event_has_line(self):
        for eid in world.EVENT_IDS:
            assert world.EVENT_LINES[eid].strip()

    def test_every_event_has_label(self):
        for e in world.EVENTS:
            assert e.label

    def test_unknown_event_raises(self):
        with pytest.raises(KeyError):
            world.event_of("meteor")

    def test_roll_is_deterministic(self):
        a = world.roll_event(OWNER, "magic", 3, (70, 50))
        b = world.roll_event(OWNER, "magic", 3, (70, 50))
        assert a.id == b.id

    def test_roll_varies_across_tiles(self):
        got = {world.roll_event(OWNER, "magic", 3, (10 + i, 40)).id for i in range(40)}
        assert len(got) >= 3, "40 个格只 roll 出 1 种事件，随机性不足"

    def test_roll_varies_across_days(self):
        got = {world.roll_event(OWNER, "magic", d, (70, 50)).id for d in range(1, 30)}
        assert len(got) >= 3

    def test_templated_lines_render_npc_name(self):
        templated = [e for e in world.EVENT_IDS if "{npc}" in world.EVENT_LINES[e]]
        assert templated, "应有事件文案引用当日 NPC 名"
        o = world.resolve_event(OWNER, "magic", 2, (71, 50), npc="阿禾")
        if o.event_id in templated:
            assert "阿禾" in o.line

    def test_no_line_leaves_template_placeholder(self):
        o = world.resolve_event(OWNER, "magic", 2, (71, 50), npc="阿禾")
        assert "{npc}" not in o.line

    def test_empty_event_gives_nothing(self):
        """诚实态：空手事件不能偷偷发材料/金币。"""
        for i in range(60):
            o = world.resolve_event(OWNER, "forest", 1, (10 + i, 40))
            if o.empty:
                assert o.materials == () and o.coins == 0

    def test_non_empty_event_sometimes_gives(self):
        got = [world.resolve_event(OWNER, "forest", 1, (10 + i, 40))
               for i in range(80)]
        assert any(o.materials for o in got)

    def test_event_materials_belong_to_theme(self):
        pool = set(themes.get_theme("ink").materials)
        for i in range(60):
            o = world.resolve_event(OWNER, "ink", 1, (10 + i, 40))
            for mid, _ in o.materials:
                assert mid in pool

    def test_material_qty_positive(self):
        for i in range(60):
            o = world.resolve_event(OWNER, "magic", 1, (10 + i, 40))
            for _, qty in o.materials:
                assert qty >= 1

    def test_resolve_is_deterministic(self):
        a = world.resolve_event(OWNER, "magic", 4, (72, 51))
        b = world.resolve_event(OWNER, "magic", 4, (72, 51))
        assert a == b

    def test_explore_rows_length(self):
        tiles = [(70, 50), (71, 50), (72, 50)]
        rows = world.explore_rows(OWNER, "magic", 1, tiles)
        assert len(rows) == 3

    def test_explore_rows_all_distinct_tiles(self):
        tiles = [(70, 50), (71, 50)]
        rows = world.explore_rows(OWNER, "magic", 1, tiles)
        assert len({r.event_id for r in rows}) >= 1


# ======================================================================
# 移动与寻路
# ======================================================================

class TestMovement:
    def test_step_towards_moves_one_tile(self, magic_map):
        start = magic_map.spawn
        goal = _walkable_neighbour(magic_map, start)
        got = world.step_towards(magic_map, start, goal)
        assert got.ok is True
        assert abs(got.tile[0] - start[0]) + abs(got.tile[1] - start[1]) == 1

    def test_step_towards_at_goal_is_noop(self, magic_map):
        start = magic_map.spawn
        got = world.step_towards(magic_map, start, start)
        assert got.ok and got.minutes == 0 and got.tile == start

    def test_step_into_wall_reports_reason(self, magic_map):
        blocked = _blocked_tile(magic_map)
        if blocked is None:
            pytest.skip("该地图无相邻墙")
        start = magic_map.spawn
        got = world.step_towards(magic_map, start, blocked) if _adjacent(start, blocked) \
            else world.step_towards(magic_map, start, (blocked[0], start[1]))
        if not got.ok:
            assert got.reason, "被挡时必须给出原因，不能空字符串"

    def test_step_to_edge_reports_boundary(self, magic_map):
        # 站在 x=0 的可走格上，目标在图外：横向候选越界，竖向无位移 → 必须报边界
        y = next(t for t in range(magic_map.rows) if magic_map.is_walkable((0, t)))
        got = world.step_towards(magic_map, (0, y), (-99, y))
        assert got.ok is False
        assert "边界" in got.reason
        assert got.tile == (0, y)

    def test_move_cost_zero_on_blocked(self, magic_map):
        blocked = _blocked_tile(magic_map)
        if blocked is None:
            pytest.skip("该地图无墙")
        assert world.move_cost(magic_map, magic_map.spawn, blocked) == 0

    def test_move_cost_positive_on_walkable(self, magic_map):
        nb = _walkable_neighbour(magic_map, magic_map.spawn)
        assert world.move_cost(magic_map, magic_map.spawn, nb) >= 1

    def test_water_costs_more_than_grass(self, magic_map):
        assert world.TERRAIN["water"].move_cost > world.TERRAIN["grass"].move_cost


class TestPathfinding:
    def test_same_tile_is_zero_steps(self, magic_map):
        assert world.path_length(magic_map, magic_map.spawn, magic_map.spawn) == 0

    def test_neighbour_is_one_step(self, magic_map):
        nb = _walkable_neighbour(magic_map, magic_map.spawn)
        assert world.path_length(magic_map, magic_map.spawn, nb) == 1

    def test_far_corner_reachable(self, magic_map):
        d = world.path_length(magic_map, magic_map.spawn, (0, 0))
        assert d is not None and d > 0

    def test_unwalkable_target_returns_none(self, magic_map):
        blocked = _blocked_tile(magic_map)
        if blocked is None:
            pytest.skip("该地图无墙")
        assert world.path_length(magic_map, magic_map.spawn, blocked) is None

    def test_unwalkable_source_returns_none(self, magic_map):
        blocked = _blocked_tile(magic_map)
        if blocked is None:
            pytest.skip("该地图无墙")
        assert world.path_length(magic_map, blocked, magic_map.spawn) is None

    def test_reachable_count_from_spawn(self, magic_map):
        n = world.reachable_count(magic_map, magic_map.spawn)
        assert n == len(magic_map.connected_from_spawn())

    def test_reachable_from_wall_is_zero(self, magic_map):
        blocked = _blocked_tile(magic_map)
        if blocked is None:
            pytest.skip("该地图无墙")
        assert world.reachable_count(magic_map, blocked) == 0

    def test_reachable_from_corner_is_subset(self, magic_map):
        assert world.reachable_count(magic_map, (0, 0)) <= \
            world.reachable_count(magic_map, magic_map.spawn)


# ======================================================================
# 探索度
# ======================================================================

class TestExploration:
    def test_empty_visit_is_zero(self, magic_map):
        assert world.explored_ratio(magic_map, []) == 0.0

    def test_full_visit_is_one(self, magic_map):
        all_tiles = [(x, y) for y in range(magic_map.rows) for x in range(magic_map.cols)]
        assert world.explored_ratio(magic_map, all_tiles) == 1.0

    def test_ratio_never_exceeds_one(self, magic_map):
        tiles = [(0, 0)] * 999
        assert world.explored_ratio(magic_map, tiles) <= 1.0

    def test_blocked_tiles_excluded_from_denominator(self, magic_map):
        """分母是可走格；把一堆墙喂进去也不该把比例压到 0.5 以下。"""
        walls = [(x, y) for y in range(magic_map.rows) for x in range(magic_map.cols)
                 if not magic_map.is_walkable((x, y))]
        got = world.explored_ratio(magic_map, walls)
        assert got == 0.0, "只喂墙时探索度必须是 0（墙本来就不算）"

    def test_out_of_bounds_visits_ignored(self, magic_map):
        assert world.explored_ratio(magic_map, [(-5, -5), (9999, 9999)]) == 0.0

    def test_monotonic_in_visited_count(self, magic_map):
        a = world.explored_ratio(magic_map, [(69, 50), (70, 50)])
        b = world.explored_ratio(magic_map, [(69, 50), (70, 50), (71, 50)])
        assert b > a

    @pytest.mark.parametrize("pct,grade", [
        (0.0, "初来乍到"),
        (0.10, "转了转"),
        (0.30, "熟门熟路"),
        (0.60, "四处走走"),
        (0.90, "无所不知"),
    ])
    def test_grade_thresholds(self, pct, grade):
        assert world.explore_grade(pct)[0] == grade

    def test_grade_has_comment_text(self):
        for pct in (0.0, 0.1, 0.3, 0.6, 0.9):
            name, note = world.explore_grade(pct)
            assert name and note


# ======================================================================
# 序列化契约（前端要拿这份数据）
# ======================================================================

class TestWorldSerialization:
    def test_chest_as_dict_shape(self, magic_map):
        from dataclasses import asdict
        d = asdict(magic_map.chests[0])
        assert set(d) == {"id", "theme", "tier", "tile", "secret_id",
                          "label", "needs", "coins", "materials"}
        assert isinstance(d["tile"], tuple) and len(d["tile"]) == 2

    def test_secret_as_dict_shape(self, magic_map):
        from dataclasses import asdict
        d = asdict(magic_map.secrets[0])
        assert set(d) == {"id", "theme", "tile", "radius", "label", "terrain_id"}

    def test_outcome_as_dict_shape(self):
        from dataclasses import asdict
        o = world.resolve_event(OWNER, "magic", 1, (70, 50))
        assert set(asdict(o)) == {"event_id", "label", "line",
                                  "materials", "coins", "empty"}

    def test_map_summary_is_serialisable(self, magic_map):
        import json
        payload = {
            "cols": magic_map.cols,
            "rows": magic_map.rows,
            "spawn": list(magic_map.spawn),
            "width_px": world.MAP_WIDTH_PX,
            "histogram": magic_map.histogram(),
            "secrets": [s.id for s in magic_map.secrets],
            "chests": [c.id for c in magic_map.chests],
        }
        assert json.loads(json.dumps(payload, ensure_ascii=False))["cols"] == EXPECTED_COLS


# ======================================================================
# helpers（不是测试）
# ======================================================================

def _rng(salt: int = 0):
    from find_yourself.services.cabin_life import rng as rng_mod
    return rng_mod.rng_for(OWNER, "chest", salt)


def _adjacent(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def _walkable_neighbour(wm: world.WorldMap, tile: tuple[int, int]) -> tuple[int, int]:
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nb = (tile[0] + dx, tile[1] + dy)
        if wm.is_walkable(nb):
            return nb
    raise AssertionError("出生点四邻全是墙，地图生成有问题")


def _blocked_tile(wm: world.WorldMap) -> tuple[int, int] | None:
    for y in range(wm.rows):
        for x in range(wm.cols):
            if not wm.is_walkable((x, y)):
                return (x, y)
    return None


def _tile_without_chest(wm: world.WorldMap) -> tuple[int, int]:
    taken = {c.tile for c in wm.chests}
    for y in range(wm.rows):
        for x in range(wm.cols):
            if (x, y) not in taken and wm.is_walkable((x, y)):
                return (x, y)
    raise AssertionError("找不到无宝箱的格")
