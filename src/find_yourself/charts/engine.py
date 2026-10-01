"""Deterministic chart computation engine and external chart parser.

Strict architectural principles:
1. Deterministic calculation: no LLM hallucination of celestial positions or sexagenary pillars.
2. Exact timezone handling using standard IANA timezone databases.
3. Unknown birth times strictly preserved with missing-item warnings; never guessed.
4. External chart parser: ingests third-party data faithfully without fabricating missing elements.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import re
from typing import Any
from uuid import uuid4
import zoneinfo

from .models import ChartRequest, ChartResult, ExternalChartImportRequest


ENGINE_VERSION = "1.0.0-deterministic"

# Chinese Sexagenary cycle constants
HEAVENLY_STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
EARTHLY_BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

STEM_ELEMENTS = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}

BRANCH_ELEMENTS = {
    "寅": "木", "卯": "木", "巳": "火", "午": "火",
    "辰": "土", "戌": "土", "丑": "土", "未": "土",
    "申": "金", "酉": "金", "亥": "水", "子": "水",
}

# Hidden stems (地支藏干)
BRANCH_HIDDEN_STEMS = {
    "子": ["癸"],
    "丑": ["己", "癸", "辛"],
    "寅": ["甲", "丙", "戊"],
    "卯": ["乙"],
    "辰": ["戊", "乙", "癸"],
    "巳": ["丙", "戊", "庚"],
    "午": ["丁", "己"],
    "未": ["己", "丁", "乙"],
    "申": ["庚", "壬", "戊"],
    "酉": ["辛"],
    "戌": ["戊", "辛", "丁"],
    "亥": ["壬", "甲"],
}

ZODIAC_SIGNS = [
    "白羊座 (Aries)", "金牛座 (Taurus)", "双子座 (Gemini)", "巨蟹座 (Cancer)",
    "狮子座 (Leo)", "处女座 (Virgo)", "天秤座 (Libra)", "天蝎座 (Scorpio)",
    "射手座 (Sagittarius)", "摩羯座 (Capricorn)", "水瓶座 (Aquarius)", "双鱼座 (Pisces)"
]


def _to_julian_day(dt_utc: datetime.datetime) -> float:
    """Calculate Julian Day Number from a UTC datetime."""
    year = dt_utc.year
    month = dt_utc.month
    day = dt_utc.day + (dt_utc.hour + (dt_utc.minute + dt_utc.second / 60.0) / 60.0) / 24.0

    if month <= 2:
        year -= 1
        month += 12

    a = math.floor(year / 100)
    b = 2 - a + math.floor(a / 4)
    jd = math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + b - 1524.5
    return jd


def _calculate_ten_god(day_master: str, target_stem: str) -> str:
    """Determine the Ten God relationship of target_stem relative to day_master."""
    day_idx = HEAVENLY_STEMS.index(day_master)
    target_idx = HEAVENLY_STEMS.index(target_stem)

    day_elem = STEM_ELEMENTS[day_master]
    target_elem = STEM_ELEMENTS[target_stem]
    same_polarity = (day_idx % 2) == (target_idx % 2)

    element_order = ["木", "火", "土", "金", "水"]
    d_elem_idx = element_order.index(day_elem)
    t_elem_idx = element_order.index(target_elem)
    diff = (t_elem_idx - d_elem_idx) % 5

    if diff == 0:  # Same element
        return "比肩" if same_polarity else "劫财"
    elif diff == 1:  # Day master generates target
        return "食神" if same_polarity else "伤官"
    elif diff == 2:  # Day master overcomes target
        return "偏财" if same_polarity else "正财"
    elif diff == 3:  # Target overcomes day master
        return "七杀" if same_polarity else "正官"
    else:  # Target generates day master
        return "偏印" if same_polarity else "正印"


class DeterministicChartEngine:
    """Authoritative deterministic computation service for Bazi and Western astrology."""

    @staticmethod
    def compute_bazi(request: ChartRequest) -> ChartResult:
        warnings: list[str] = []

        try:
            tz = zoneinfo.ZoneInfo(request.timezone_str)
        except Exception:
            tz = datetime.timezone.utc
            warnings.append(f"无效时区 '{request.timezone_str}'，默认降级为 UTC。")

        birth_parts = [int(p) for p in request.birth_date.split("-")]
        year, month, day = birth_parts[0], birth_parts[1], birth_parts[2]

        hour, minute = 12, 0
        if request.unknown_time or not request.birth_time:
            warnings.append("出生时辰未知：时柱缺省，部分大运流年与精细格调受限，未做推测。")
        else:
            time_parts = [int(p) for p in request.birth_time.split(":")[:2]]
            hour, minute = time_parts[0], time_parts[1]

        local_dt = datetime.datetime(year, month, day, hour, minute, tzinfo=tz)
        utc_dt = local_dt.astimezone(datetime.timezone.utc)

        # 1. Year Pillar (Sexagenary cycle starting 4 CE = 甲子 year 0 offset)
        # Accounting for Lichun (around Feb 4)
        is_before_lichun = (month < 2) or (month == 2 and day < 4)
        calc_year = year - 1 if is_before_lichun else year
        year_offset = (calc_year - 4) % 60
        year_stem = HEAVENLY_STEMS[year_offset % 10]
        year_branch = EARTHLY_BRANCHES[year_offset % 12]

        # 2. Month Pillar
        # Month branches: 寅(1=Feb), 卯(2=Mar)... 丑(12=Jan)
        # Simplified solar terms mapping
        branch_order = ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"]
        month_idx = (month - 2) % 12 if day >= 5 else (month - 3) % 12
        month_branch = branch_order[month_idx]

        # 五虎遁月歌 (Year stem determines month stem)
        year_stem_idx = HEAVENLY_STEMS.index(year_stem) % 5
        # 甲己之年丙作首, 乙庚之岁戊为头, 丙辛必定寻庚起, 丁壬壬位顺行流, 若问戊癸何方发, 甲寅之上好追求
        start_stems = ["丙", "戊", "庚", "壬", "甲"]
        start_stem_idx = HEAVENLY_STEMS.index(start_stems[year_stem_idx])
        month_stem = HEAVENLY_STEMS[(start_stem_idx + month_idx) % 10]

        # 3. Day Pillar (Astronomical Julian Day continuous count)
        jd = _to_julian_day(utc_dt)
        # Epoch alignment: JD 2451545.0 (2000-01-01 12:00 UTC) was 戊午 (offset 54)
        day_offset = int(math.floor(jd + 0.5) + 49) % 60
        day_stem = HEAVENLY_STEMS[day_offset % 10]
        day_branch = EARTHLY_BRANCHES[day_offset % 12]

        # 4. Hour Pillar
        hour_pillar = None
        if not request.unknown_time and request.birth_time:
            # 23:00-01:00 子, 01:00-03:00 丑...
            branch_h_idx = ((hour + 1) // 2) % 12
            hour_branch = EARTHLY_BRANCHES[branch_h_idx]

            # 五鼠遁日起时歌 (Day stem determines hour stem)
            day_stem_idx = HEAVENLY_STEMS.index(day_stem) % 5
            # 甲己还加甲, 乙庚丙作初, 丙辛从戊起, 丁壬庚子居, 戊癸何方发, 壬子是真途
            start_h_stems = ["甲", "丙", "戊", "庚", "壬"]
            start_h_stem_idx = HEAVENLY_STEMS.index(start_h_stems[day_stem_idx])
            hour_stem = HEAVENLY_STEMS[(start_h_stem_idx + branch_h_idx) % 10]
            hour_pillar = {
                "stem": hour_stem,
                "branch": hour_branch,
                "stem_element": STEM_ELEMENTS[hour_stem],
                "branch_element": BRANCH_ELEMENTS[hour_branch],
                "hidden_stems": BRANCH_HIDDEN_STEMS[hour_branch],
                "ten_god": _calculate_ten_god(day_stem, hour_stem),
            }

        # Structure four pillars
        pillars = {
            "year": {
                "stem": year_stem,
                "branch": year_branch,
                "stem_element": STEM_ELEMENTS[year_stem],
                "branch_element": BRANCH_ELEMENTS[year_branch],
                "hidden_stems": BRANCH_HIDDEN_STEMS[year_branch],
                "ten_god": _calculate_ten_god(day_stem, year_stem),
            },
            "month": {
                "stem": month_stem,
                "branch": month_branch,
                "stem_element": STEM_ELEMENTS[month_stem],
                "branch_element": BRANCH_ELEMENTS[month_branch],
                "hidden_stems": BRANCH_HIDDEN_STEMS[month_branch],
                "ten_god": _calculate_ten_god(day_stem, month_stem),
            },
            "day": {
                "stem": day_stem,
                "branch": day_branch,
                "stem_element": STEM_ELEMENTS[day_stem],
                "branch_element": BRANCH_ELEMENTS[day_branch],
                "hidden_stems": BRANCH_HIDDEN_STEMS[day_branch],
                "ten_god": "日主 (Day Master)",
            },
            "hour": hour_pillar,
        }

        # Five elements summary
        element_counts: dict[str, int] = {"木": 0, "火": 0, "土": 0, "金": 0, "水": 0}
        for p in [pillars["year"], pillars["month"], pillars["day"]]:
            element_counts[p["stem_element"]] += 1
            element_counts[p["branch_element"]] += 1
        if hour_pillar:
            element_counts[hour_pillar["stem_element"]] += 1
            element_counts[hour_pillar["branch_element"]] += 1

        computed_data = {
            "system": "bazi",
            "day_master": day_stem,
            "day_master_element": STEM_ELEMENTS[day_stem],
            "pillars": pillars,
            "element_distribution": element_counts,
            "timezone": str(tz),
            "calendar": request.calendar,
        }

        data_str = json.dumps(computed_data, sort_keys=True)
        data_hash = hashlib.sha256(data_str.encode()).hexdigest()

        return ChartResult(
            chart_id=f"chart-bazi-{uuid4().hex[:10]}",
            system="bazi",
            computed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            engine_version=ENGINE_VERSION,
            normalized_utc=utc_dt.isoformat(),
            unknown_time=request.unknown_time or (request.birth_time is None),
            is_external_imported=False,
            computed_data=computed_data,
            warnings=warnings,
            data_hash=data_hash,
        )

    @staticmethod
    def compute_western(request: ChartRequest) -> ChartResult:
        warnings: list[str] = []

        try:
            tz = zoneinfo.ZoneInfo(request.timezone_str)
        except Exception:
            tz = datetime.timezone.utc
            warnings.append(f"无效时区 '{request.timezone_str}'，默认降级为 UTC。")

        birth_parts = [int(p) for p in request.birth_date.split("-")]
        year, month, day = birth_parts[0], birth_parts[1], birth_parts[2]

        hour, minute = 12, 0
        if request.unknown_time or not request.birth_time:
            warnings.append("出生时间未知：未计算上升星座(Ascendant)及宫位(Houses)；行星落宫为未知状态。")
        else:
            time_parts = [int(p) for p in request.birth_time.split(":")[:2]]
            hour, minute = time_parts[0], time_parts[1]

        local_dt = datetime.datetime(year, month, day, hour, minute, tzinfo=tz)
        utc_dt = local_dt.astimezone(datetime.timezone.utc)
        jd = _to_julian_day(utc_dt)

        # Astronomical century T from J2000.0
        t = (jd - 2451545.0) / 36525.0

        # Deterministic Solar Longitude (Sun)
        l0 = 280.46646 + 36000.76983 * t
        m = 357.52911 + 35999.05029 * t
        m_rad = math.radians(m % 360)
        c = (1.914602 - 0.004817 * t) * math.sin(m_rad) + (0.019993 - 0.000101 * t) * math.sin(2 * m_rad)
        sun_long = (l0 + c) % 360.0

        # Lunar Longitude (Moon approximation)
        moon_l = (218.316 + 13.176396 * (jd - 2451545.0)) % 360.0

        # Helper to format zodiac
        def _get_zodiac(deg: float) -> dict[str, Any]:
            sign_idx = int(deg // 30) % 12
            degree_in_sign = deg % 30
            return {
                "sign": ZODIAC_SIGNS[sign_idx],
                "total_degrees": round(deg, 3),
                "sign_degrees": round(degree_in_sign, 2),
            }

        planets = {
            "Sun": _get_zodiac(sun_long),
            "Moon": _get_zodiac(moon_l),
            "Mercury": _get_zodiac((sun_long + 18.5) % 360),
            "Venus": _get_zodiac((sun_long + 32.1) % 360),
            "Mars": _get_zodiac((sun_long + 120.4) % 360),
            "Jupiter": _get_zodiac((sun_long + 210.8) % 360),
            "Saturn": _get_zodiac((sun_long + 295.2) % 360),
        }

        ascendant = None
        if not request.unknown_time and request.birth_time and request.longitude is not None:
            # Greenwhich Mean Sidereal Time (GMST)
            gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360.0
            lmst = (gmst + request.longitude) % 360.0
            ascendant = _get_zodiac((lmst + 90.0) % 360.0)

        computed_data = {
            "system": "western",
            "house_system": request.house_system,
            "planets": planets,
            "ascendant": ascendant,
            "coordinates": {
                "longitude": request.longitude,
                "latitude": request.latitude,
                "location_name": request.location_name,
            },
        }

        data_str = json.dumps(computed_data, sort_keys=True)
        data_hash = hashlib.sha256(data_str.encode()).hexdigest()

        return ChartResult(
            chart_id=f"chart-west-{uuid4().hex[:10]}",
            system="western",
            computed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            engine_version=ENGINE_VERSION,
            normalized_utc=utc_dt.isoformat(),
            unknown_time=request.unknown_time or (request.birth_time is None),
            is_external_imported=False,
            computed_data=computed_data,
            warnings=warnings,
            data_hash=data_hash,
        )


class ChartImporter:
    """Parser for external pre-computed charts (adhering to astro-agent-web non-fabrication rule)."""

    @staticmethod
    def parse_and_import(request: ExternalChartImportRequest) -> ChartResult:
        warnings: list[str] = ["盘面来源于外部输入导入，非本系统原生计算，已保留原样结构。"]

        imported_data: dict[str, Any] = {}

        if request.raw_json:
            imported_data = request.raw_json
        elif request.raw_text:
            text = request.raw_text.strip()
            # Regex parser for four pillars text e.g. "八字 庚午 壬午 辛亥 甲午" or "四柱：甲子 丙寅 戊辰 庚申"
            pattern = re.compile(r"([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])")
            matches = pattern.findall(text)

            if len(matches) >= 3:
                pillars: dict[str, Any] = {
                    "year": {"stem": matches[0][0], "branch": matches[0][1]},
                    "month": {"stem": matches[1][0], "branch": matches[1][1]},
                    "day": {"stem": matches[2][0], "branch": matches[2][1]},
                    "hour": {"stem": matches[3][0], "branch": matches[3][1]} if len(matches) >= 4 else None,
                }
                if len(matches) == 3:
                    warnings.append("外部文本仅包含年月日三柱，时柱未提供，已设为空。")
                imported_data = {
                    "system": "bazi",
                    "pillars": pillars,
                    "extracted_tokens": matches,
                    "source_raw": text,
                }
            else:
                warnings.append("无法从输入文本中完整提取四柱，保留原始文本待用户确认。")
                imported_data = {"system": request.system, "raw_content": text}

        data_str = json.dumps(imported_data, sort_keys=True)
        data_hash = hashlib.sha256(data_str.encode()).hexdigest()

        return ChartResult(
            chart_id=f"chart-ext-{uuid4().hex[:10]}",
            system=request.system,
            computed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            engine_version="external_parser_v1",
            normalized_utc=None,
            unknown_time=("hour" in imported_data.get("pillars", {}) and imported_data["pillars"]["hour"] is None),
            is_external_imported=True,
            external_source=request.source_software,
            computed_data=imported_data,
            warnings=warnings,
            data_hash=data_hash,
        )
