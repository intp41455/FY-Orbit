"""确定性天文计算（F04：排盘不得用固定近似冒充真实计算）。

纯标准库、无星历表、无网络、无随机。精度口径（诚实标注，不冒充 JPL）：
* 太阳视黄经：NOAA 级展开式，误差约 0.01° —— 节气时刻定位到分钟级；
* 月亮黄经：Meeus 低精度月球理论（含 6 项主要摄动），误差约 0.1°；
* 水星~土星黄经：Meeus 低精度开普勒解（J2000 平根数 + 百年变率），
  误差约 0.1°~1° —— 足以判定星座/冲合位置，**不足以做角秒级占星推运**；
* 上升点：按黄道交点公式由本地恒星时 + 纬度真算（此前版本用
  ``(LMST+90)°`` 固定偏移，属虚构，已删除）。

所有函数都是纯函数：同一儒略日输入永远得到同一输出（确定性引擎要求）。
"""
from __future__ import annotations

import datetime
import math

#: 24 节气对应的太阳视黄经（度）。月柱只用其中 12 个「节」。
SOLAR_TERMS: tuple[tuple[str, float], ...] = (
    ("立春", 315.0), ("雨水", 330.0), ("惊蛰", 345.0), ("春分", 0.0),
    ("清明", 15.0), ("谷雨", 30.0), ("立夏", 45.0), ("小满", 60.0),
    ("芒种", 75.0), ("夏至", 90.0), ("小暑", 105.0), ("大暑", 120.0),
    ("立秋", 135.0), ("处暑", 150.0), ("白露", 165.0), ("秋分", 180.0),
    ("寒露", 195.0), ("霜降", 210.0), ("立冬", 225.0), ("小雪", 240.0),
    ("大雪", 255.0), ("冬至", 270.0), ("小寒", 285.0), ("大寒", 300.0),
)

#: 月柱对应的 12 节（立春=寅月起，顺序即地支序）。
MONTH_JIE: tuple[tuple[str, float], ...] = (
    ("立春", 315.0), ("惊蛰", 345.0), ("清明", 15.0), ("立夏", 45.0),
    ("芒种", 75.0), ("小暑", 105.0), ("立秋", 135.0), ("白露", 165.0),
    ("寒露", 195.0), ("立冬", 225.0), ("大雪", 255.0), ("小寒", 285.0),
)

