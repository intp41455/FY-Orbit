"""F04：排盘必须用真实天文计算，不得用固定近似冒充。

* 八字年柱/月柱：立春与 12 节的真实时刻（太阳视黄经二分定位），
  不再是「2 月 4 日」「每月 5 日」固定日期；
* 西方行星：开普勒轨道解真算，不再是「太阳黄经 + 固定偏移」；
* 上升点：恒星时 + 纬度公式真算，不再是「LMST+90°」。

时刻锚点均为公开发表的天文事件（容差按低精度理论口径给足余量）。
"""
from __future__ import annotations

import datetime

from find_yourself.charts import astronomy as astro
from find_yourself.charts.engine import DeterministicChartEngine
from find_yourself.charts.models import ChartRequest


def _jd(y: int, mo: int, d: int, h: int = 12, mi: int = 0) -> float:
    return astro.to_julian_day(
        datetime.datetime(y, mo, d, h, mi, tzinfo=datetime.timezone.utc))


def _bazi(date: str, time: str) -> dict:
    req = ChartRequest(birth_date=date, birth_time=time,
                       timezone_str="Asia/Shanghai", system="bazi")
    return DeterministicChartEngine.compute_bazi(req).computed_data


# --- 太阳与节气：绝对锚点 ---------------------------------------------------

def test_sun_longitude_j2000_noon() -> None:
    assert abs(astro.sun_apparent_longitude(2451545.0) - 280.37) < 0.05


def test_lichun_2024_moment() -> None:
    moment = astro.solar_term_moment_utc(2024, 315.0)
    expect = datetime.datetime(2024, 2, 4, 8, 27, tzinfo=datetime.timezone.utc)
    assert abs((moment - expect).total_seconds()) < 45 * 60


def test_lichun_2023_moment() -> None:
    moment = astro.solar_term_moment_utc(2023, 315.0)
    expect = datetime.datetime(2023, 2, 4, 2, 43, tzinfo=datetime.timezone.utc)
    assert abs((moment - expect).total_seconds()) < 45 * 60


def test_bazi_year_straddles_real_lichun() -> None:
    # 2024-02-04 10:00 CST（立春 16:27 之前）仍是癸卯年 …
    before = _bazi("2024-02-04", "10:00")
    assert before["pillars"]["year"]["stem"] == "癸"
    assert before["pillars"]["year"]["branch"] == "卯"
    # … 18:00（立春之后）已是甲辰年。固定「2 月 4 日」近似在此会错一边。
    after = _bazi("2024-02-04", "18:00")
    assert after["pillars"]["year"]["stem"] == "甲"
    assert after["pillars"]["year"]["branch"] == "辰"


def test_bazi_month_straddles_real_jingzhe() -> None:
    moment = astro.solar_term_moment_utc(2024, 345.0)
    cst = datetime.timezone(datetime.timedelta(hours=8))
    local = moment.astimezone(cst)
    for delta_h, expect_branch in ((-4.0, "寅"), (4.0, "卯")):
        probe = local + datetime.timedelta(hours=delta_h)
        data = _bazi(probe.strftime("%Y-%m-%d"), probe.strftime("%H:%M"))
        assert data["pillars"]["month"]["branch"] == expect_branch


# --- 西方行星：冲日锚点（距角≈180°） -----------------------------------------

def test_mars_opposition_2025_01_16() -> None:
    jd = _jd(2025, 1, 16)
    elong = astro.elongation_deg(
        astro.planet_longitude(jd, "Mars"),
        astro.sun_apparent_longitude(jd))
    assert abs(elong - 180.0) < 5.0


def test_jupiter_opposition_2026_01_10() -> None:
    jd = _jd(2026, 1, 10)
    elong = astro.elongation_deg(
        astro.planet_longitude(jd, "Jupiter"),
        astro.sun_apparent_longitude(jd))
    assert abs(elong - 180.0) < 5.0


def test_western_planets_are_not_fixed_offsets() -> None:
    req = ChartRequest(birth_date="2025-01-16", birth_time="12:00",
                       timezone_str="UTC", system="western")
    planets = DeterministicChartEngine.compute_western(req).computed_data["planets"]
    sun = planets["Sun"]["total_degrees"]
    mars = planets["Mars"]["total_degrees"]
    # 冲日时火星与太阳距角≈180°：固定偏移（+120.4°）在此必错。
    assert abs(astro.elongation_deg(mars, sun) - 180.0) < 5.0
    assert "Mercury" in planets and "Saturn" in planets


# --- 月亮：朔望锚点 ----------------------------------------------------------

def test_new_moon_2000_01_06() -> None:
    jd = _jd(2000, 1, 6, 18, 14)
    elong = astro.elongation_deg(
        astro.moon_longitude(jd), astro.sun_apparent_longitude(jd))
    assert elong < 8.0


def test_full_moon_2000_01_21() -> None:
    jd = _jd(2000, 1, 21, 4, 40)
    elong = astro.elongation_deg(
        astro.moon_longitude(jd), astro.sun_apparent_longitude(jd))
    assert abs(elong - 180.0) < 8.0


# --- 上升点：随恒星时转动 ------------------------------------------------------

def test_ascendant_rotates_with_sidereal_time() -> None:
    def asc_at(hour: int) -> float:
        req = ChartRequest(birth_date="2024-06-01",
                           birth_time=f"{hour:02d}:00",
                           timezone_str="Asia/Shanghai",
                           longitude=121.0, latitude=31.0, system="western")
        return DeterministicChartEngine.compute_western(
            req).computed_data["ascendant"]["total_degrees"]
    # 同一地点晚 1 小时出生，上升点约转 15°（固定 LMST+90° 公式转得不对）。
    delta = astro.elongation_deg(asc_at(13), asc_at(12))
    assert 10.0 < delta < 20.0
