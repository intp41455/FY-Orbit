"""A-三重模式-01/04 · REST 层端到端验收：小白「模板起手 → 装配 → 运行」。

`tests/unit/dsl_sdk/test_mode_entries.py` 验的是服务层函数；本文件从**前端真正
会打的那条路**再走一遍，证明契约在 HTTP 上成立、不是只有单测里的调用成立：

1. ``GET /api/dsl/modes`` → ``modes.length == 3``，每个模式都有起手模板（判据 1）；
2. 小白起手模板的 ``dsl`` 直接喂 ``POST /api/dsl-canvas/validate-ir`` → 无诊断，
   再喂 ``POST /api/dsl-canvas/runs`` → ``succeeded``（判据 2 的「运行」一环）；
3. 小白与技术的模板图 ``dsl`` **逐字节相同**，且 ``validate-ir`` 判定一致（判据 3）；
4. 企业起手模板（带 ``approval``）运行 → **202 + suspended**，而不是失败
   （判据 4；前端据此显示「等待人工裁决」，绝不报成「执行失败」）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from find_yourself.api.routes import dsl_canvas
from helpers import login_owner


def _modes(client: TestClient, headers: dict) -> list[dict]:
    r = client.get("/api/dsl/modes", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["modes"]


def _template(modes: list[dict], mode: str, template_id: str) -> dict:
    entry = next(m for m in modes if m["mode"] == mode)
    return next(t for t in entry["templates"] if t["template_id"] == template_id)


def test_three_modes_over_http(client: TestClient):
    headers = login_owner(client)
    modes = _modes(client, headers)
    assert [m["mode"] for m in modes] == ["beginner", "technical", "enterprise"]
    for m in modes:
        assert m["templates"], m["mode"]
        assert m["default_template"] in {t["template_id"] for t in m["templates"]}


def test_beginner_template_starts_up_and_runs(client: TestClient, tmp_path):
    """小白全流程：模板起手（拿到图）→ 校验通过 → 运行成功。"""
    dsl_canvas.reset_store_for_tests(archive_dir=str(tmp_path / "runs"))
    headers = login_owner(client)
    modes = _modes(client, headers)
    dsl = _template(modes, "beginner", "beginner-starter")["dsl"]

    assert dsl["nodes"] and dsl["edges"], "模板起手必须给真图，不能是空壳"

    r = client.post("/api/dsl-canvas/validate-ir", json={"dsl": dsl}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"valid": True, "diagnostics": []}

    r = client.post("/api/dsl-canvas/runs", json={"dsl": dsl}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded", body
    # 模板图中的 filter 丢掉空文本：「甲 / 丙」留下（不是原样的三条）。
    assert [n["text"] for n in body["output"]] == ["甲", "丙"]


def test_beginner_and_technical_templates_are_the_same_ir(client: TestClient):
    """判据 3 · 同源：两条入口给的图是同一张，且校验判定一致。"""
    headers = login_owner(client)
    modes = _modes(client, headers)
    beginner = _template(modes, "beginner", "beginner-starter")["dsl"]
    technical = _template(modes, "technical", "technical-pipeline")["dsl"]

    assert beginner == technical, "同源在这里破了：两条入口给了不同的图"

    v1 = client.post("/api/dsl-canvas/validate-ir", json={"dsl": beginner},
                     headers=headers).json()
    v2 = client.post("/api/dsl-canvas/validate-ir", json={"dsl": technical},
                     headers=headers).json()
    assert v1 == v2 == {"valid": True, "diagnostics": []}


def test_enterprise_template_suspends_not_fails(client: TestClient, tmp_path):
    """判据 4 · 企业入口：approval 处**挂起**（202），不是失败。"""
    dsl_canvas.reset_store_for_tests(archive_dir=str(tmp_path / "runs"))
    headers = login_owner(client)
    modes = _modes(client, headers)
    dsl = _template(modes, "enterprise", "enterprise-governed")["dsl"]

    kinds = [n.get("verb") for n in dsl["nodes"]]
    assert "approval" in kinds, "企业模板必须带治理节点，否则「不回归」无从谈起"

    r = client.post("/api/dsl-canvas/runs", json={"dsl": dsl}, headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "suspended"
    assert body["suspended"]["node_id"]
