"""插件与工具市场评分体系测试（A-工具市场-03 · P12）。

验证项：
1. 评分合法性：1~5 星范围校验，非法输入抛 ValidationFailed。
2. 评价增改：同一用户再次评分为更新（upsert），不重复计数。
3. 贝叶斯排位计算（好用被顶上来）：冷启动收敛于中立基准，评价多且高的包排位显著提升。
4. 市场列表排序：默认按综合得分降序，好评包稳居首位；支持按星级、评价数、名称排序。
5. 详情呈现：返回平均分、总评价数、星级分布直方图、当前用户评价。
6. 审计日志：评分操作产生 marketplace.rated 审计记录。
"""

import pytest
from sqlalchemy import select

from find_yourself.db.models import AuditEvent
from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.grant import GrantService
from find_yourself.services.marketplace import MarketplaceService
from find_yourself.services.marketplace_rating import MarketplaceRatingStore
from find_yourself.services.skill import SkillService


@pytest.fixture()
def rating_store(tmp_path):
    db_file = str(tmp_path / "test_ratings.db")
    return MarketplaceRatingStore(db_file)


@pytest.fixture()
def market(session, audit, rating_store):
    skills = SkillService(session, audit)
    grants = GrantService(session, audit)
    return MarketplaceService(session, audit, skills, grants, ratings=rating_store)


def _instruction_package(**extra) -> dict:
    pkg = {
        "name": "market-instruction",
        "semantic_version": "1.0.0",
        "skill_md": "# instruction\n\n惰性指令包。",
        "source": "internal",
        "license": "MIT",
        "domain": "work",
    }
    pkg.update(extra)
    return pkg


def _seed_active_skill(skills, market, owner, name: str) -> str:
    sk = skills.stage(
        owner,
        name=name,
        semantic_version="1.0.0",
        package=_instruction_package(name=name),
        source="internal",
        license_="MIT",
        domain="work",
    )
    ev = skills.evaluate(owner, sk.id, static_passed=True, functional_passed=True)
    market.publish(owner, sk.id, ev.id)
    return sk.id


def test_rating_bounds_and_validation(market, owner):
    sid = _seed_active_skill(market.skills, market, owner, "Alpha-1")
    with pytest.raises(ValidationFailed) as ei:
        market.rate_package(owner, sid, 0.5)
    assert ei.value.code == "rating_out_of_range"

    with pytest.raises(ValidationFailed) as ei:
        market.rate_package(owner, sid, 5.5)
    assert ei.value.code == "rating_out_of_range"

    with pytest.raises(ValidationFailed) as ei:
        market.rate_package(owner, sid, "five")  # type: ignore
    assert ei.value.code == "rating_invalid"


def test_rate_package_upsert_and_summary(market, owner):
    sid = _seed_active_skill(market.skills, market, owner, "Alpha-2")
    res1 = market.rate_package(owner, sid, 4.0, comment="很好用")
    assert res1["rating"] == 4.0
    assert res1["summary"]["average_rating"] == 4.0
    assert res1["summary"]["rating_count"] == 1

    # 同一用户更新评分
    res2 = market.rate_package(owner, sid, 5.0, comment="更新为五星")
    assert res2["rating"] == 5.0
    assert res2["summary"]["average_rating"] == 5.0
    assert res2["summary"]["rating_count"] == 1
    assert res2["summary"]["distribution"]["5"] == 1


def test_good_packages_rise_to_top_ranking(market, owner, rating_store):
    """验证「好用被顶上来」核心逻辑：多个评分且高分的包排在前面。"""
    s_poor = _seed_active_skill(market.skills, market, owner, "Poor Tool")
    s_great = _seed_active_skill(market.skills, market, owner, "Great Tool")
    s_new = _seed_active_skill(market.skills, market, owner, "New Tool")

    # s_great 收到 10 个 5 星评价
    for i in range(10):
        rating_store.rate("plugin", s_great, f"user-{i}", 5.0)

    # s_poor 收到 10 个 1 星评价
    for i in range(10):
        rating_store.rate("plugin", s_poor, f"user-{i}", 1.0)

    # s_new 无评价（中立默认）

    listing = market.list_packages(owner, sort_by="score")
    ids = [item["skill_id"] for item in listing["items"]]
    assert ids == [s_great, s_new, s_poor]
    assert listing["items"][0]["rating"]["average_rating"] == 5.0
    assert listing["items"][0]["rating"]["score"] > 4.3
    assert listing["items"][2]["rating"]["average_rating"] == 1.0
    assert listing["items"][2]["rating"]["score"] < 2.0


def test_sort_by_different_dimensions(market, owner, rating_store):
    s_a = _seed_active_skill(market.skills, market, owner, "AAA Tool")
    s_b = _seed_active_skill(market.skills, market, owner, "BBB Tool")

    rating_store.rate("plugin", s_a, "u1", 3.0)
    rating_store.rate("plugin", s_b, "u1", 5.0)
    rating_store.rate("plugin", s_b, "u2", 4.0)

    # 按 rating 最高
    by_rating = market.list_packages(owner, sort_by="rating")
    assert by_rating["items"][0]["skill_id"] == s_b

    # 按 reviews 最多
    by_reviews = market.list_packages(owner, sort_by="reviews")
    assert by_reviews["items"][0]["skill_id"] == s_b

    # 按 name 字母序
    by_name = market.list_packages(owner, sort_by="name")
    assert by_name["items"][0]["skill_id"] == s_a


def test_get_package_detail_and_reviews(market, owner):
    sid = _seed_active_skill(market.skills, market, owner, "Alpha-3")
    market.rate_package(owner, sid, 5.0, comment="极为推荐！")

    detail = market.get_package(owner, sid)
    assert detail["rating"]["average_rating"] == 5.0
    assert detail["rating"]["rating_count"] == 1
    assert detail["my_rating"]["rating"] == 5.0
    assert detail["my_rating"]["comment"] == "极为推荐！"

    reviews = market.get_package_ratings(owner, sid)
    assert reviews["total"] == 1
    assert reviews["items"][0]["comment"] == "极为推荐！"


def test_rating_creates_audit_event(market, owner, session):
    sid = _seed_active_skill(market.skills, market, owner, "Alpha-4")
    market.rate_package(owner, sid, 4.5, comment="不错")

    events = session.execute(select(AuditEvent)).scalars().all()
    rated_events = [e for e in events if e.action == "marketplace.rated"]
    assert len(rated_events) == 1
    assert rated_events[0].target == sid
    assert rated_events[0].details["rating"] == 4.5


def test_rate_nonexistent_or_inactive_package(market, owner):
    with pytest.raises(NotFound):
        market.rate_package(owner, "s-404", 5.0)
