"""G1-2b 契约测试：随身小物必须真正随 mood 变化（不再是恒为 lantern 的死功能）。

历史上 HAND_ITEMS 的键是 curious/warm/steady/bright/quiet/bold，但 build_avatar 用
``HAND_ITEMS.get(portrait.mood)`` 取物，而 mood 的合法值只有 sunny/calm/melancholy
（MOOD_EYE_BIAS 的键）—— 两者永无交集，于是永远 miss、恒回退 HAND_ITEMS_DEFAULT，
「性格随身小物」功能形同虚设。

本测试守住两件事：
1. 契约：HAND_ITEMS 的键必须是 mood 白名单（MOOD_EYE_BIAS）的子集，否则必然 miss；
2. 行为：build_avatar 传入每个 mood 时，hand_item_id 真的取到对应小物（非默认），
   且 mood 缺失时回退默认 lantern。

运行：PYTHONPATH= pytest tests/unit/test_hand_items_contract.py -q
"""
from __future__ import annotations

from find_yourself.services import avatar_gen as ag
from find_yourself.services.avatar_gen import (
    HAND_ITEMS,
    HAND_ITEMS_DEFAULT,
    MOOD_EYE_BIAS,
)


def test_hand_items_keys_subset_of_mood_whitelist():
    """键必须落在 mood 合法值集合内，否则 HAND_ITEMS.get(mood) 必 miss（G1-2b 根因）。"""
    assert set(HAND_ITEMS.keys()).issubset(set(MOOD_EYE_BIAS.keys()))


def test_hand_items_activated_per_mood_not_default():
    """每个 mood 都真的选中对应小物，而不是恒回退默认。"""
    for mood, expected in HAND_ITEMS.items():
        result = ag.build_avatar({"mood": mood})
        assert result["params"]["labels"]["hand_item_id"] == expected
        assert expected != HAND_ITEMS_DEFAULT


def test_hand_items_default_without_mood():
    """mood 缺失且无 mbti 时，回退默认 lantern。"""
    result = ag.build_avatar({})
    assert result["params"]["labels"]["hand_item_id"] == HAND_ITEMS_DEFAULT
