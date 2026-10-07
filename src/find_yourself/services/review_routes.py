"""P9 · 双路线策略：白盒为主 + Computer Use 兜底（A-路线-03）。

需求现场
--------

「点哪评哪」要让 AI 能精确改到用户点的那一处。有两条技术路线：

* **白盒路线（主）** —— 靠 DOM：选择器 / 结构路径 / 指纹精确定位到组件，
  改动指令可被代码层直接消费（执行 Agent 拿到选择器就能改文件）。
* **Computer Use 兜底** —— 白盒定位不到时（跨 iframe、Canvas 绘制区、第三方
  嵌入式组件），退化为看图点击：给出归一化坐标 + 截图引用，让人/模型按图操作。

**主从关系是硬约束**：白盒能定位就必须走白盒；Computer Use 只在白盒**明确
失败**时启用，且必须记录失败原因。不允许「图省事直接上 Computer Use」——
那会丢掉可复现的结构定位，把精确修改退化成碰运气点击。

设计约束（违反即返工）
----------------------

1. **不静默降级**——每一次从白盒切到 Computer Use 都必须带 ``reason``；
   无理由的切换被拒（``ValidationFailed``）。
2. **兜底要可复原**——Computer Use 的坐标必须归一化（0~1），并记录视口尺寸，
   换分辨率仍能定位。
3. **诚实边界**：本模块只做**策略决策与载荷构造**，不真的驱动浏览器/鼠标；
   真实 Computer Use 执行属 BLOCKED_EXTERNAL（需外部执行器接入）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import ValidationFailed
from .review_bridge import (
    MODE_DOM,
    MODE_FREEHAND,
    MODE_REGION,
    ROUTE_COMPUTER_USE,
    ROUTE_WHITEBOX,
)

#: 白盒定位失败的原因分类（切换 Computer Use 必须给出其中之一）
WHY_NO_SELECTOR = "no_selector"            # 目标无稳定选择器（Canvas/绘图区）
WHY_CROSS_ORIGIN = "cross_origin_iframe"   # 跨域 iframe，DOM 不可达
WHY_SHADOW_DOM = "closed_shadow_root"      # 闭合 Shadow DOM，结构不可见
WHY_THIRD_PARTY = "third_party_embed"      # 第三方嵌入组件，无源码控制
FALLBACK_REASONS = (
    WHY_NO_SELECTOR, WHY_CROSS_ORIGIN, WHY_SHADOW_DOM, WHY_THIRD_PARTY,
)


@dataclass
class Locator:
    """一次「要点哪」的定位输入（前端采集，交给策略决策）。"""

    mode: str = MODE_DOM
    selector: str = ""
    dom_path: list[str] = field(default_factory=list)
    tag: str = ""
    #: 归一化坐标（Computer Use 兜底 / 圈选 / 画笔都用它）
    region: dict = field(default_factory=dict)
    #: 视口尺寸（记录像素上下文，便于需要时换算）
    viewport: dict = field(default_factory=dict)
    #: 元素是否在可达 DOM 内（跨域 iframe / 闭合 shadow 时为 False）
    dom_reachable: bool = True


@dataclass
class RouteDecision:
    """策略决策结果（A-路线-03）。"""

    route: str
    reason: str = ""
    #: 执行载荷：白盒给选择器/路径；兜底给归一化坐标 + 视口
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_fallback(self) -> bool:
        return self.route == ROUTE_COMPUTER_USE

    def to_public(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "reason": self.reason,
            "is_fallback": self.is_fallback,
            "payload": self.payload,
        }


def decide_route(locator: Locator, *, fallback_reason: str | None = None) -> RouteDecision:
    """决定走白盒还是 Computer Use 兜底（A-路线-03 的核心策略）。

    规则（**白盒优先**）：

    1. DOM 可达 **且** 有稳定选择器 → **白盒**（直接给选择器）。
    2. DOM 可达但无选择器（Canvas / 绘图区）→ 兜底，``why=no_selector``。
    3. DOM 不可达（跨域 iframe / 闭合 shadow）→ 兜底，**必须由调用方给出
       ``fallback_reason``**（``cross_origin_iframe`` / ``closed_shadow_root``）；
       无理由则拒绝切换。
    4. 兜底载荷必须含合法归一化 ``region``；缺失则拒绝（不可复原的兜底无意义）。
    """
    if locator.mode not in (MODE_DOM, MODE_REGION, MODE_FREEHAND):
        raise ValidationFailed("invalid_mode", "定位方式非法")

    # 圈选 / 画笔本身就是「看图」，天然走 Computer Use 兜底
    if locator.mode in (MODE_REGION, MODE_FREEHAND):
        _require_region(locator)
        return RouteDecision(
            route=ROUTE_COMPUTER_USE,
            reason=WHY_NO_SELECTOR,
            payload=_visual_payload(locator),
        )

    # DOM 点选：可达且有选择器 → 白盒（主路线）
    if locator.dom_reachable and locator.selector:
        return RouteDecision(
            route=ROUTE_WHITEBOX,
            reason="",
            payload={
                "selector": locator.selector,
                "dom_path": list(locator.dom_path),
                "tag": locator.tag,
            },
        )

    # 走到这里说明白盒不可用 —— 必须给出理由，且载荷必须可复原
    if not fallback_reason:
        raise ValidationFailed(
            "fallback_reason_required",
            "白盒定位不可用，启用 Computer Use 兜底必须给出 reason"
            f"（{FALLBACK_REASONS} 之一）——A-路线-03 不允许静默降级",
        )
    if fallback_reason not in FALLBACK_REASONS:
        raise ValidationFailed(
            "invalid_fallback_reason",
            f"fallback_reason 必须是 {FALLBACK_REASONS} 之一",
        )
    _require_region(locator)
    return RouteDecision(
        route=ROUTE_COMPUTER_USE,
        reason=fallback_reason,
        payload=_visual_payload(locator),
    )


def _require_region(locator: Locator) -> None:
    r = locator.region or {}
    try:
        x, y = float(r["x"]), float(r["y"])
        w, h = float(r["w"]), float(r["h"])
    except (KeyError, TypeError, ValueError):
        raise ValidationFailed(
            "region_required_for_fallback",
            "Computer Use 兜底必须携带归一化区域 {x,y,w,h}（0~1）——"
            "无坐标的兜底无法复原（A-路线-03）",
        ) from None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
        raise ValidationFailed(
            "region_out_of_range", "归一化坐标必须落在 0~1 且 w,h > 0",
        )


def _visual_payload(locator: Locator) -> dict[str, Any]:
    return {
        "region": dict(locator.region),
        "viewport": dict(locator.viewport),
        "hint_tag": locator.tag,
    }


def summarize_routes(decisions: list[RouteDecision]) -> dict[str, Any]:
    """统计一组定位决策的主/兜底比例——**可观测性**，防止兜底被滥用。

    若 ``fallback`` 占比异常高（>50%），说明白盒定位能力有系统性缺口，
    应回头补选择器而不是继续扩大兜底。
    """
    total = len(decisions)
    fallback = sum(1 for d in decisions if d.is_fallback)
    return {
        "total": total,
        "whitebox": total - fallback,
        "fallback": fallback,
        "fallback_ratio": (fallback / total) if total else 0.0,
        "needs_review": total > 0 and fallback / total > 0.5,
    }
