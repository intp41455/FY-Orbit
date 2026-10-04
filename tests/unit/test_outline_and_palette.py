"""G5-2 描边体系重构 + 调色板下限 契约测试。

验收点（任务书 7.4）：
  - 🔴 描边左右两侧对称（from_left=True 时右侧也画）
  - 8 层色键数与 MIN_PALETTE_COLORS 一致（此处断言整角色字符色板 ≥ 下限）
  - 8 层每层都有填充（实测矩阵填充率 > 0）
"""
from find_yourself.services import avatar_gen as ag

EXPECTED_LAYERS = [
    "shadow", "body", "hair", "face",
    "outfit", "accessory", "hand_item", "outline",
]


def _rect_block() -> list[str]:
    """一个居中实心矩形剪影（宽 8，x=8..15，y=10..19）。"""
    block = ["." * ag.AVATAR_WIDTH for _ in range(ag.AVATAR_HEIGHT)]
    for y in range(10, 20):
        row = list(block[y])
        for x in range(8, 16):
            row[x] = "X"
        block[y] = "".join(row)
    return block


def _has_side(outline: list[str], outside_x: int) -> bool:
    return any(row[outside_x] == "x" for row in outline)


def test_outline_drawn_on_both_sides_when_from_left():
    """from_left=True 时，左侧（x=7）与右侧（x=16）转折点都应有描边。"""
    out = ag._draw_outline([_rect_block()], True)
    assert _has_side(out, 7), "左侧轮廓缺失"
    assert _has_side(out, 16), "右侧轮廓缺失（G5-2：from_left=True 时右侧也画）"


def test_outline_symmetric_regardless_of_from_left():
    """无论受光方向，左右两侧都应有描边像素（对称契约）。"""
    for flag in (True, False):
        out = ag._draw_outline([_rect_block()], flag)
        assert _has_side(out, 7), f"from_left={flag} 时左侧轮廓缺失"
        assert _has_side(out, 16), f"from_left={flag} 时右侧轮廓缺失"


def test_outline_is_valid_boundary():
    """每个描边像素都必须与实体 4-邻域相邻（是真实边界，不是浮空/亮条）。"""
    out = ag._draw_outline([_rect_block()], True)
    block = _rect_block()
    for y in range(ag.AVATAR_HEIGHT):
        for x in range(ag.AVATAR_WIDTH):
            if out[y][x] != "x":
                continue
            adjacent = (
                (x > 0 and block[y][x - 1] != ".")
                or (x < ag.AVATAR_WIDTH - 1 and block[y][x + 1] != ".")
                or (y > 0 and block[y - 1][x] != ".")
                or (y < ag.AVATAR_HEIGHT - 1 and block[y + 1][x] != ".")
            )
            assert adjacent, f"描边像素 ({x},{y}) 未贴合实体，疑似浮空"


def test_char_palette_meets_minimum():
    """角色字符色板键数 ≥ MIN_PALETTE_COLORS（调色板下限契约）。"""
    params = ag.build_avatar({"mood": "calm", "hair_style": "short_neat", "outfit": "knit"})
    palette = params["char_palette"]
    assert len(palette) >= ag.MIN_PALETTE_COLORS


def test_compose_layers_all_eight_nonempty():
    """8 层每层都应有填充（实测矩阵填充率 > 0）。"""
    params = ag.build_avatar({"mood": "calm"})
    layers = params["layers"]
    assert set(layers.keys()) == set(EXPECTED_LAYERS), "8 层名称与契约不一致"
    for name in EXPECTED_LAYERS:
        opaque = sum(1 for row in layers[name] for ch in row if ch != ".")
        assert opaque > 0, f"层 {name} 填充率为 0（应非空）"
