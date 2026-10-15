"""NEW-2 NOTICE模块：GET /api/legal/notice 公开下发许可证声明。

* 无需登录（登录页即可查看授权条款）；
* ``notice_text`` 必须与仓库根 NOTICE 文件逐字一致（不许维护一份会漂移的副本）；
* 第三方组件清单来自真实清单文件（pyproject.toml / package.json），为空时诚实标注来源缺失。
"""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "NOTICE").exists() and (parent / "pyproject.toml").exists():
            return parent
    raise AssertionError("repo root with NOTICE not found")


def test_legal_notice_is_public_and_matches_notice_file(client) -> None:
    r = client.get("/api/legal/notice")
    assert r.status_code == 200
    body = r.json()
    assert body["license"]["id"] == "BSL-1.1"
    assert body["license"]["holder"] == "intp41455"
    assert "Business Source License 1.1" in body["notice_text"]
    # 与仓库 NOTICE 文件逐字一致。
    expected = (_repo_root() / "NOTICE").read_text(encoding="utf-8")
    assert body["notice_text"] == expected


def test_legal_notice_third_party_lists_come_from_manifests(client) -> None:
    body = client.get("/api/legal/notice").json()
    third = body["third_party"]
    assert third["backend_source"] == "pyproject.toml"
    assert third["frontend_source"] == "web/package.json"
    # 后端清单必须含真实核心依赖（来自清单解析，不是手写）。
    assert any("fastapi" in name for name in third["backend"])
    assert any("react" in name for name in third["frontend"])
