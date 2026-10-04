"""B4 · 建造与装修数据层单测（对应 ``services/cabin_life/build.py``）。

断言风格（同 ``test_cabin_life_world.py``）：
    * 钉**规则与不变量**（「90° 旋转宽高互换」「地毯可与床叠放」），
      不钉具体坐标；
    * 每条**拒绝路径**都要有测试，并断言**拒绝理由非空**——
      这是说明书「越界/禁用位拒绝并提示」的验收点；
    * 「确认」不偷偷改变布局（只算不清零）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.cabin_life import build


# ======================================================================
# 网格常量（对齐 A1 冻结的 TILE=32）
# ======================================================================

class TestGridConstants:
    def test_tile_is_32(self):
        assert build.TILE == 32

    def test_room_is_positive(self):
        assert build.ROOM_COLS >= 8 and build.ROOM_ROWS >= 6

    def test_door_row_is_inside_room(self):
        assert 0 <= build.DOOR_ROW < build.ROOM_ROWS

    def test_wall_band_inside_room(self):
        lo, hi = build.WALL_BAND
        assert 0 <= lo <= hi < build.ROOM_ROWS

    def test_door_clear_range_inside_room(self):
        lo, hi = build.DOOR_CLEAR_X
        assert 0 <= lo <= hi < build.ROOM_COLS

    def test_snap_rounds_to_grid(self):
        assert build.snap(0) == 0
        assert build.snap(15) == 0
        assert build.snap(build.TILE) == build.TILE

    def test_snap_matches_a1_math_round_on_half_cell(self):
        """A1 用 `Math.round(v / tile) * tile`：JS 在 x.5 向上、Python 向下。

        显式钉住半格行为与前端一致，否则后端说「吸附到这格」、
        前端却画到隔壁格。
        """
        assert build.snap(build.TILE / 2) == build.TILE
        assert build.snap(-build.TILE / 2) == 0

    def test_snap_rejects_non_positive_tile(self):
        with pytest.raises(ValueError):
            build.snap(10, tile=0)

    def test_snap_tile_is_floor_division(self):
        assert build.snap_tile(0) == 0
        assert build.snap_tile(build.TILE - 1) == 0
        assert build.snap_tile(build.TILE) == 1

    def test_tile_of_xy(self):
        assert build.tile_of_xy(build.TILE * 3 + 5, build.TILE * 2) == (3, 2)


# ======================================================================
# 家具目录与分类
# ======================================================================

class TestCatalog:
    def test_every_category_has_items(self):
        for cat in build.CATEGORIES:
            assert len(build.of_category(cat)) >= 3, f"分类 {cat} 太空"

    def test_six_categories_as_spec(self):
        assert set(build.CATEGORIES) == {
            "table_chair", "bed", "cabinet", "decor", "lamp", "plant"
        }

    def test_labels_cover_categories(self):
        assert set(build.CATEGORY_LABELS) == set(build.CATEGORIES)

    def test_ids_unique(self):
        ids = [f.id for f in build.CATALOG]
        assert len(ids) == len(set(ids))

    def test_ids_are_namespaced(self):
        """B4 与 W1（cabin_interior）的 id 必须隔离，不能互相覆盖。"""
        assert all(f.id.startswith("b4_") for f in build.CATALOG)

    def test_unknown_furniture_raises(self):
        with pytest.raises(KeyError):
            build.furniture("gold_toilet")

    def test_unknown_category_raises(self):
        with pytest.raises(KeyError):
            build.of_category("fountain")

    def test_sizes_positive_and_bounded(self):
        for f in build.CATALOG:
            assert f.width >= 1 and f.height >= 1
            assert f.footprint <= build.MAX_FOOTPRINT_CELLS

    def test_cost_non_negative(self):
        for f in build.CATALOG:
            assert f.cost >= 0

    def test_layers_valid(self):
        for f in build.CATALOG:
            assert f.layer in build.LAYERS

    def test_bad_definition_rejected(self):
        with pytest.raises(ValueError):
            build.FurnitureDef("x", "x", "nope", "floor", 1, 1, 1)
        with pytest.raises(ValueError):
            build.FurnitureDef("x", "x", "bed", "air", 1, 1, 1)
        with pytest.raises(ValueError):
            build.FurnitureDef("x", "x", "bed", "floor", 0, 1, 1)
        with pytest.raises(ValueError):
            build.FurnitureDef("x", "x", "bed", "floor", 99, 99, 1)

    def test_category_rows_shape(self):
        rows = build.category_rows()
        assert len(rows) == len(build.CATEGORIES)
        for r in rows:
            assert set(r) == {"category", "label", "count", "cheapest", "ids"}
            assert r["cheapest"] == min(
                build.furniture(i).cost for i in r["ids"]
            )

    def test_category_rows_counts_match_catalog(self):
        assert sum(r["count"] for r in build.category_rows()) == len(build.CATALOG)


# ======================================================================
# 旋转
# ======================================================================

class TestRotation:
    def test_normalize_accepts_four(self):
        for d in (0, 90, 180, 270):
            assert build.normalize_rotation(d) == d

    def test_non_right_angle_raises(self):
        for d in (45, 100, -30, 360):
            with pytest.raises(ValueError):
                build.normalize_rotation(d)

    def test_size_swaps_at_90(self):
        f = build.furniture("b4_bunk_bed")
        assert build.size_at(f, 90) == (f.height, f.width)
        assert build.size_at(f, 270) == (f.height, f.width)

    def test_size_same_at_0_and_180(self):
        f = build.furniture("b4_bunk_bed")
        assert build.size_at(f, 0) == (f.width, f.height)
        assert build.size_at(f, 180) == (f.width, f.height)

    def test_square_furniture_same_size_all_ways(self):
        f = build.furniture("b4_pot_plant")
        assert len({build.size_at(f, d) for d in build.ROTATIONS}) == 1

    def test_next_rotation_cycles(self):
        assert build.next_rotation(0) == 90
        assert build.next_rotation(270) == 0
        assert build.next_rotation(90, -1) == 0

    def test_next_rotation_four_steps_is_identity(self):
        d = 180
        for _ in range(4):
            d = build.next_rotation(d)
        assert d == 180

    def test_rotate_item_changes_rotation(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_bunk_bed", 2, 3))
        assert lay.by_id("a").rotation == 0
        turned = build.rotate_item(lay, "a")
        assert turned.by_id("a").rotation == 90

    def test_rotate_item_footprint_swaps(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_bunk_bed", 2, 3))
        assert lay.by_id("a").footprint == build.size_at(
            build.furniture("b4_bunk_bed"), 0
        )[0] * build.size_at(build.furniture("b4_bunk_bed"), 0)[1]

    def test_rotate_that_would_exceed_raises(self):
        lay = build.new_layout()
        # 贴右下角放一条 3×1 长地毯（y=7 意味着它只剩 1 行可用高度），
        # 转 90° 后变 1 宽 × 3 高，会顶出下边界
        lay = build.place(lay, build.PlacedItem("a", "b4_rug_long",
                                                build.ROOM_COLS - 3,
                                                build.ROOM_ROWS - 2))
        with pytest.raises(ValueError) as ei:
            build.rotate_item(lay, "a")
        assert "转不开" in str(ei.value)

    def test_rotate_unknown_item_raises(self):
        with pytest.raises(KeyError):
            build.rotate_item(build.new_layout(), "ghost")

    def test_rotate_does_not_mutate_original(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_tea_table", 2, 3))
        build.rotate_item(lay, "a")
        assert lay.by_id("a").rotation == 0


# ======================================================================
# 网格吸附与占位
# ======================================================================

class TestFootprint:
    def test_cells_count_matches_size(self):
        it = build.PlacedItem("a", "b4_double_bed", 2, 2)
        assert len(it.cells) == it.def_.width * it.def_.height

    def test_cells_start_at_origin(self):
        it = build.PlacedItem("a", "b4_tea_table", 3, 4)
        assert it.cells[0] == (3, 4)

    def test_ghost_footprint_matches_item(self):
        it = build.PlacedItem("a", "b4_tea_table", 3, 4)
        assert build.ghost_footprint(build.new_layout(), "b4_tea_table", 3, 4) == list(it.cells)

    def test_ghost_shows_out_of_bounds_cell(self):
        """越界时预览必须把界外格也画出来，玩家才看得见「有一格在外面」。"""
        cells = build.ghost_footprint(build.new_layout(), "b4_double_bed",
                                      build.ROOM_COLS - 1, 3)
        assert any(c[0] >= build.ROOM_COLS for c in cells)

    def test_unknown_furniture_in_item_raises(self):
        with pytest.raises(KeyError):
            build.PlacedItem("a", "sofa_from_other_game", 0, 0)

    def test_bad_rotation_in_item_raises(self):
        with pytest.raises(ValueError):
            build.PlacedItem("a", "b4_pot_plant", 0, 0, rotation=33)


# ======================================================================
# 放置：成功路径
# ======================================================================

class TestPlacing:
    def test_place_returns_new_layout(self):
        lay = build.new_layout()
        out = build.place(lay, build.PlacedItem("a", "b4_pot_plant", 2, 3))
        assert out is not lay and lay.items == []

    def test_place_appends(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_pot_plant", 2, 3))
        lay = build.place(lay, build.PlacedItem("b", "b4_candle", 4, 3))
        assert lay.ids() == ["a", "b"]

    def test_place_preserves_draw_order(self):
        lay = build.new_layout()
        for i, fid in enumerate(("b4_pot_plant", "b4_candle", "b4_stool")):
            lay = build.place(lay, build.PlacedItem(f"i{i}", fid, i, 5))
        assert lay.ids() == ["i0", "i1", "i2"]

    def test_all_floor_furniture_placeable_somewhere(self):
        """每件家具都必须至少有一个合法落点，否则是死条目。"""
        for f in build.CATALOG:
            found = False
            for y in range(build.ROOM_ROWS):
                for x in range(build.ROOM_COLS):
                    it = build.PlacedItem(f"probe_{f.id}", f.id, x, y)
                    if build.can_place(build.new_layout(), it).ok:
                        found = True
                        break
                if found:
                    break
            assert found, f"{f.id} 全屋无处可放"


# ======================================================================
# 放置：拒绝路径（说明书验收点）
# ======================================================================

class TestRejections:
    def _ok(self, fid, x, y, rot=0, lay=None) -> bool:
        lay = lay if lay is not None else build.new_layout()
        return build.can_place(lay, build.PlacedItem("probe", fid, x, y, rot)).ok

    def _reason(self, fid, x, y, rot=0, lay=None):
        lay = lay if lay is not None else build.new_layout()
        it = build.PlacedItem("probe", fid, x, y, rot)
        v = build.can_place(lay, it)
        assert v.ok is False, f"{fid} @({x},{y}) 本该被拒"
        assert v.reason, "拒绝必须带原因文案"
        return v.reason

    def test_overlap_rejected(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_tea_table", 3, 4))
        assert "重叠" in self._reason("b4_candle", 4, 4, lay=lay)

    def test_overlap_from_different_side(self):
        lay = build.new_layout()
        # 2×1 的茶几占 (3,4)(4,4)；烛台落到右格同样算重叠
        lay = build.place(lay, build.PlacedItem("a", "b4_tea_table", 3, 4))
        assert self._reason("b4_candle", 4, 4, lay=lay)

    def test_touching_but_not_overlapping_is_allowed(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_tea_table", 3, 4))
        assert self._ok("b4_candle", 5, 4, lay=lay)

    def test_negative_x_rejected(self):
        assert "超出房间范围" in self._reason("b4_candle", -1, 4)

    def test_negative_y_rejected(self):
        assert "超出房间范围" in self._reason("b4_candle", 3, -1)

    def test_past_right_edge_rejected(self):
        assert "超出房间范围" in self._reason(
            "b4_double_bed", build.ROOM_COLS - 1, 3
        )

    def test_past_bottom_edge_rejected(self):
        assert "超出房间范围" in self._reason(
            "b4_bunk_bed", 3, build.ROOM_ROWS - 1
        )

    def test_too_big_for_room_rejected(self):
        """家具比房间还宽/高——与「坐标越界」是两条不同的拒绝原因。"""
        tiny = build.Layout(items=[], cols=2, rows=2)
        it = build.PlacedItem("x", "b4_double_bed", 0, 0)
        v = build.can_place(tiny, it)
        assert v.ok is False and "放不下" in v.reason

    def test_footprint_over_cap_rejected_at_definition(self):
        with pytest.raises(ValueError):
            build.FurnitureDef("b4_huge", "巨型柜", "cabinet", "floor",
                               build.MAX_FOOTPRINT_CELLS + 1, 1, 1)

    def test_door_cell_rejected(self):
        cell = sorted(build.door_cells())[0]
        assert "门口" in self._reason("b4_candle", cell[0], cell[1])

    def test_every_door_cell_rejected(self):
        for x, y in build.door_cells():
            assert "门口" in self._reason("b4_candle", x, y)

    def test_rug_also_blocked_at_door(self):
        """地毯也不能堵门——堵的是「路」，不是「高度」。

        2×2 地毯从门口往上一格铺，其中一格会压在门口格上。
        """
        cell = sorted(build.door_cells())[0]
        assert "门口" in self._reason("b4_rug_small", cell[0], cell[1] - 1)

    def test_wall_mounted_rejected_on_floor(self):
        assert "只能挂在墙上" in self._reason("b4_painting", 3, 4)

    def test_floor_furniture_rejected_on_wall_band(self):
        assert "不能放到墙上" in self._reason("b4_candle", 3, build.WALL_BAND[0])

    def test_rug_rejected_at_wall_root(self):
        assert "墙根" in self._reason("b4_rug_small", 3, build.WALL_BAND[1])

    def test_duplicate_id_rejected(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 3, 4))
        v = build.can_place(lay, build.PlacedItem("a", "b4_stool", 7, 4))
        assert v.ok is False and "同 id" in v.reason

    def test_place_raises_on_invalid(self):
        with pytest.raises(ValueError) as ei:
            build.place(build.new_layout(),
                        build.PlacedItem("a", "b4_candle", -1, 4))
        assert str(ei.value)

    @staticmethod
    def _filled_layout() -> build.Layout:
        """铺满 60 件 1×1 烛台的大房间（默认 12×9 放不下 60 件）。"""
        lay = build.Layout(items=[], cols=10, rows=10)
        for i in range(build.MAX_ITEMS):
            lay = build.place(lay, build.PlacedItem(
                f"i{i}", "b4_candle", i % 10, 2 + i // 10))
        return lay

    def test_item_cap_enforced(self):
        lay = self._filled_layout()
        assert len(lay.items) == build.MAX_ITEMS
        v = build.can_place(lay, build.PlacedItem("overflow", "b4_candle", 0, 1))
        assert v.ok is False and str(build.MAX_ITEMS) in v.reason

    def test_moving_existing_item_at_cap_is_allowed(self):
        lay = self._filled_layout()
        v = build.validate(lay, build.PlacedItem("i0", "b4_candle", 0, 1),
                           ignore_id="i0")
        assert v.ok is True


# ======================================================================
# 地毯叠放规则
# ======================================================================

class TestUnderLayer:
    def test_rug_under_bed_allowed(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("rug", "b4_rug_large", 2, 4))
        lay = build.place(lay, build.PlacedItem("bed", "b4_double_bed", 2, 4))
        assert "bed" in lay.ids()

    def test_bed_over_rug_placed_later_also_allowed(self):
        """顺序反了也一样——重叠只看落位层，不看谁先放。"""
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("bed", "b4_double_bed", 2, 4))
        lay = build.place(lay, build.PlacedItem("rug", "b4_rug_large", 2, 4))
        assert "rug" in lay.ids()

    def test_rug_does_not_occupy(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("rug", "b4_rug_large", 2, 4))
        assert lay.occupied() == {}

    def test_rug_on_rug_allowed_by_design(self):
        """两张地毯叠放**允许**：`under` 层不进占位集，否则一块房里只能铺一块。"""
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_rug_large", 2, 4))
        v = build.can_place(lay, build.PlacedItem("b", "b4_rug_small", 3, 5))
        assert v.ok is True

    def test_wall_item_occupies_its_wall_cell(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("p", "b4_painting", 3, build.WALL_BAND[0]))
        assert (3, build.WALL_BAND[0]) in lay.occupied()

    def test_two_wall_items_on_same_cell_rejected(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("p", "b4_painting", 3, build.WALL_BAND[0]))
        v = build.can_place(lay, build.PlacedItem("q", "b4_wall_clock",
                                                  3, build.WALL_BAND[0]))
        assert v.ok is False and "重叠" in v.reason

    def test_floor_item_blocks_another_floor_item(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_bunk_bed", 2, 3))
        occupied = lay.occupied()
        assert len(occupied) == build.furniture("b4_bunk_bed").footprint


# ======================================================================
# 移动 / 旋转 / 移除
# ======================================================================

class TestOperations:
    def test_move_ok(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 3, 4))
        out = build.move_to(lay, "a", 7, 5)
        assert out.by_id("a").x == 7 and out.by_id("a").y == 5

    def test_move_does_not_mutate(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 3, 4))
        build.move_to(lay, "a", 7, 5)
        assert lay.by_id("a").x == 3

    def test_move_ignores_own_footprint(self):
        """移动到自己的旧位置不算「和自己重叠」。"""
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_bunk_bed", 2, 3))
        out = build.move_to(lay, "a", 3, 3)
        assert out.by_id("a").x == 3

    def test_move_into_other_rejected(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_tea_table", 3, 4))
        lay = build.place(lay, build.PlacedItem("b", "b4_candle", 7, 4))
        with pytest.raises(ValueError) as ei:
            build.move_to(lay, "b", 4, 4)
        assert "重叠" in str(ei.value)

    def test_move_into_door_rejected(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 3, 4))
        cell = sorted(build.door_cells())[0]
        with pytest.raises(ValueError) as ei:
            build.move_to(lay, "a", cell[0], cell[1])
        assert "门口" in str(ei.value)

    def test_move_unknown_item_raises(self):
        with pytest.raises(KeyError):
            build.move_to(build.new_layout(), "nope", 1, 1)

    def test_remove(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 3, 4))
        assert build.remove(lay, "a").items == []

    def test_remove_unknown_raises(self):
        with pytest.raises(KeyError):
            build.remove(build.new_layout(), "nope")

    def test_by_id_raises_for_missing(self):
        with pytest.raises(KeyError):
            build.new_layout().by_id("nope")

    def test_nearest_legal_returns_self_when_legal(self):
        it = build.PlacedItem("a", "b4_candle", 3, 4)
        assert build.nearest_legal(build.new_layout(), it) == it

    def test_nearest_legal_finds_neighbour(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_double_bed", 2, 3))
        got = build.nearest_legal(lay, build.PlacedItem("b", "b4_double_bed", 3, 3))
        assert got is not None
        assert build.can_place(lay, got).ok

    def test_nearest_legal_returns_none_when_boxed_in(self):
        """整间房已占满 → 半径内没有合法落点，如实返回 None，不随便挑一格。"""
        lay = build.Layout(items=[], cols=2, rows=2)
        for i, cell in enumerate([(1, 1), (0, 1)]):  # 墙面带以上那两格占满
            lay = build.place(lay, build.PlacedItem(f"f{i}", "b4_pot_plant", *cell))
        got = build.nearest_legal(lay, build.PlacedItem("b", "b4_pot_plant", 1, 1))
        assert got is None

    def test_nearest_legal_is_deterministic(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_double_bed", 2, 3))
        probe = build.PlacedItem("b", "b4_double_bed", 3, 3)
        assert build.nearest_legal(lay, probe) == build.nearest_legal(lay, probe)


# ======================================================================
# 确认与评分
# ======================================================================

class TestConfirm:
    def test_empty_layout_confirm(self):
        out = build.confirm(build.new_layout())
        assert out["items"] == 0 and out["comfort"] == 0 and out["total_cost"] == 0

    def test_confirm_does_not_mutate(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 3, 4))
        before = len(lay.items)
        build.confirm(lay)
        assert len(lay.items) == before

    def test_cost_is_sum(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_double_bed", 2, 3))
        lay = build.place(lay, build.PlacedItem("b", "b4_candle", 6, 3))
        out = build.confirm(lay)
        expected = build.furniture("b4_double_bed").cost + build.furniture("b4_candle").cost
        assert out["total_cost"] == expected

    def test_comfort_is_sum(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_double_bed", 2, 3))
        assert build.confirm(lay)["comfort"] == build.furniture("b4_double_bed").comfort

    def test_counts_are_per_category_of_placed_items(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_double_bed", 2, 3))
        counts = build.confirm(lay)["counts"]
        assert counts["bed"] == 1 and counts["lamp"] == 0

    def test_counts_cover_all_categories(self):
        counts = build.confirm(build.new_layout())["counts"]
        assert set(counts) == set(build.CATEGORIES)

    def test_confirm_line_is_chinese(self):
        out = build.confirm(build.new_layout())
        assert any("一" <= ch <= "鿿" for ch in out["line"])

    def test_grade_monotonic(self):
        grades = [build.comfort_grade(c) for c in (0, 5, 15, 30, 50, 90)]
        assert len(set(grades)) == len(grades), "不同舒适度应有不同评语"

    def test_grade_boundaries(self):
        assert build.comfort_grade(0) == "还什么都没有。"
        assert build.comfort_grade(9) == "勉强能住。"
        assert build.comfort_grade(10) == "像个家了。"
        assert build.comfort_grade(100) == "这里是你自己的地方。"


# ======================================================================
# 序列化
# ======================================================================

class TestSerialization:
    def test_round_trip(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_bunk_bed", 2, 3, 90))
        back = build.from_dict(build.to_dict(lay))
        assert back.by_id("a").rotation == 90
        assert back.by_id("a").x == 2

    def test_to_dict_shape(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 3, 4))
        d = build.to_dict(lay)
        assert set(d) == {"cols", "rows", "items"}
        assert set(d["items"][0]) == {"id", "furniture_id", "x", "y", "rotation"}

    def test_json_serialisable(self):
        import json
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 3, 4))
        assert json.loads(json.dumps(build.to_dict(lay), ensure_ascii=False))["cols"] == build.ROOM_COLS

    def test_unknown_furniture_on_load_raises(self):
        with pytest.raises(KeyError):
            build.from_dict({"items": [{"id": "a", "furniture_id": "gold_toilet",
                                       "x": 0, "y": 0}]})

    def test_float_coord_on_load_raises(self):
        with pytest.raises(ValueError):
            build.from_dict({"items": [{"id": "a", "furniture_id": "b4_candle",
                                       "x": 1.5, "y": 0}]})

    def test_bool_coord_on_load_raises(self):
        with pytest.raises(ValueError):
            build.from_dict({"items": [{"id": "a", "furniture_id": "b4_candle",
                                       "x": True, "y": 0}]})

    def test_bad_rotation_on_load_raises(self):
        with pytest.raises(ValueError):
            build.from_dict({"items": [{"id": "a", "furniture_id": "b4_candle",
                                       "x": 0, "y": 0, "rotation": 45}]})


# ======================================================================
# 查询辅助
# ======================================================================

class TestQueries:
    def test_items_of_category(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 8, 4))
        lay = build.place(lay, build.PlacedItem("b", "b4_double_bed", 1, 2))
        assert [i.id for i in build.items_of(lay, "lamp")] == ["a"]
        assert [i.id for i in build.items_of(lay, "bed")] == ["b"]

    def test_category_of(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_candle", 3, 4))
        assert build.category_of(lay, "a") == "lamp"

    def test_blocked_cells_includes_door(self):
        cells = build.blocked_cells(build.new_layout())
        for d in build.door_cells():
            assert d in cells

    def test_blocked_cells_includes_occupied(self):
        lay = build.new_layout()
        lay = build.place(lay, build.PlacedItem("a", "b4_tea_table", 3, 4))
        cells = build.blocked_cells(lay)
        for c in lay.by_id("a").cells:
            assert c in cells

    def test_blocked_cells_stays_inside_room(self):
        for c in build.blocked_cells(build.new_layout()):
            assert 0 <= c[0] < build.ROOM_COLS and 0 <= c[1] < build.ROOM_ROWS

    def test_all_item_ids_unique(self):
        ids = build.all_item_ids(["b4_candle", "b4_candle", "b4_stool"])
        assert len(set(ids)) == 3

    def test_is_door_cell(self):
        cell = sorted(build.door_cells())[0]
        assert build.is_door_cell(cell) is True
        assert build.is_door_cell((0, 0)) is False

    def test_wall_cells_are_in_band(self):
        lo, hi = build.WALL_BAND
        for x, y in build.wall_cells():
            assert lo <= y <= hi
