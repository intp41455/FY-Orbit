"""B9 · 昼夜与天气（纯逻辑层）。

规则（说明书 §5-B9）：
    * 1 游戏日 = 24 游戏小时；`GameClock` 用 (day, minute) 表示，`minute ∈ [0,1440)`；
    * 天气按 (owner, theme, day) **确定性 roll**，同参数必得同结果 → 存档读回不漂移；
    * 天气对玩法有真实影响（采集产量 / 移动耗时 / 经营客源），不是纯装饰；
    * 色温 key 供 A5 光影与前端 HUD 消费（B 包只出数据，不碰渲染）。

诚实原则：
    - 天气池为空 → 抛错，不静默塞一个「晴天」假装没事；
    - 未知天气 id → 抛错，不返回默认字典让调用方以为成功。
"""

from __future__ import annotations

from dataclasses import dataclass

from .rng import clamp, weighted_pick, rng_for
from .themes import get_theme

MINUTES_PER_DAY = 1440
DAILY_RESET_MINUTE = 6 * 60  # 06:00 起床 = 新一天开始结算

#: 昼夜分段（前端 HUD 与 NPC 作息共用同一切分）
DAY_PARTS: tuple[tuple[str, int, int], ...] = (
    ("dawn", 5 * 60, 7 * 60),
    ("morning", 7 * 60, 11 * 60),
    ("noon", 11 * 60, 14 * 60),
    ("afternoon", 14 * 60, 17 * 60),
    ("dusk", 17 * 60, 20 * 60),
    ("night", 20 * 60, 24 * 60),
    ("late_night", 0, 5 * 60),
)

PART_LABELS: dict[str, str] = {
    "dawn": "清晨",
    "morning": "上午",
    "noon": "正午",
    "afternoon": "下午",
    "dusk": "傍晚",
    "night": "夜晚",
    "late_night": "深夜",
}


