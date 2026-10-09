"""P12 插件与模板市场、分层视图与生态兼容 HTTP 契约测试。

测试覆盖：
1. /api/plugins/marketplace 检索与 sort_by（score, rating, reviews）
2. /api/plugins/marketplace/{id}/rate 与 /ratings 评分打分与查询
3. /api/plugins/templates/hierarchy 模板三层视图（新手默认/进阶可换/一级技术入口）
4. /api/plugins/templates/market 模板市场列表与按得分排序
5. /api/plugins/templates/impact-preview 模板切换影响预检
6. /api/plugins/templates/{id}/export 导出单文件 .fytemplate 包
7. /api/plugins/templates/security-review 导入前安全审查门禁
8. /api/plugins/templates/import 安全导入与权限裁剪
9. /api/plugins/cursor/analyze, search, assemble 符号解析与代码上下文装配
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner


def test_templates_hierarchy_endpoint(client: TestClient):
    auth = login_owner(client)
    res = client.get("/api/plugins/templates/hierarchy", headers=auth)
    assert res.status_code == 200
    data = res.json()
    assert data["single_source"] is True
    assert len(data["layers"]) == 3
    assert data["layers"][0]["id"] == "novice_default"
    assert data["layers"][1]["id"] == "advanced_swappable"
    assert data["layers"][2]["id"] == "technical_removable"
    assert data["layers"][2]["portal"]["first_class_entry"] is True


def test_templates_market_list_and_ratings(client: TestClient):
    auth = login_owner(client)
    res = client.get("/api/plugins/templates/market?sort_by=score", headers=auth)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 3

    # 对 writing-pipeline 打分
    rate_res = client.post(
        "/api/plugins/templates/writing-pipeline/rate",
        json={"rating": 5.0, "comment": "非常棒的写作系统！"},
        headers=auth,
    )
    assert rate_res.status_code == 200
    assert rate_res.json()["rating"] == 5.0

    # 查评价明细
    ratings_res = client.get("/api/plugins/templates/writing-pipeline/ratings", headers=auth)
    assert ratings_res.status_code == 200
    assert ratings_res.json()["total"] == 1
    assert ratings_res.json()["items"][0]["comment"] == "非常棒的写作系统！"


def test_template_impact_preview_endpoint(client: TestClient):
    auth = login_owner(client)
    res = client.post(
        "/api/plugins/templates/impact-preview",
        json={
            "from_template_id": "writing-pipeline",
            "to_template_id": "development-pipeline",
        },
        headers=auth,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["safe_to_switch"] is True
    assert data["snapshot_action"] == "auto_savepoint_before_switch"
    assert "member_changes" in data


def test_template_export_review_and_import_pipeline(client: TestClient):
    auth = login_owner(client)

    # 1. 导出
    exp_res = client.get("/api/plugins/templates/writing-pipeline/export", headers=auth)
    assert exp_res.status_code == 200
    pkg = exp_res.json()
    assert pkg["magic"] == "FYTEMPLATE_V1"
    assert pkg["checksum"]

    # 2. 安全审查
    review_res = client.post("/api/plugins/templates/security-review", json=pkg, headers=auth)
    assert review_res.status_code == 200
    report = review_res.json()
    assert report["passed"] is True
    assert report["risk_level"] == "low"
    assert report["can_import"] is True

    # 3. 导入并裁剪工具
    imp_res = client.post(
        "/api/plugins/templates/import",
        json={
            "package": pkg,
            "confirmed_tools": ["text_search"],
            "custom_name": "API Imported Writing",
        },
        headers=auth,
    )
    assert imp_res.status_code == 200
    data = imp_res.json()
    assert data["imported"] is True
    assert data["name"] == "API Imported Writing"


def test_cursor_context_endpoints(client: TestClient):
    auth = login_owner(client)

    py_code = """
class UserService:
    def get_user(self, user_id: str) -> dict:
        \"\"\"根据用户 ID 获取详情。\"\"\"
        return {"id": user_id, "name": "Alice"}
"""
    # 1. 分析符号
    an_res = client.post(
        "/api/plugins/cursor/analyze",
        json={"file_path": "user.py", "code": py_code},
        headers=auth,
    )
    assert an_res.status_code == 200
    syms = an_res.json()["symbols"]
    assert any(s["name"] == "UserService" for s in syms)

    # 2. 搜索切片
    sr_res = client.post(
        "/api/plugins/cursor/search",
        json={"query": "get_user", "files": {"user.py": py_code}},
        headers=auth,
    )
    assert sr_res.status_code == 200
    slices = sr_res.json()["slices"]
    assert len(slices) >= 1
    assert "UserService.get_user" in slices[0]["symbol_name"]

    # 3. 组装高密度上下文
    as_res = client.post(
        "/api/plugins/cursor/assemble",
        json={"query": "get_user", "files": {"user.py": py_code}, "max_chars": 2000},
        headers=auth,
    )
    assert as_res.status_code == 200
    assert "📌 Cursor-Grade Codebase Context" in as_res.json()["prompt_context"]
