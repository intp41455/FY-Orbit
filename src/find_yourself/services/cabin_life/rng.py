"""确定性随机源（B 包纯逻辑层公用）。

诚实原则：游戏内一切「随机」（天气、物价波动、采集产出、事件）都必须**可复算**——
同 (owner, day, 主题, 序号) 必得同一结果，否则玩家存档读回后世界会漂移，
日结算也无法被测试断言。这里用 sha256 派生，**不读系统随机源**。

与 `services/cabin_gameplay.py` 的 `_rng` 同思路，但本模块不依赖 DB，
可被纯单测直接调用。
"""

from __future__ import annotations

import hashlib
import random
from typing import Any


def seed_of(*parts: Any) -> str:
    """把任意标量拼成稳定 seed 字符串（顺序敏感，跨进程稳定）。"""
    return "|".join(str(p) for p in parts)


def rng_for(*parts: Any) -> random.Random:
    """由 seed 元组派生一个 `random.Random`（同参数 → 同序列）。"""
    digest = hashlib.sha256(seed_of(*parts).encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def pick(rng: random.Random, seq):
    """从非空序列取一个元素；空序列直接报错（不静默返回 None 伪造成功）。"""
    items = list(seq)
    if not items:
        raise ValueError("pick() 需要非空序列")
    return items[rng.randrange(len(items))]


def weighted_pick(rng: random.Random, table: dict[str, int]) -> str:
    """按权重取键。权重必须为正整数且和 > 0，否则报错（不静默兜底）。"""
    usable = {k: int(v) for k, v in table.items() if int(v) > 0}
    if not usable:
        raise ValueError("weighted_pick() 需要至少一个正权重")
    total = sum(usable.values())
    roll = rng.randrange(total)
    acc = 0
    for key, weight in usable.items():
        acc += weight
        if roll < acc:
            return key
    return next(reversed(usable))  # pragma: no cover - 浮点/边界兜底


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))