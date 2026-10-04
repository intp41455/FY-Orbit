"""G1-2 契约测试：前端微调选项必须是后端白名单的**子集**。

历史上前端 TUNING_OPTIONS 混入了 14 个后端白名单外的非法值（tea/honey/wheat/
silver、tunic/jacket/suit/cape_outfit、frown/smirn、round/sleepy/wink/wide），
提交会被后端 422 拒，前端却照常展示 —— 用户调到这些项就卡死。

本测试直接读 web/src/pages/AvatarWorkshopPage.tsx，解析 TUNING_OPTIONS 字面量，
断言每个分类的每一项都落在 avatar_gen.py 对应的枚举元组里。后端改枚举或前端
手滑写错值时，测试立刻红，杜绝再次漂移。

运行：PYTHONPATH= pytest tests/unit/test_tuning_options_contract.py -q
"""
from __future__ import annotations

import re
from pathlib import Path

from find_yourself.services.avatar_gen import (
    EYE_STYLES,
    HAIR_STYLES,
    HAIR_TONES,
    MOUTH_STYLES,
    OUTFITS,
)

ROOT = Path(__file__).resolve().parents[2]
TSX = ROOT / "web" / "src" / "pages" / "AvatarWorkshopPage.tsx"

# 前端键 → 后端白名单元组
BACKEND_WHITELIST = {
    "hair_style": HAIR_STYLES,
    "hair_tone": HAIR_TONES,
    "outfit": OUTFITS,
    "mouth": MOUTH_STYLES,
    "eye": EYE_STYLES,
}


def _read_tuning_options() -> dict[str, list[str]]:
    text = TSX.read_text(encoding="utf-8")
    # 只抓 const TUNING_OPTIONS = { ... } 这段
    m = re.search(r"const TUNING_OPTIONS\s*=\s*\{(.*?)\}\s*as const;", text, re.S)
    if not m:
        raise AssertionError("未能在 AvatarWorkshopPage.tsx 中找到 TUNING_OPTIONS 定义")
    block = m.group(1)
    result: dict[str, list[str]] = {}
    for key in BACKEND_WHITELIST:
        km = re.search(rf"{key}:\s*\[(.*?)\]", block, re.S)
        assert km is not None, f"TUNING_OPTIONS 缺少分类 {key}"
        values = re.findall(r"'([^']+)'", km.group(1))
        result[key] = values
    return result


def test_tuning_options_exist_and_match_backend_counts():
    """每个分类的选项数必须与后端枚举数一致（不多不少）。"""
    opts = _read_tuning_options()
    for key, allowed in BACKEND_WHITELIST.items():
        assert len(opts[key]) == len(allowed), (
            f"{key} 前端 {len(opts[key])} 项 ≠ 后端 {len(allowed)} 项"
        )


def test_tuning_options_subset_of_backend_whitelist():
    """每个前端选项都必须在后端白名单里（非法值会被后端 422）。"""
    opts = _read_tuning_options()
    for key, allowed in BACKEND_WHITELIST.items():
        allowed_set = set(allowed)
        for v in opts[key]:
            assert v in allowed_set, f"{key} 含后端白名单外的非法值：{v!r}"
