"""P5 · 插件市场后端单测（上架/检索/安装）。

门禁契约：
* 上架 = 复用 ``SkillService.promote`` 门禁，市场无法绕过（未过门禁的包
  ``state`` 永远到不了 ``active``）；
* **未过门禁的包不出现在市场列表**（服务端 WHERE 过滤，非前端隐藏）；
* 分页默认有界、超界显式拒绝；
* 安装：``plugin`` 包必须出示存在/active/未过期的授权（``GrantService``
  创建的授权数据面），运行期访问继续走 ``is_authorized``。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from find_yourself.db.models import Grant
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, NotFound, ValidationFailed
from find_yourself.services.grant import GrantService
from find_yourself.services.marketplace import MAX_PAGE_LIMIT, MarketplaceService
from find_yourself.services.memory import MemoryService
from find_yourself.services.plugin_signing import (
    SigningKeyService,
    generate_signing_keypair,
    sign_package,
)
from find_yourself.services.skill import SkillService


def _plugin_package(**extra) -> dict:
    pkg = {
        "name": "market-plugin",
        "semantic_version": "1.0.0",
        "skill_md": "# market plugin\n\n带文件条目的插件包。",
        "scripts": {"main.py": "print('hello')\n"},
        "source": "internal",
        "license": "MIT",
        "domain": "personal",
    }
    pkg.update(extra)
    return pkg


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


@pytest.fixture()
def mk(session, owner):
    audit = AuditService(session)
    skills = SkillService(session, audit)
    grants = GrantService(session, audit)
    market = MarketplaceService(session, audit, skills=skills, grants=grants)
    keys = SigningKeyService(session)
    return SimpleNamespace(market=market, skills=skills, grants=grants,
                           keys=keys, audit=audit, owner=owner)


from types import SimpleNamespace  # noqa: E402  (置于 fixture 之后仅供 reader 顺序)


def _stage_instruction(mk, *, name="instr-1", version="1.0.0") -> str:
    sk = mk.skills.stage(mk.owner, name=name, semantic_version=version,
                         package=_instruction_package(name=name),
                         source="internal", license_="MIT", domain="work")
    ev = mk.skills.evaluate(mk.owner, sk.id, static_passed=True, functional_passed=True)
    return sk.id, ev.id


def _stage_and_publish_instruction(mk, *, name="instr-1") -> str:
    sk_id, ev_id = _stage_instruction(mk, name=name)
    mk.market.publish(mk.owner, sk_id, ev_id)
    return sk_id


def _stage_and_publish_plugin(mk, *, name="plugin-1") -> str:
    priv, pub = generate_signing_keypair()
    mk.keys.register(mk.owner, key_id=f"key-{name}", public_key=pub)
    pkg = _plugin_package(name=name)
    sig = sign_package(private_key=priv, package=pkg)
    sk = mk.skills.stage(mk.owner, name=name, semantic_version="1.0.0", package=pkg,
                         source="internal", license_="MIT", domain="personal",
                         signature=sig, signing_key_id=f"key-{name}",
                         signature_algorithm="ed25519")
    ev = mk.skills.evaluate(mk.owner, sk.id, static_passed=True, functional_passed=True)
    mk.market.publish(mk.owner, sk.id, ev.id)
    return sk.id


def _personal_grant(mk, session, owner, *, memory_id: str) -> str:
    g = mk.grants.create(
        owner, source_domain="personal", consumer_domain="work",
        record_ids=[memory_id], expires_at=utcnow() + timedelta(days=1),
    )
    return g.id


# ---- 上架（复用 promote 门禁） ------------------------------------------------
def test_publish_delegates_to_promote_gate_and_lists(mk):
    """过完门禁的包 publish 后 active，且出现在市场列表。"""
    sk_id = _stage_and_publish_instruction(mk)
    listed = mk.market.list_packages(mk.owner)
    assert [i["skill_id"] for i in listed["items"]] == [sk_id]
    assert listed["total"] == 1


def test_publish_blocked_by_gate_never_listed(mk):
    """未过门禁（缺 evaluation/门禁判定不过）→ publish 抛 Conflict，且列表查不到。"""
    sk = mk.skills.stage(mk.owner, name="blocked", semantic_version="1.0.0",
                         package=_instruction_package(name="blocked"),
                         source="internal", license_="MIT", domain="work")
    with pytest.raises(Conflict):
        mk.market.publish(mk.owner, sk.id, "no-such-evaluation")
    assert mk.market.list_packages(mk.owner)["total"] == 0


def test_staged_only_package_never_appears_in_market(mk):
    """门禁核心（服务端过滤）：只 stage 未 promote 的包在服务端就查不到。"""
    mk.skills.stage(mk.owner, name="staged-only", semantic_version="1.0.0",
                    package=_instruction_package(name="staged-only"),
                    source="internal", license_="MIT", domain="work")
    listed = mk.market.list_packages(mk.owner)
    assert listed["total"] == 0
    assert listed["items"] == []


def test_disabled_package_disappears_from_market(mk):
    sk_id = _stage_and_publish_instruction(mk, name="bye")
    assert mk.market.list_packages(mk.owner)["total"] == 1
    mk.skills.disable(mk.owner, sk_id)
    listed = mk.market.list_packages(mk.owner)
    assert listed["total"] == 0  # 服务端过滤：下架后不可见，而非前端隐藏


def test_get_package_unlisted_is_not_found(mk):
    sk = mk.skills.stage(mk.owner, name="hidden", semantic_version="1.0.0",
                         package=_instruction_package(name="hidden"),
                         source="internal", license_="MIT", domain="work")
    with pytest.raises(NotFound):
        mk.market.get_package(mk.owner, sk.id)


# ---- 检索：过滤 + 有界分页 ------------------------------------------------------
def test_list_pagination_defaults_bounded(mk):
    for i in range(3):
        _stage_and_publish_instruction(mk, name=f"p-{i}")
    res = mk.market.list_packages(mk.owner)
    assert res["limit"] == 20 and res["offset"] == 0 and res["total"] == 3
    page2 = mk.market.list_packages(mk.owner, limit=2, offset=2)
    assert len(page2["items"]) == 1 and page2["offset"] == 2


def test_list_limit_over_max_rejected(mk):
    with pytest.raises(ValidationFailed) as err:
        mk.market.list_packages(mk.owner, limit=MAX_PAGE_LIMIT + 1)
    assert err.value.code == "page_limit_exceeded"


def test_list_limit_zero_and_negative_rejected(mk):
    for bad in (0, -1):
        with pytest.raises(ValidationFailed):
            mk.market.list_packages(mk.owner, limit=bad)


def test_list_offset_negative_rejected(mk):
    with pytest.raises(ValidationFailed):
        mk.market.list_packages(mk.owner, offset=-3)


def test_list_query_filters_by_name_case_insensitive(mk):
    _stage_and_publish_instruction(mk, name="AlphaTool")
    _stage_and_publish_instruction(mk, name="betaTool")
    res = mk.market.list_packages(mk.owner, query="alpha")
    assert res["total"] == 1 and res["items"][0]["name"] == "AlphaTool"


def test_list_domain_filter(mk):
    _stage_and_publish_instruction(mk, name="w-one")  # domain=work
    _stage_and_publish_plugin(mk, name="p-one")       # domain=personal
    res = mk.market.list_packages(mk.owner, domain="personal")
    assert res["total"] == 1 and res["items"][0]["name"] == "p-one"


def test_list_capability_filter(mk):
    _stage_and_publish_instruction(mk, name="c-instr")
    _stage_and_publish_plugin(mk, name="c-plugin")
    res = mk.market.list_packages(mk.owner, capability="materialize:files")
    assert res["total"] == 1 and res["items"][0]["name"] == "c-plugin"
    with pytest.raises(ValidationFailed):
        mk.market.list_packages(mk.owner, capability="nonsense:axis")


def test_total_reflects_server_side_filter_not_frontend_hiding(mk):
    """total 由服务端过滤后的集合计算——未过门禁的包连 total 都进不去。"""
    _stage_and_publish_instruction(mk, name="in-market")
    mk.skills.stage(mk.owner, name="not-in-market", semantic_version="1.0.0",
                    package=_instruction_package(name="not-in-market"),
                    source="internal", license_="MIT", domain="work")
    assert mk.market.list_packages(mk.owner)["total"] == 1


# ---- 详情：风险与扫描摘要 ------------------------------------------------------
def test_detail_shows_risk_level_scan_summary_and_capabilities(mk):
    sk_id = _stage_and_publish_plugin(mk, name="detail-p")
    card = mk.market.get_package(mk.owner, sk_id)
    assert card["gate_profile"] == "plugin"
    assert card["capabilities"] == ["materialize:files"]
    assert card["risk_reasons"] == ["materializes_files"]
    assert card["signature_verified"] is True
    assert card["scan"]["passed"] is True
    assert {"passed", "risk_level", "finding_count"} <= set(card["scan"])
    assert isinstance(card["scan"]["findings"], list)  # 详情才带 findings


def test_detail_instruction_package_lower_risk(mk):
    sk_id = _stage_and_publish_instruction(mk, name="d-instr")
    card = mk.market.get_package(mk.owner, sk_id)
    assert card["risk_level"] == "low"
    assert card["capabilities"] == ["instruction:inline"]


# ---- 安装（GrantService 授权约束） ----------------------------------------------
def test_install_instruction_package_without_grant_ok(mk):
    sk_id = _stage_and_publish_instruction(mk, name="i-install")
    card = mk.market.install(mk.owner, sk_id)
    assert card["installed"] is True and card["grant_id"] is None


def test_install_plugin_package_requires_grant(mk):
    """门禁核心：plugin 包安装无授权 → 显式拒绝（不是悄悄成功）。"""
    sk_id = _stage_and_publish_plugin(mk, name="g-plugin")
    with pytest.raises(ValidationFailed) as err:
        mk.market.install(mk.owner, sk_id)
    assert err.value.code == "install_grant_required"


def test_install_plugin_package_with_active_grant_ok(mk, session):
    mem = MemoryService(session, mk.grants, mk.audit)
    m = mem.upsert(mk.owner, owner_id="owner-1", domain="personal",
                   category="self_report", content="插件要读的记录", source_ids=[])
    sk_id = _stage_and_publish_plugin(mk, name="g-ok")
    gid = _personal_grant(mk, session, mk.owner, memory_id=m.id)
    card = mk.market.install(mk.owner, sk_id, grant_id=gid)
    assert card["installed"] is True and card["grant_id"] == gid


def test_install_plugin_package_with_revoked_grant_rejected(mk, session):
    mem = MemoryService(session, mk.grants, mk.audit)
    m = mem.upsert(mk.owner, owner_id="owner-1", domain="personal",
                   category="self_report", content="x", source_ids=[])
    sk_id = _stage_and_publish_plugin(mk, name="g-rev")
    gid = _personal_grant(mk, session, mk.owner, memory_id=m.id)
    mk.grants.revoke(mk.owner, gid)
    with pytest.raises(ValidationFailed) as err:
        mk.market.install(mk.owner, sk_id, grant_id=gid)
    assert err.value.code == "install_grant_inactive"


def test_install_plugin_package_with_expired_grant_rejected(mk, session):
    mem = MemoryService(session, mk.grants, mk.audit)
    m = mem.upsert(mk.owner, owner_id="owner-1", domain="personal",
                   category="self_report", content="x", source_ids=[])
    sk_id = _stage_and_publish_plugin(mk, name="g-exp")
    gid = _personal_grant(mk, session, mk.owner, memory_id=m.id)
    # 直接把已建授权置为过期（create 本身拒绝过去时间，这里模拟时间流逝）
    g = session.get(Grant, gid)
    g.expires_at = utcnow() - timedelta(days=1)
    session.flush()
    with pytest.raises(ValidationFailed) as err:
        mk.market.install(mk.owner, sk_id, grant_id=gid)
    assert err.value.code == "install_grant_expired"


def test_install_requires_owner_actor(mk):
    sk_id = _stage_and_publish_instruction(mk, name="i-owner")
    agent = Actor(subject_type="service", service_id="agent-x")
    with pytest.raises(Exception):
        mk.market.install(agent, sk_id)


def test_install_unknown_package_not_found(mk):
    with pytest.raises(NotFound):
        mk.market.install(mk.owner, "no-such-skill", grant_id=None)


def test_install_writes_audit(mk, session):
    sk_id = _stage_and_publish_instruction(mk, name="i-audit")
    mk.market.install(mk.owner, sk_id)
    rows = session.execute(
        __import__("sqlalchemy").text(
            "SELECT action FROM audit_events WHERE action='marketplace.installed'")
    ).fetchall()
    assert rows, "安装必须留审计痕"
