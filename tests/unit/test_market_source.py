"""I-12/T8：市场远程源（market_source kind）。

覆盖裁决落点：kind 登记 + 落库 + 探活 + 导入（只运输）+ 可见-but-阻塞陈列
+ 安装门禁拒绝。远端包永远不过本地签名/扫描门禁就装不上。
"""
from __future__ import annotations

import json

import pytest

from find_yourself.db.hub_constants import HUB_KINDS
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.grant import GrantService
from find_yourself.services.hub.connections import HubService
from find_yourself.services.marketplace import MarketplaceService
from find_yourself.services.skill import SkillService


def _mk(session, owner):
    audit = AuditService(session)
    skills = SkillService(session, audit)
    grants = GrantService(session, audit)
    market = MarketplaceService(session, audit, skills=skills, grants=grants)
    return market, skills


def _source_dir(tmp_path, packages) -> str:
    (tmp_path / "index.json").write_text(
        json.dumps({"packages": packages}), encoding="utf-8")
    for pkg in packages:
        (tmp_path / f"{pkg['id']}.json").write_text(json.dumps({
            "name": pkg["name"],
            "semantic_version": pkg.get("version", "1.0.0"),
            "package": {"skill_md": f"# {pkg['name']}\n\n远程包说明。",
                        "domain": "work"},
            "license": "MIT",
            "domain": "work",
        }), encoding="utf-8")
    return tmp_path.as_uri()


def _conn(*, hub, owner, url):
    return hub.create_connection(owner, {
        "name": "test-remote-src",
        "kind": "market_source",
        "config": {"url": url},
    })


def test_market_source_kind_registered() -> None:
    assert "market_source" in HUB_KINDS


def test_create_market_source_requires_url(session, owner) -> None:
    hub = HubService(session)
    with pytest.raises(ValidationFailed) as exc_info:
        hub.create_connection(owner, {"name": "no-url", "kind": "market_source",
                                      "config": {}})
    assert exc_info.value.code == "hub_market_source_url_required"


def test_import_probe_list_block_install(session, owner, tmp_path) -> None:
    market, _skills = _mk(session, owner)
    hub = HubService(session)
    url = _source_dir(tmp_path, [{
        "id": "remote-hello", "name": "远端问候包", "version": "1.0.0",
        "description": "测试包", "download_url": f"{tmp_path.as_uri()}/remote-hello.json",
    }])
    conn = _conn(hub=hub, owner=owner, url=url)

    # 探活：file:// 源可达，包数如实。
    report = hub.health_check(owner, conn["id"])
    assert report["report"]["ok"] is True
    assert "1 个包" in report["report"]["detail"]

    # 导入：只运输 → staged，不过门禁不上架。
    result = market.import_from_source(owner, conn["id"])
    assert len(result["imported"]) == 1
    assert result["skipped"] == []

    # 列表：可见-but-阻塞，带原因。
    listing = market.list_packages(owner)
    assert listing["total"] == 0
    assert len(listing["blocked"]) == 1
    blocked = listing["blocked"][0]
    assert blocked["installable"] is False
    assert blocked["blocked_reasons"]

    # 安装：门禁拒绝（未签名/未过扫描），且理由中文可读。
    with pytest.raises(ValidationFailed) as exc_info:
        market.install(owner, result["imported"][0])
    assert exc_info.value.code == "install_gate_blocked"
