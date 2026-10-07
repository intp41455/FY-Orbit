"""P9 单测 · 双路线策略（A-路线-03）。

覆盖：白盒优先、白盒不可用时 Computer Use 兜底、兜底**必须给理由**、
坐标归一化校验、兜底占比可观测。
"""

from __future__ import annotations

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.review_bridge import (
    MODE_DOM,
    MODE_FREEHAND,
    MODE_REGION,
    ROUTE_COMPUTER_USE,
    ROUTE_WHITEBOX,
)
from find_yourself.services.review_routes import (
    WHY_CROSS_ORIGIN,
    WHY_NO_SELECTOR,
    Locator,
    decide_route,
    summarize_routes,
)

REGION = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}


# --------------------------------------------------------------------------- #
# 白盒为主
# --------------------------------------------------------------------------- #


def test_dom_reachable_with_selector_uses_whitebox():
    """★ 主路线：白盒能定位就必须走白盒（不允许图省事上兜底）。"""
    d = decide_route(Locator(
        mode=MODE_DOM, selector="body > div > button", tag="button",
        dom_path=["body", "div", "button"],
    ))
    assert d.route == ROUTE_WHITEBOX
    assert d.is_fallback is False
    assert d.payload["selector"] == "body > div > button"
    assert d.reason == ""


def test_whitebox_payload_carries_dom_path_for_agent():
    d = decide_route(Locator(mode=MODE_DOM, selector="#x", dom_path=["body", "main"]))
    assert d.payload["dom_path"] == ["body", "main"]


# --------------------------------------------------------------------------- #
# Computer Use 兜底
# --------------------------------------------------------------------------- #


def test_dom_unreachable_requires_reason():
    """★ 硬约束：白盒不可用时不给理由 → 拒绝（禁止静默降级）。"""
    with pytest.raises(ValidationFailed) as e:
        decide_route(Locator(mode=MODE_DOM, selector="", dom_reachable=False,
                             region=REGION))
    assert "reason" in str(e.value).lower() or "兜底" in str(e.value)


def test_dom_unreachable_with_reason_falls_back():
    d = decide_route(
        Locator(mode=MODE_DOM, dom_reachable=False, region=REGION,
                viewport={"w": 1440, "h": 900}),
        fallback_reason=WHY_CROSS_ORIGIN,
    )
    assert d.route == ROUTE_COMPUTER_USE
    assert d.is_fallback is True
    assert d.reason == WHY_CROSS_ORIGIN
    assert d.payload["region"] == REGION
    assert d.payload["viewport"] == {"w": 1440, "h": 900}


def test_dom_reachable_but_no_selector_falls_back_with_reason():
    """Canvas / 绘图区：DOM 可达但无选择器 → 兜底。"""
    d = decide_route(
        Locator(mode=MODE_DOM, selector="", dom_reachable=True, region=REGION),
        fallback_reason=WHY_NO_SELECTOR,
    )
    assert d.is_fallback


def test_invalid_fallback_reason_rejected():
    with pytest.raises(ValidationFailed):
        decide_route(Locator(mode=MODE_DOM, dom_reachable=False, region=REGION),
                     fallback_reason="because")


def test_fallback_without_region_rejected():
    """★ 兜底必须可复原：没有归一化坐标的兜底无意义。"""
    with pytest.raises(ValidationFailed):
        decide_route(Locator(mode=MODE_DOM, dom_reachable=False, region={}),
                     fallback_reason=WHY_CROSS_ORIGIN)


def test_fallback_region_out_of_range_rejected():
    with pytest.raises(ValidationFailed):
        decide_route(
            Locator(mode=MODE_DOM, dom_reachable=False,
                    region={"x": 1.4, "y": 0, "w": 0.2, "h": 0.2}),
            fallback_reason=WHY_CROSS_ORIGIN,
        )


# --------------------------------------------------------------------------- #
# 圈选 / 画笔天然走兜底
# --------------------------------------------------------------------------- #


def test_region_mode_goes_computer_use():
    d = decide_route(Locator(mode=MODE_REGION, region=REGION))
    assert d.route == ROUTE_COMPUTER_USE
    assert d.reason == WHY_NO_SELECTOR


def test_freehand_mode_goes_computer_use():
    d = decide_route(Locator(mode=MODE_FREEHAND, region=REGION))
    assert d.route == ROUTE_COMPUTER_USE


def test_region_mode_without_region_rejected():
    with pytest.raises(ValidationFailed):
        decide_route(Locator(mode=MODE_REGION, region={}))


# --------------------------------------------------------------------------- #
# 可观测性：兜底占比
# --------------------------------------------------------------------------- #


def test_summarize_routes_flags_overuse():
    decisions = [
        decide_route(Locator(mode=MODE_DOM, selector="#a")),          # 白盒
        decide_route(Locator(mode=MODE_REGION, region=REGION)),        # 兜底
        decide_route(Locator(mode=MODE_REGION, region=REGION)),        # 兜底
    ]
    s = summarize_routes(decisions)
    assert s["total"] == 3
    assert s["whitebox"] == 1
    assert s["fallback"] == 2
    assert s["needs_review"] is True  # >50% 兜底 → 提示回头补选择器


def test_summarize_routes_healthy():
    decisions = [
        decide_route(Locator(mode=MODE_DOM, selector=f"#a{i}")) for i in range(4)
    ] + [decide_route(Locator(mode=MODE_REGION, region=REGION))]
    s = summarize_routes(decisions)
    assert s["needs_review"] is False


def test_summarize_routes_empty():
    s = summarize_routes([])
    assert s["total"] == 0
    assert s["fallback_ratio"] == 0.0
    assert s["needs_review"] is False
