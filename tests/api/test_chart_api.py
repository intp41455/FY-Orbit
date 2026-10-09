"""API integration tests for charts: compute, import, and interpret."""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner


def test_chart_compute_and_interpret_flow(client: TestClient) -> None:
    headers = login_owner(client)

    # 包6 A-命理画像-01：公共知识走真 kb RAG（有 owner 时空结果即空，不再回退常量）。
    # 旧测试断言 web_citations 非空依赖降级常量；这里先入库一份八字心理笔记，
    # 让真链路有召回，并断言 citations 来自 kb_rag 而非 curated_fallback。
    doc = client.post(
        "/api/kb/documents?name=八字心理原型笔记.md",
        content=(
            "Bazi (八字) psychological perspective: 命盘作为心理原型映射，"
            "psychological analysis of bazi chart 融合荣格共时性原理。"
        ).encode("utf-8"),
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert doc.status_code == 200, doc.text

    # 1. Compute Bazi chart
    res_bazi = client.post(
        "/api/charts/compute",
        json={
            "birth_date": "1992-08-18",
            "birth_time": "14:30",
            "timezone_str": "Asia/Shanghai",
            "system": "bazi",
        },
        headers=headers,
    )
    assert res_bazi.status_code == 200
    bazi_data = res_bazi.json()
    assert bazi_data["system"] == "bazi"
    assert bazi_data["unknown_time"] is False
    assert len(bazi_data["data_hash"]) == 64
    chart_id = bazi_data["chart_id"]

    # 2. Interpret the computed chart
    res_interp = client.post(
        "/api/charts/interpret",
        json={
            "chart_id": chart_id,
            "perspective": "psychological",
            "user_notes": "关于职业转型的探索",
            "authorized_domain": "personal",
            "include_web_search": True,
            "include_personal_memory": True,
        },
        headers=headers,
    )
    assert res_interp.status_code == 200
    interp_data = res_interp.json()
    assert interp_data["chart_id"] == chart_id
    assert "computed_chart" in interp_data["sections"]
    assert "public_reference" in interp_data["sections"]
    assert "hypothesis_reasoning" in interp_data["sections"]
    assert "免责声明" in interp_data["disclaimer"]
    # 升级断言：citations 来自真 kb RAG（confidence=kb_rag、非降级），
    # 不再接受 curated_fallback——验证命理公共知识真链路已激活。
    web_citations = interp_data["web_citations"]
    assert len(web_citations) > 0
    assert all(c["confidence"] == "kb_rag" for c in web_citations)
    assert all(not c.get("degraded", False) for c in web_citations)


def test_chart_compute_western(client: TestClient) -> None:
    headers = login_owner(client)

    res_west = client.post(
        "/api/charts/compute",
        json={
            "birth_date": "1998-12-05",
            "birth_time": "09:15",
            "timezone_str": "Europe/London",
            "longitude": -0.12,
            "latitude": 51.5,
            "system": "western",
        },
        headers=headers,
    )
    assert res_west.status_code == 200
    data = res_west.json()
    assert data["system"] == "western"
    assert "Sun" in data["computed_data"]["planets"]


def test_chart_import_and_interpret_flow(client: TestClient) -> None:
    headers = login_owner(client)

    # 1. Import external raw chart text
    res_import = client.post(
        "/api/charts/import",
        json={
            "raw_text": "命主八字：甲子年 丙寅月 戊辰日 庚申时",
            "source_software": "AstroScannerOCR",
            "system": "bazi",
        },
        headers=headers,
    )
    assert res_import.status_code == 200
    imp_data = res_import.json()
    assert imp_data["is_external_imported"] is True
    assert imp_data["external_source"] == "AstroScannerOCR"
    chart_id = imp_data["chart_id"]

    # 2. Interpret imported chart
    res_interp = client.post(
        "/api/charts/interpret",
        json={
            "chart_id": chart_id,
            "perspective": "traditional",
            "include_web_search": True,
        },
        headers=headers,
    )
    assert res_interp.status_code == 200
    interp = res_interp.json()
    assert "computed_chart" in interp["sections"]
    assert "public_reference" in interp["sections"]


def test_chart_daily_fortune_and_tarot_api(client: TestClient) -> None:
    headers = login_owner(client)

    # 1. Daily fortune endpoint
    res_f = client.post(
        "/api/charts/fortune/daily",
        json={"birth_date": "1994-07-21", "target_date": "2026-10-07"},
        headers=headers,
    )
    assert res_f.status_code == 200
    f_data = res_f.json()
    assert f_data["date"] == "2026-10-07"
    assert "day_pillar" in f_data
    assert "luck_score" in f_data

    # 2. Tarot draw endpoint
    res_t = client.post(
        "/api/charts/tarot/draw",
        json={"spread": "single", "question": "今日重点指引"},
        headers=headers,
    )
    assert res_t.status_code == 200
    t_data = res_t.json()
    assert len(t_data["cards"]) == 1
    assert "cabin_interaction" in t_data

    # 3. Synastry endpoint
    res_s = client.post(
        "/api/charts/synastry",
        json={
            "chart_a": {"birth_date": "1994-07-21"},
            "chart_b": {"birth_date": "1996-03-15"},
        },
        headers=headers,
    )
    assert res_s.status_code == 200
    s_data = res_s.json()
    assert 50 <= s_data["compatibility_score"] <= 100

