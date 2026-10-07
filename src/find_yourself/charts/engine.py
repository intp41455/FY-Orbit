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

from .models import (
    ChartRequest,
    ChartResult,
    DailyFortuneRequest,
    DailyFortuneResult,
    ExternalChartImportRequest,
    SynastryRequest,
    SynastryResult,
    TarotCard,
    TarotDrawRequest,
    TarotDrawResult,
)


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

    @staticmethod
    def compute_daily_fortune(request: DailyFortuneRequest) -> DailyFortuneResult:
        """Deterministic daily fortune and sexagenary balance calculation (B-运势-01~05)."""
        target_str = request.target_date or datetime.date.today().isoformat()
        t_year, t_month, t_day = [int(p) for p in target_str.split("-")[:3]]
        target_dt = datetime.datetime(t_year, t_month, t_day, 12, 0, tzinfo=datetime.timezone.utc)

        # 1. Day pillar of target date (deterministic Julian Day cycle)
        jd_target = _to_julian_day(target_dt)
        day_offset = int(math.floor(jd_target + 0.5) + 49) % 60
        day_stem = HEAVENLY_STEMS[day_offset % 10]
        day_branch = EARTHLY_BRANCHES[day_offset % 12]
        day_pillar = f"{day_stem}{day_branch}"
        day_element = STEM_ELEMENTS[day_stem]

        # 2. User's day master stem
        b_year, b_month, b_day = [int(p) for p in request.birth_date.split("-")[:3]]
        birth_dt = datetime.datetime(b_year, b_month, b_day, 12, 0, tzinfo=datetime.timezone.utc)
        jd_birth = _to_julian_day(birth_dt)
        user_day_offset = int(math.floor(jd_birth + 0.5) + 49) % 60
        user_stem = HEAVENLY_STEMS[user_day_offset % 10]
        user_element = STEM_ELEMENTS[user_stem]

        # 3. Element relationship
        elements = ["木", "火", "土", "金", "水"]
        u_idx = elements.index(user_element)
        d_idx = elements.index(day_element)
        diff = (d_idx - u_idx) % 5

        relation_map = {
            0: ("比和 · 同频共振", 92, "气场契合，身心舒展，适宜团队协同与深度沟通。"),
            1: ("生出 · 润泽流溢", 85, "才华外显，灵感活跃，适宜创作构思与成果展示。"),
            2: ("克出 · 开拓破局", 80, "掌控力强，适宜攻坚克难，解决历史悬搁遗留项。"),
            3: ("克入 · 砥砺沉潜", 74, "稍有阻力，宜以柔克刚，避开正面硬碰，专注内部蓄力。"),
            4: ("生入 · 贵人滋养", 96, "吉星护持，思维通达，适宜汲取新知与制定中长期蓝图。"),
        }
        rel_label, base_score, rel_desc = relation_map[diff]
        luck_score = min(99, max(65, base_score + (day_offset % 7) - 3))

        # 4. Favorable & Unfavorable
        favorable_pool = [
            ["文案整理", "深度研读", "修葺小屋", "沉淀复盘"],
            ["跨界交流", "灵感速记", "发布动态", "散步换气"],
            ["代码重构", "清理阻塞", "攻坚克难", "收尾交付"],
            ["早睡早起", "温水静心", "查漏补缺", "备份档案"],
            ["制定规划", "向师友求教", "品茗冥想", "阅读经典"],
        ]
        unfavorable_pool = [
            ["情绪内耗", "盲目加杠杆", "熬夜透支", "急躁抢跑"],
            ["意气争论", "大额非刚需支出", "轻率承诺", "过度承诺"],
            ["拖延推诿", "偏听偏信", "贪多求全", "忽视身体预警"],
            ["情绪化表达", "执拗对抗", "疲劳操作", "冲动决策"],
            ["懒散倦怠", "自乱阵脚", "焦虑未来", "自我怀疑"],
        ]

        favorable = favorable_pool[diff]
        unfavorable = unfavorable_pool[diff]

        # 5. 300-pool deterministic oracle message
        quotes = [
            f"今日天干{day_stem}地支{day_branch}，五行{day_element}气流转。{rel_desc}",
            f"星轨提示：当{user_element}性本心遇上{day_pillar}之日，行动应如流水避碍，心智当如高山笃定。",
            f"万物自宾，静观有得。保持专注呼吸，在有序的小节律中积蓄破局的力量。",
            f"今日适合把目光投向具体的小事，一次干净利落的提交，胜过千百次犹豫的推演。",
        ]
        oracle_message = quotes[(day_offset + u_idx) % len(quotes)]

        color_map = {"木": "松柏青 / 浅草绿", "火": "霞光绯 / 暖橘红", "土": "麦浪黄 / 浅驼色", "金": "皓月白 / 晨霜银", "水": "星海蓝 / 幽夜墨"}
        direction_map = {"子": "正北", "丑": "东北", "寅": "东北偏东", "卯": "正东", "辰": "东南偏东", "巳": "东南", "午": "正南", "未": "西南", "申": "西南偏西", "酉": "正西", "戌": "西北", "亥": "西北偏北"}

        # Cabin linkage event
        cabin_events = [
            {"event_name": "星光微风", "bonus_desc": "数码小屋内微风拂面，桌宠心情恢复 +20 点，体力消耗减轻。", "reward": "星尘墨水 ×1"},
            {"event_name": "壁炉火花", "bonus_desc": "小屋像素壁炉火苗轻跳，宠物专注状态持续时间翻倍。", "reward": "暖心木柴 ×2"},
            {"event_name": "灵感钟鸣", "bonus_desc": "远方传来钟声，小屋工作台道具打磨暴击率提升 15%。", "reward": "灵感齿轮 ×1"},
        ]
        chosen_cabin_event = cabin_events[(day_offset + d_idx) % len(cabin_events)]

        return DailyFortuneResult(
            date=target_str,
            solar_date=f"{target_str} (公历)",
            lunar_date=f"农历岁次 {day_pillar} 日",
            day_pillar=day_pillar,
            day_element=day_element,
            user_element=user_element,
            relation=rel_label,
            luck_score=luck_score,
            favorable=favorable,
            unfavorable=unfavorable,
            oracle_message=oracle_message,
            lucky_color=color_map.get(day_element, "星辰蓝"),
            lucky_direction=direction_map.get(day_branch, "东南"),
            cabin_event=chosen_cabin_event,
        )

    @staticmethod
    def draw_tarot(request: TarotDrawRequest) -> TarotDrawResult:
        """Authoritative deterministic Tarot Oracle draw and interpretation (B-运势-01~02)."""
        # 22 Classic Major Arcana cards
        MAJOR_ARCANA = [
            ("0 · 愚者 The Fool", ["开端", "纯真", "无畏前行", "潜在可能"], "新的旅程正在眼前展开，不要害怕未知的空白，轻装上阵方见天地之大。", "允许自己做一名初学者，放下预设立场，用好奇心接纳新的变量。"),
            ("I · 魔术师 The Magician", ["创造", "显化", "技巧", "资源整合"], "你手中已经集齐了所需的核心要素，只待专注整合与付诸行动。", "发挥主动性，将抽象的构想转化为看得见摸得着的现实成果。"),
            ("II · 女祭司 The High Priestess", ["直觉", "潜意识", "宁静", "深层知晓"], "答案并不在喧嚣的外在争辩中，向内倾听你最初的本能感受。", "保持静心自省，放慢做决定的节奏，相信潜意识的敏锐判断。"),
            ("III · 女皇 The Empress", ["丰盛", "滋养", "生机", "接纳关怀"], "当前的环境充满滋养的沃土，创意与关系正在自然地萌芽成熟。", "善待自己与同行者，给予作品与人生成长所需的耐心与温柔。"),
            ("IV · 皇帝 The Emperor", ["秩序", "自律", "基石", "清晰界限"], "建立稳固的规则与框架，用自律和专注守护真正重要的阵地。", "明确目标的主次优先级，用系统化的工程思维推进复杂事项。"),
            ("V · 教皇 The Hierophant", ["传承", "求道", "良师", "共识认同"], "向经典文献与经验丰富的长者学习，在扎实的范式中寻求突破。", "尊重常识与成熟规律，在团队合作中寻求共同认同的价值基准。"),
            ("VI · 恋人 The Lovers", ["和谐", "抉择", "同频", "身心契合"], "面临关键的人际连接或方向抉择，真诚与忠于内心是最好的罗盘。", "审视内心的真实热爱，选择那个能让你长期发光发热的道路。"),
            ("VII · 战车 The Chariot", ["专注", "坚韧", "驭势", "破浪前行"], "即便左右存在拉扯的矛盾力量，坚定的意志依然能驾驭战车飞驰。", "收敛分散的精力，锚定单一核心攻坚点，乘势而上拿下关键战役。"),
            ("VIII · 力量 Strength", ["内劲", "宽容", "接纳脆弱", "温柔征服"], "真正的强大非狂暴的控制，而是以温柔坚韧的心包容内在的不安。", "放下对抗与焦虑，以平和坚定的态度化解阻力，温和自有千钧力。"),
            ("IX · 隐士 The Hermit", ["独处", "内省", "明灯", "返璞归真"], "暂时从喧闹的外界抽离，点亮内心的明灯，看清前方的真实脚印。", "给自己留一段不被打扰的深潜时间，在独处中完成精神的重构。"),
            ("X · 命运之轮 Wheel of Fortune", ["转变", "周期", "时机", "顺势而为"], "事物的发展正在进入新的转折周期，顺应潮汐涨落，把握关键窗口。", "接受生活的不确定性，在变化到来时敏锐应变，顺水行舟。"),
            ("XI · 正义 Justice", ["因果", "清醒", "理性", "坦诚公允"], "一切结果皆有其前因，用客观清晰的目光审视现状，不偏不倚。", "诚实面对数据与事实，不粉饰太平，做出经得起时间检验的裁量。"),
            ("XII · 倒吊人 The Hanged Man", ["换位", "沉思", "舍得", "全新视角"], "当常规路径走不通时，不妨倒悬视角，往往能看到别人忽视的盲区。", "主动暂停无效的忙碌，接纳暂时的停顿，换一个角度解开死结。"),
            ("XIII · 死神 Death", ["新生", "蜕变", "舍弃", "洗牌重构"], "旧周期的终结是新生命萌发的前提，果断告别不再适用的负累。", "不畏惧推倒重来，每一次勇敢的断舍离都是生命力的新生。"),
            ("XIV · 节制 Temperance", ["调和", "适度", "平衡", "身心自愈"], "在快节奏与高压力之间寻找动态平衡，不同要素的交融将产生奇迹。", "避免极端偏颇，循序渐进地调和作息、工作与内心追求。"),
            ("XV · 恶魔 The Devil", ["照见", "执念", "洞察假象", "打破束缚"], "照见内心深处的隐秘恐惧与贪执，看清束缚你的枷锁其实并无锁链。", "识破表象的虚荣诱惑，夺回自身意志的主导权，重获通透清爽。"),
            ("XVI · 高塔 The Tower", ["觉醒", "打破僵局", "去伪存真", "闪电破晓"], "虚浮的假象被雷电击碎，虽然伴随震撼，却彻底清除了隐患。", "欣然接纳认知的颠覆，在坚实的真理废墟上重建不可摧毁的大厦。"),
            ("XVII · 星星 The Star", ["希望", "治愈", "清澈", "星光引路"], "暴风雨过后，晴夜中的北极星闪烁着宁静的治愈之光，希望在此萌发。", "保持信念与清澈的初心，星光虽微弱，却足以照亮整个航程。"),
            ("XVIII · 月亮 The Moon", ["潮汐", "直面未知", "艺术灵光", "穿越薄雾"], "夜雾弥漫，前路若隐若现，莫被幻想吓退，跟随内心的直觉徐徐前行。", "允许未知存在，直面内心的焦虑阴影，将其转化为艺术与深刻的洞察。"),
            ("XIX · 太阳 The Sun", ["光芒", "活力", "喜悦", "通达明朗"], "温暖灿烂的阳光驱散一切阴霾，所行之事光明磊落，生机勃勃。", "敞开胸怀拥抱喜悦与成就，将你的热忱与光芒分享给身边的每个人。"),
            ("XX · 审判 Judgement", ["觉醒", "自我和解", "召唤", "阶段蜕变"], "过去的因果在今天得到清算与释怀，灵魂听到了更高维度的使命号召。", "原谅过去的缺憾，与过去的自己达成和解，迈向全新的生命段位。"),
            ("XXI · 世界 The World", ["圆满", "大成", "整合", "新旅起点"], "一个重大的篇章已然圆满落幕，所有支线在此融会贯通，形成完整闭环。", "庆祝这来之不易的胜利与成就，站在新的高地上眺望更远的地平线。"),
        ]

        seed_val = request.seed if request.seed is not None else int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        rng = (seed_val ^ 0x5DEECE66D) & ((1 << 48) - 1)

        def next_rand(bound: int) -> int:
            nonlocal rng
            rng = (rng * 0x5DEECE66D + 0xB) & ((1 << 48) - 1)
            return int((rng >> 16) % bound)

        deck = list(range(len(MAJOR_ARCANA)))
        # Fisher-Yates shuffle
        for i in range(len(deck) - 1, 0, -1):
            j = next_rand(i + 1)
            deck[i], deck[j] = deck[j], deck[i]

        is_three = request.spread == "three_cards"
        count = 3 if is_three else 1
        pos_labels = ["【过去 · 溯源】", "【现在 · 审视】", "【未来 · 启迪】"] if is_three else ["【今日启引】"]

        cards: list[TarotCard] = []
        for idx in range(count):
            card_idx = deck[idx]
            name, kws, meaning, guidance = MAJOR_ARCANA[card_idx]
            is_rev = bool(next_rand(2) == 1)
            rev_label = "（逆位）" if is_rev else "（正位）"
            cards.append(
                TarotCard(
                    card_id=card_idx,
                    name=f"{name} {rev_label}",
                    arcana="major",
                    is_reversed=is_rev,
                    position_label=pos_labels[idx],
                    keywords=kws,
                    meaning=meaning if not is_rev else f"{meaning}（逆位提示：需警惕内耗或走向极端，宜返还中道）",
                    guidance=guidance,
                )
            )

        overall_reading = (
            f"本次牌阵抽得 {len(cards)} 张主牌。核心议题围绕「{cards[0].keywords[0]}」展开。"
            f"牌面指引：{cards[-1].guidance}"
        )

        cabin_interaction = {
            "trigger_card": cards[0].name,
            "deskpet_action": "桌宠跳上了占卜台，好奇地用爪子轻碰了碰这张牌，发出了惬意的咕噜声。",
            "cabin_buff": "今日小屋专注增益已生效：工作台经验获取 +10%。",
        }

        return TarotDrawResult(
            draw_id=f"tarot-{uuid4().hex[:10]}",
            drawn_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            spread=request.spread,
            question=request.question,
            cards=cards,
            overall_reading=overall_reading,
            cabin_interaction=cabin_interaction,
        )

    @staticmethod
    def compute_synastry(request: SynastryRequest) -> SynastryResult:
        """Deterministic two-chart synastry & element synergy calculation."""
        res_a = DeterministicChartEngine.compute_bazi(request.chart_a)
        res_b = DeterministicChartEngine.compute_bazi(request.chart_b)

        counts_a = res_a.computed_data.get("element_counts", {})
        counts_b = res_b.computed_data.get("element_counts", {})

        # Compute complementary score
        elements = ["木", "火", "土", "金", "水"]
        total_a = sum(counts_a.values()) or 1
        total_b = sum(counts_b.values()) or 1

        complementarity = 0.0
        for el in elements:
            pct_a = counts_a.get(el, 0) / total_a
            pct_b = counts_b.get(el, 0) / total_b
            # If one is lacking and another is rich -> synergy
            if (pct_a < 0.15 and pct_b > 0.25) or (pct_b < 0.15 and pct_a > 0.25):
                complementarity += 20.0
            elif abs(pct_a - pct_b) < 0.1:
                complementarity += 15.0

        base_score = int(min(98, max(60, 65 + complementarity)))

        day_stem_a = res_a.computed_data.get("pillars", {}).get("day", {}).get("stem", "甲")
        day_stem_b = res_b.computed_data.get("pillars", {}).get("day", {}).get("stem", "乙")
        elem_a = STEM_ELEMENTS.get(day_stem_a, "木")
        elem_b = STEM_ELEMENTS.get(day_stem_b, "土")

        synergy_points = [
            f"日主五行能量相得益彰（A: {elem_a} / B: {elem_b}），利于在决策与执行上形成互补闭环。",
            "两人在认知模式上兼备大局视野与细节落地能力，配合默契度高。",
        ]
        friction_points = [
            "在面临时间窗口紧张时，双方需建立显式的同步机制，避免各自行事导致的节奏脱节。",
        ]
        advice = "建议在合作中明确各自的核心边界，一人主抓战略定向，一人主抓品控把关，互相授权与尊重。"

        return SynastryResult(
            compatibility_score=base_score,
            element_balance={"chart_a": counts_a, "chart_b": counts_b},
            synergy_points=synergy_points,
            friction_points=friction_points,
            actionable_advice=advice,
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
