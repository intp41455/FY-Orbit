"""包6 · A-命理画像-02 · charts 画像 → 性格维度 → 游戏角色映射单测。

全部纯函数、确定性：同画像恒同映射；缺项走中性默认 50 并如实记 advisory。
"""

from __future__ import annotations

from types import SimpleNamespace

from find_yourself.services.avatar_profile import (
    ARCHETYPES,
    PERSONALITY_DIMENSIONS,
    chart_to_portrait,
    personality_dimensions,
)
from find_yourself.services.errors import ValidationFailed


BAZI_CHART = SimpleNamespace(
    system="bazi",
    computed_data={
        "system": "bazi",
        "day_master": "庚",
        "day_master_element": "金",
        "pillars": {},
        "element_distribution": {"木": 1, "火": 1, "土": 1, "金": 2, "水": 1},
    },
)

WESTERN_CHART = SimpleNamespace(
    system="western",
    computed_data={
        "system": "western",
        "planets": {
            "Sun": {"sign": "狮子座 (Leo)"},
            "Moon": {"sign": "处女座 (Virgo)"},
        },
        "ascendant": {"sign": "射手座 (Sagittarius)"},
    },
)


def test_chart_to_portrait_bazi_extracts_day_master_element():
    portrait = chart_to_portrait(BAZI_CHART)
    assert portrait["bazi_day_master"] == "庚"
    assert portrait["bazi_element"] == "金"
    assert set(portrait) <= {"bazi_day_master", "bazi_element", "mbti", "sun_sign", "moon_sign", "asc_sign"}


def test_chart_to_portrait_western_extracts_signs():
    portrait = chart_to_portrait(WESTERN_CHART)
    assert portrait["sun_sign"] == "狮子座 (Leo)"
    assert portrait["moon_sign"] == "处女座 (Virgo)"
    assert portrait["asc_sign"] == "射手座 (Sagittarius)"


def test_chart_to_portrait_skips_missing_fields():
    empty = SimpleNamespace(system="bazi", computed_data={})
    assert chart_to_portrait(empty) == {}
    external = SimpleNamespace(system="ziwei", computed_data={"pillars": {}})
    assert chart_to_portrait(external) == {}


def test_personality_dimensions_is_deterministic():
    portrait = {"mbti": "ENTP", "bazi_element": "火", "sun_sign": "aries"}
    a = personality_dimensions(portrait)
    b = personality_dimensions(portrait)
    assert a == b


def test_personality_dimensions_full_schema():
    out = personality_dimensions({"mbti": "INTJ", "bazi_element": "水"})
    assert set(out["dimensions"]) == {k for k, _ in PERSONALITY_DIMENSIONS}
    assert set(out["element_balance"]) == {"wood", "fire", "earth", "metal", "water"}
    assert all(5.0 <= v <= 95.0 for v in out["dimensions"].values())
    assert out["dominant_element"] == "water"
    assert out["dominant_element_label"] == "水"
    assert out["mapping_version"] == "1"


def test_mbti_extrovert_vs_introvert_diverge():
    extro = personality_dimensions({"mbti": "ESTP"})["dimensions"]["extraversion"]
    intro = personality_dimensions({"mbti": "ISTP"})["dimensions"]["extraversion"]
    assert extro > intro
    assert extro > 50.0 > intro


def test_bazi_element_shifts_dominant_element():
    fire = personality_dimensions({"bazi_element": "火"})
    water = personality_dimensions({"bazi_element": "水"})
    assert fire["dominant_element"] == "fire"
    assert water["dominant_element"] == "water"
    assert fire["dimensions"]["extraversion"] > water["dimensions"]["extraversion"]


def test_missing_inputs_are_neutral_and_reported():
    out = personality_dimensions({})
    assert all(v == 50.0 for v in out["dimensions"].values())
    assert "missing:bazi_element" in out["advisory"]
    assert "missing:western_signs" in out["advisory"]
    assert out["role_mapping"]["archetype_key"] == "wanderer"


def test_invalid_mbti_is_reported_not_corrected():
    out = personality_dimensions({"mbti": "XYZP"})
    assert "mbti_format_invalid" in out["advisory"]
    assert out["dimensions"]["extraversion"] == 50.0


def test_role_mapping_archetype_and_playstyle():
    mage = personality_dimensions({"mbti": "INFP", "bazi_element": "水"})
    assert mage["role_mapping"]["archetype"] == ARCHETYPES["mage"]["name"]
    assert mage["role_mapping"]["archetype_key"] == "mage"
    playstyle = mage["role_mapping"]["playstyle"]
    assert set(playstyle) == {"combat", "support", "crafting", "survival", "exploration"}
    assert playstyle["exploration"] == mage["dimensions"]["openness"]
    assert mage["role_mapping"]["party_role"] == ARCHETYPES["mage"]["party_role"]


def test_archetype_table_is_complete():
    for key, entry in ARCHETYPES.items():
        assert set(entry) == {"name", "party_role", "growth_hint"}
        assert entry["name"] and entry["party_role"] and entry["growth_hint"]


def test_western_signs_contribute_to_element_balance():
    out = personality_dimensions({"sun_sign": "狮子座 (Leo)", "moon_sign": "virgo"})
    assert out["element_balance"]["fire"] > 20.0  # 狮子座 → 火
    assert out["element_balance"]["earth"] > 20.0  # 处女座 → 土