#: 行星 J2000 平根数 + 百年变率（Meeus《Astronomical Algorithms》附表）：
#: (a[AU], e, i[°], L[°], ϖ[°], Ω[°])，变率同序。
_PLANET_ELEMENTS: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {
    "Mercury": (
        (0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593),
        (0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081),
    ),
    "Venus": (
        (0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
        (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418),
    ),
    "Earth": (
        (1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
        (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0),
    ),
    "Mars": (
        (1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
        (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343),
    ),
    "Jupiter": (
        (5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
        (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106),
    ),
    "Saturn": (
        (9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
        (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794),
    ),
}


def to_julian_day(dt_utc: datetime.datetime) -> float:
    """UTC datetime → 儒略日（与引擎既有 ``_to_julian_day`` 同算法）。"""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=datetime.timezone.utc)
    else:
        dt_utc = dt_utc.astimezone(datetime.timezone.utc)
    year, month = dt_utc.year, dt_utc.month
    day_frac = (dt_utc.day + (dt_utc.hour + (dt_utc.minute + dt_utc.second / 60.0)
                / 60.0) / 24.0)
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (year + 4716))
            + math.floor(30.6001 * (month + 1)) + day_frac + b - 1524.5)


def sun_apparent_longitude(jd: float) -> float:
    """太阳视黄经（度，0~360）。NOAA 级展开，误差约 0.01°。"""
    t = (jd - 2451545.0) / 36525.0
    l0 = 280.46646 + 36000.76983 * t
    m = math.radians((357.52911 + 35999.05029 * t) % 360.0)
    c = ((1.914602 - 0.004817 * t) * math.sin(m)
         + (0.019993 - 0.000101 * t) * math.sin(2.0 * m))
    return (l0 + c) % 360.0


def _ang_diff(a: float, target: float) -> float:
    """a - target 折到 (-180, 180]。"""
    return (a - target + 540.0) % 360.0 - 180.0


def solar_term_moment_utc(year: int, longitude: float) -> datetime.datetime:
    """某年太阳视黄经首次到达 ``longitude`` 的 UTC 时刻（二分逼近，秒级）。

    太阳黄经年内单调递增（~1°/天），从 1 月 1 日逐日扫描定出包络区间，
    再二分到 1e-4 天（约 9 秒）—— 确定性、无迭代发散风险。
    """
    start = datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc)
    jd_day = to_julian_day(start)
    prev_jd, prev_d = jd_day, _ang_diff(sun_apparent_longitude(jd_day), longitude)
    for _ in range(370):
        jd_day += 1.0
        d = _ang_diff(sun_apparent_longitude(jd_day), longitude)
        if prev_d <= 0.0 < d:
            lo, hi = prev_jd, jd_day
            for _ in range(40):
                mid = (lo + hi) / 2.0
                if _ang_diff(sun_apparent_longitude(mid), longitude) <= 0.0:
                    lo = mid
                else:
                    hi = mid
            return (datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
                    + datetime.timedelta(days=(lo + hi) / 2.0 - 2440587.5))
        prev_jd, prev_d = jd_day, d
    raise ValueError(f"年份 {year} 内未找到黄经 {longitude}°（不应发生）")


def moon_longitude(jd: float) -> float:
    """月亮地心视黄经（度）。Meeus 低精度（含 6 项主要摄动），误差约 0.1°。"""
    t = (jd - 2451545.0) / 36525.0
    lp = 218.3164477 + 481267.88123421 * t
    d = 297.8501921 + 445267.1114034 * t
    m = 357.5291092 + 35999.0502909 * t
    mp = 134.9633964 + 477198.8675055 * t
    f = 93.2720950 + 483202.0175233 * t
    lon = (lp
           + 6.289 * math.sin(math.radians(mp % 360.0))
           + 1.274 * math.sin(math.radians((2 * d - mp) % 360.0))
           + 0.658 * math.sin(math.radians((2 * d) % 360.0))
           + 0.214 * math.sin(math.radians((2 * mp) % 360.0))
           - 0.186 * math.sin(math.radians(m % 360.0))
           - 0.114 * math.sin(math.radians((2 * f) % 360.0)))
    return lon % 360.0


def _heliocentric(jd: float, name: str) -> tuple[float, float, float]:
    """行星日心黄道直角坐标（AU）。开普勒方程牛顿迭代解偏近点角。"""
    t = (jd - 2451545.0) / 36525.0
    (a0, e0, i0, l0, p0, n0), (da, de, di, dl, dp, dn) = _PLANET_ELEMENTS[name]
    a = a0 + da * t
    e = e0 + de * t
    inc = math.radians(i0 + di * t)
    l = math.radians((l0 + dl * t) % 360.0)
    long_peri = math.radians((p0 + dp * t) % 360.0)
    node = math.radians((n0 + dn * t) % 360.0)
    m = (l - long_peri) % (2.0 * math.pi)
    # 牛顿迭代（e<0.21 全收敛；初值 M 足够）。
    ecc_anom = m
    for _ in range(12):
        ecc_anom -= (ecc_anom - e * math.sin(ecc_anom) - m) / (
            1.0 - e * math.cos(ecc_anom))
    xp = a * (math.cos(ecc_anom) - e)
    yp = a * math.sqrt(1.0 - e * e) * math.sin(ecc_anom)
    v = math.atan2(yp, xp)
    r = math.hypot(xp, yp)
    arg = v + long_peri - node
    x = r * (math.cos(node) * math.cos(arg)
             - math.sin(node) * math.sin(arg) * math.cos(inc))
    y = r * (math.sin(node) * math.cos(arg)
             + math.cos(node) * math.sin(arg) * math.cos(inc))
    z = r * math.sin(arg) * math.sin(inc)
    return x, y, z


def planet_longitude(jd: float, name: str) -> float:
    """行星地心视黄经（度，水星~土星）。低精度理论，误差约 0.1°~1°。"""
    if name == "Earth":
        raise ValueError("Earth 没有地心黄经（用 sun 反查：地心太阳即日心地球反向）")
    x, y, z = _heliocentric(jd, name)
    ex, ey, ez = _heliocentric(jd, "Earth")
    lon = math.degrees(math.atan2(y - ey, x - ex)) % 360.0
    return lon


def elongation_deg(lon1: float, lon2: float) -> float:
    """两黄经的距角（0~180°）。"""
    return abs(_ang_diff(lon1, lon2))


def ascendant(jd: float, longitude_deg: float, latitude_deg: float) -> float:
    """上升点黄经（度）。由 GMST→本地恒星时 + 黄赤交角真算，需地理纬度。"""
    t = (jd - 2451545.0) / 36525.0
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360.0
    ramc = math.radians((gmst + longitude_deg) % 360.0)
    eps = math.radians(23.4392911 - 0.0130042 * t)
    lat = math.radians(latitude_deg)
    asc = math.atan2(math.cos(ramc),
                     -(math.sin(ramc) * math.cos(eps)
                       + math.tan(lat) * math.sin(eps)))
    return math.degrees(asc) % 360.0
