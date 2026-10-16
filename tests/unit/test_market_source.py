"""I-12/T8：市场远程源（market_source kind + source_type 两种来源形态）。

覆盖裁决落点：kind 登记 + 落库 + 探活 + 导入（只运输）+ 可见-but-阻塞陈列
+ 安装门禁拒绝。远端包永远不过本地签名/扫描门禁就装不上。

Git 形态（``source_type="git"``）用**可注入的假执行器**测试：单测绝不真调 git，
只按 argv 里的目标目录铺假件，因此 clone 成功/失败/超限/路径穿越都能确定性地断言。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from find_yourself.db.hub_constants import HUB_KINDS, MARKET_SOURCE_TYPES
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.grant import GrantService
from find_yourself.services.hub import adapters as hub_adapters
from find_yourself.services.hub.adapters import MarketSourceAdapter
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


class _FakeGit:
    """假 git 执行器：按 argv 末尾的目标目录铺文件，绝不真调 git。"""

    def __init__(self, files: dict[str, str] | None = None, *,
                 returncode: int = 0, stderr: str = "") -> None:
        self.files = files or {}
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []

    def __call__(self, argv: list[str], timeout: float) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        self.timeouts.append(timeout)
        if self.returncode:
            return self.returncode, "", self.stderr
        dest = Path(argv[-1])
        for rel, content in self.files.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return 0, "", ""


def _git_repo(packages: dict[str, Any], *, index_name: str = "fy-market.json") -> dict[str, str]:
    """拼出「仓库内容」：清单 + 每个包的 manifest（同一套 JSON 形态）。"""
    files = {index_name: json.dumps({"packages": packages}, ensure_ascii=False)}
    for pkg in packages:
        files[f"packages/{pkg['id']}.json"] = json.dumps({
            "name": pkg["name"],
            "semantic_version": pkg.get("version", "1.0.0"),
            "package": {"skill_md": f"# {pkg['name']}\n\nGit 源包说明。", "domain": "work"},
            "license": "MIT",
            "domain": "work",
        }, ensure_ascii=False)
    return files


def _pkg(pid: str, *, rel: str | None = None) -> dict[str, Any]:
    return {"id": pid, "name": f"Git 源包 {pid}", "version": "2.0.0",
            "description": "来自 Git 仓库", "manifest_path": rel or f"packages/{pid}.json"}


def _git_conn(*, hub, owner, url, extra: dict[str, Any] | None = None):
    return hub.create_connection(owner, {
        "name": "test-git-src",
        "kind": "market_source",
        "config": {"url": url, "source_type": "git", **(extra or {})},
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


# --------------------------------------------------------------------------- #
# Git 形态（source_type="git"）
# --------------------------------------------------------------------------- #


def test_source_type_is_explicit_field_not_new_kind() -> None:
    # Git 是「用哪条通道搬运」，不是新的连接类型：kind 仍只有 market_source 一个。
    assert MARKET_SOURCE_TYPES == ("http", "git")
    assert "git_source" not in HUB_KINDS


def test_unknown_source_type_rejected(session, owner) -> None:
    hub = HubService(session)
    with pytest.raises(ValidationFailed) as exc_info:
        hub.create_connection(owner, {
            "name": "bad-type",
            "kind": "market_source",
            "config": {"url": "https://example.com/x.git", "source_type": "ftp"},
        })
    assert exc_info.value.code == "hub_market_source_bad_type"


def test_git_url_guard_rejects_non_git_scheme(session, owner) -> None:
    hub = HubService(session)
    with pytest.raises(ValidationFailed) as exc_info:
        hub.create_connection(owner, {
            "name": "js-url",
            "kind": "market_source",
            "config": {"url": "javascript:alert(1)", "source_type": "git"},
        })
    assert exc_info.value.code == "hub_market_source_git_bad_url"


def test_git_scp_style_url_accepted_but_private_host_blocked(session, owner) -> None:
    hub = HubService(session)
    ok = _git_conn(hub=hub, owner=owner, url="git@github.com:acme/fy-market.git")
    assert ok["kind"] == "market_source"

    adapter = MarketSourceAdapter({"url": "git@127.0.0.1:acme/fy-market.git",
                                   "source_type": "git"})
    with pytest.raises(ValidationFailed) as exc_info:
        adapter._clone(timeout_seconds=1.0)
    assert exc_info.value.code == "hub_ssrf_blocked"


def test_git_import_end_to_end(session, owner, tmp_path) -> None:
    market, _skills = _mk(session, owner)
    hub = HubService(session)
    fake = _FakeGit(_git_repo([_pkg("git-hello")]))
    workdir = tmp_path / "clones"
    conn = _git_conn(hub=hub, owner=owner, url="https://example.com/acme/market.git")

    result = market.import_from_source(owner, conn["id"], runner=fake,
                                       workdir=str(workdir))
    assert len(result["imported"]) == 1
    assert result["skipped"] == []

    # clone argv：浅克隆 + `--` 隔离位置参数（仓库地址绝不被当成 git 选项）
    argv = fake.calls[0]
    assert argv[0] == "git"
    assert "--depth" in argv and argv[argv.index("--depth") + 1] == "1"
    assert argv[-3] == "--"
    assert argv[-2] == "https://example.com/acme/market.git"

    # 陈列：可见-but-阻塞（导入只运输，不上架）
    listing = market.list_packages(owner)
    assert listing["total"] == 0
    assert len(listing["blocked"]) == 1
    assert listing["blocked"][0]["blocked_reasons"]

    # 临时克隆目录用完即清，不在磁盘留垃圾
    assert workdir.exists() and not list(workdir.iterdir())


def test_git_clone_failure_and_missing_git_have_codes(session, owner, tmp_path) -> None:
    hub = HubService(session)
    conn = _git_conn(hub=hub, owner=owner, url="https://example.com/acme/market.git")
    adapter = hub.market_source_adapter(
        owner, conn["id"],
        runner=_FakeGit(returncode=128, stderr="fatal: repository not found"),
        workdir=str(tmp_path / "clones"),
    )
    with pytest.raises(ValidationFailed) as exc_info:
        adapter.list_packages()
    assert exc_info.value.code == "hub_market_source_git_clone_failed"
    assert "repository not found" in exc_info.value.message


def test_git_runner_maps_timeout_and_missing_binary(monkeypatch) -> None:
    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(hub_adapters.subprocess, "run", _timeout)
    with pytest.raises(ValidationFailed) as exc_info:
        hub_adapters._run_git(["git", "clone"], 1.0)
    assert exc_info.value.code == "hub_market_source_git_timeout"

    def _missing(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(hub_adapters.subprocess, "run", _missing)
    with pytest.raises(ValidationFailed) as exc_info2:
        hub_adapters._run_git(["git", "clone"], 1.0)
    assert exc_info2.value.code == "hub_market_source_git_unavailable"


def test_git_missing_manifest_reports_candidates(tmp_path) -> None:
    adapter = MarketSourceAdapter(
        {"url": "https://example.com/acme/market.git", "source_type": "git"},
        runner=_FakeGit({"README.md": "# not a market"}),
        workdir=str(tmp_path / "clones"),
    )
    with pytest.raises(ValidationFailed) as exc_info:
        adapter.list_packages()
    assert exc_info.value.code == "hub_market_source_git_manifest_missing"


def test_git_repo_size_limit_refuses_huge_repo(tmp_path) -> None:
    adapter = MarketSourceAdapter(
        {"url": "https://example.com/acme/market.git", "source_type": "git",
         "git_max_bytes": 64},
        runner=_FakeGit({"fy-market.json": json.dumps({"packages": []}),
                         "big.bin": "x" * 4096}),
        workdir=str(tmp_path / "clones"),
    )
    with pytest.raises(ValidationFailed) as exc_info:
        adapter.list_packages()
    assert exc_info.value.code == "hub_market_source_git_repo_too_large"


@pytest.mark.parametrize("evil_path", [
    "../../outside.json",
    "..\\..\\outside.json",
    "/etc/passwd",
    "C:/Windows/win.ini",
])
def test_git_manifest_path_traversal_blocked(tmp_path, evil_path) -> None:
    adapter = MarketSourceAdapter(
        {"url": "https://example.com/acme/market.git", "source_type": "git"},
        runner=_FakeGit(_git_repo([_pkg("evil", rel=evil_path)])),
        workdir=str(tmp_path / "clones"),
    )
    entries = adapter.list_packages()
    with pytest.raises(ValidationFailed) as exc_info:
        adapter.load_package_manifest(entries[0])
    assert exc_info.value.code == "hub_market_source_git_path_escape"


def test_git_path_traversal_entry_is_skipped_not_fatal(session, owner, tmp_path) -> None:
    market, _skills = _mk(session, owner)
    hub = HubService(session)
    conn = _git_conn(hub=hub, owner=owner, url="https://example.com/acme/market.git")
    fake = _FakeGit(_git_repo([_pkg("evil", rel="../../outside.json"), _pkg("ok")]))

    result = market.import_from_source(owner, conn["id"], runner=fake,
                                       workdir=str(tmp_path / "clones"))
    # 好包照常导入，坏包如实进 skipped（不整批失败，也不假装成功）
    assert len(result["imported"]) == 1
    assert len(result["skipped"]) == 1
    assert "path_escape" in result["skipped"][0]["reason"]


def test_git_single_package_repo_imports(session, owner, tmp_path) -> None:
    market, _skills = _mk(session, owner)
    hub = HubService(session)
    single = json.dumps({
        "name": "solo-git-pkg",
        "semantic_version": "0.9.0",
        "package": {"skill_md": "# solo\n\n单包仓库。", "domain": "work"},
    }, ensure_ascii=False)
    conn = _git_conn(hub=hub, owner=owner, url="https://example.com/acme/solo.git",
                     extra={"git_manifest": "solo.json"})

    result = market.import_from_source(owner, conn["id"],
                                       runner=_FakeGit({"solo.json": single}),
                                       workdir=str(tmp_path / "clones"))
    assert len(result["imported"]) == 1
    assert result["skipped"] == []


def test_git_timeout_budgets_differ_per_purpose(tmp_path) -> None:
    # 导入：HTTP 索引的 10s 量级不能直接套给 clone（默认下限 GIT_DEFAULT_TIMEOUT）
    fake = _FakeGit(_git_repo([_pkg("p1")]))
    adapter = MarketSourceAdapter(
        {"url": "https://example.com/acme/market.git", "source_type": "git"},
        runner=fake, workdir=str(tmp_path / "clones"),
    )
    adapter.list_packages(timeout_seconds=10.0)
    assert fake.timeouts == [hub_adapters.GIT_DEFAULT_TIMEOUT]

    # 探活：专用小预算（不能让 health-check-all 被一个慢仓库拖死）
    probe = _FakeGit(_git_repo([_pkg("p1")]))
    probe_adapter = MarketSourceAdapter(
        {"url": "https://example.com/acme/market.git", "source_type": "git"},
        runner=probe, workdir=str(tmp_path / "clones"),
    )
    probe_adapter.health()
    assert probe.timeouts == [hub_adapters.GIT_HEALTH_TIMEOUT]

    # 显式配置优先于两者
    tuned = _FakeGit(_git_repo([_pkg("p1")]))
    tuned_adapter = MarketSourceAdapter(
        {"url": "https://example.com/acme/market.git", "source_type": "git",
         "git_timeout_seconds": 7},
        runner=tuned, workdir=str(tmp_path / "clones"),
    )
    tuned_adapter.list_packages(timeout_seconds=10.0)
    assert tuned.timeouts == [7.0]


def test_http_source_never_touches_git_runner(session, owner, tmp_path) -> None:
    # 老连接（无 source_type）必须仍走 HTTP/file 路径，git 执行器一次都不许被调。
    market, _skills = _mk(session, owner)
    hub = HubService(session)
    url = _source_dir(tmp_path, [{
        "id": "remote-hello", "name": "远端问候包", "version": "1.0.0",
        "description": "测试包", "download_url": f"{tmp_path.as_uri()}/remote-hello.json",
    }])
    conn = _conn(hub=hub, owner=owner, url=url)

    def _boom(_argv, _timeout):
        raise AssertionError("HTTP/file 源不应调用 git 执行器")

    result = market.import_from_source(owner, conn["id"], runner=_boom,
                                       workdir=str(tmp_path / "clones"))
    assert len(result["imported"]) == 1