@dataclass(frozen=True)
class GameClock:
    """游戏内时钟。`day` 从 1 起（与 HUD「第 N 天」一致）。"""

    day: int
    minute: int

    def __post_init__(self) -> None:
        if self.day < 1:
            raise ValueError(f"day must be >= 1, got {self.day}")
        if not (0 <= self.minute < MINUTES_PER_DAY):
            raise ValueError(f"minute must be in [0,{MINUTES_PER_DAY}), got {self.minute}")

    # -- 派生 ---------------------------------------------------------
    @property
    def part(self) -> str:
        for name, lo, hi in DAY_PARTS:
            if lo <= self.minute < hi:
                return name
        raise AssertionError("unreachable: DAY_PARTS 覆盖全天")  # pragma: no cover

    @property
    def part_label(self) -> str:
        return PART_LABELS[self.part]

    @property
    def is_night(self) -> bool:
        return self.part in ("night", "late_night")

    @property
    def hour(self) -> int:
        return self.minute // 60

    def label(self, weather_label: str | None = None) -> str:
        base = f"第{self.day}天 {self.part_label}"
        return f"{base} {weather_label}" if weather_label else base

    # -- 运算 ---------------------------------------------------------
    def advanced(self, minutes: int) -> "GameClock":
        """推进若干游戏分钟，跨日自动进位。"""
        if minutes < 0:
            raise ValueError("minutes must be >= 0")
        total = (self.day - 1) * MINUTES_PER_DAY + self.minute + minutes
        return GameClock(day=total // MINUTES_PER_DAY + 1, minute=total % MINUTES_PER_DAY)

    def slept_to_next_morning(self) -> "GameClock":
        """睡觉 = 跳到次日 06:00（B5 / B11 存档触发点）。"""
        return GameClock(day=self.day + 1, minute=DAILY_RESET_MINUTE)


def day_part(minute: int) -> str:
    """给定分钟返回时段名（NPC 作息与 HUD 共用同一真源）。"""
    return GameClock(day=1, minute=minute).part


# ----------------------------------------------------------------------
# 天气
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class WeatherDef:
    id: str
    label: str
    icon: str
    #: 色温档（0=夜 1=正午），供 A5 光影 / HUD 背景色使用
    light: float
    #: 采集产量乘数（百分比，整数便于复算）
    yield_pct: int
    #: 移动/交互耗时乘数
    pace_pct: int
    #: 经营客源乘数
    demand_pct: int
    #: 是否允许户外采集（False = 全域禁止，必须有替代路径）
    blocks_gather: bool = False


WEATHERS: dict[str, WeatherDef] = {
    w.id: w
    for w in (
        WeatherDef("clear", "晴", "☀", 1.0, 100, 100, 105),
        WeatherDef("cloud", "多云", "⛅", 0.92, 100, 100, 100),
        WeatherDef("rain", "雨", "🌧", 0.78, 70, 120, 85),
        WeatherDef("fog", "雾", "🌫", 0.7, 85, 110, 92),
        WeatherDef("wind", "风", "🍃", 0.88, 95, 105, 98),
        WeatherDef("snow", "雪", "❄", 0.82, 65, 130, 80),
        WeatherDef("hail", "冰雹", "🧊", 0.74, 55, 140, 72),
        WeatherDef("starfall", "星雨", "🌠", 0.9, 130, 100, 120),
        WeatherDef("sandstorm", "沙暴", "🌪", 0.66, 45, 160, 60, blocks_gather=True),
        WeatherDef("static", "静电风暴", "📡", 0.7, 60, 135, 70, blocks_gather=True),
    )
}


def get_weather(weather_id: str) -> WeatherDef:
    try:
        return WEATHERS[weather_id]
    except KeyError as exc:  # pragma: no cover
        raise KeyError(f"unknown weather {weather_id!r}; known={sorted(WEATHERS)}") from exc


#: 天气 roll 权重（阴天系权重高于晴天，保证晴天不是 100% 必然）
WEATHER_WEIGHTS: dict[str, int] = {
    "clear": 34,
    "cloud": 22,
    "rain": 18,
    "fog": 10,
    "wind": 10,
    "snow": 8,
    "hail": 4,
    "starfall": 5,
    "sandstorm": 4,
    "static": 3,
}


def roll_weather(owner: str, theme: str, day: int) -> str:
    """按 (owner, theme, day) 确定性 roll 天气 id。

    主题只决定**候选池**（见 `themes.ThemeDef.weather_pool`），所以科幻星球
    不会出现「冰雹」以外的桃色天气，古风桃源也不会 roll 出沙暴。
    """
    if day < 1:
        raise ValueError(f"day must be >= 1, got {day}")
    pool = get_theme(theme).weather_pool
    if not pool:  # pragma: no cover - themes.py 已保证非空
        raise ValueError(f"theme {theme!r} has empty weather pool")
    table = {wid: WEATHER_WEIGHTS.get(wid, 5) for wid in pool}
    return weighted_pick(rng_for("weather", owner, theme, day), table)


def weather_of(clock: GameClock, owner: str, theme: str) -> WeatherDef:
    return get_weather(roll_weather(owner, theme, clock.day))


def scaled(base: int, pct: int) -> int:
    """把基准值按百分比缩放并夹到 ≥1（绝不缩放成 0 产出）。"""
    return max(1, (base * pct + 50) // 100)


def gather_yield(clock: GameClock, owner: str, theme: str, base_qty: int) -> int:
    """当前天气下的实际采集产量（可复算：base × yield_pct，夹到 ≥1）。"""
    return scaled(base_qty, weather_of(clock, owner, theme).yield_pct)


def gather_minutes(clock: GameClock, owner: str, theme: str, base_minutes: int) -> int:
    return scaled(base_minutes, weather_of(clock, owner, theme).pace_pct)


def demand_pct(clock: GameClock, owner: str, theme: str) -> int:
    """经营客源乘数（B8 消费；夜里商店没人来，如实下调）。"""
    w = weather_of(clock, owner, theme)
    night_factor = 60 if clock.is_night else 100
    return (w.demand_pct * night_factor + 50) // 100


def gather_blocked(clock: GameClock, owner: str, theme: str) -> bool:
    """极端天气下是否全域禁采集（沙暴 / 静电风暴）——替代路径见 tasks.B2。"""
    return weather_of(clock, owner, theme).blocks_gather


def light_level(clock: GameClock, owner: str, theme: str) -> float:
    """昼夜 × 天气综合色温（0..1），供 A5 光影层消费。"""
    minute = clock.minute
    if minute < 5 * 60:
        base = 0.12
    elif minute < 7 * 60:
        base = 0.35 + (minute - 300) / 120 * 0.35
    elif minute < 17 * 60:
        base = 0.7 + 0.3 * (1 - abs(minute - 12 * 60) / 300)
    elif minute < 20 * 60:
        base = 0.7 - (minute - 1020) / 180 * 0.45
    else:
        base = 0.25 - (minute - 1200) / 240 * 0.15
    base = clamp(int(base * 100), 0, 100) / 100
    return round(base * weather_of(clock, owner, theme).light, 3)