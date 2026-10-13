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
from helpers import login_owner

from find_yourself.api.routes import dsl_canvas


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


def _open_hitl(client: TestClient, headers: dict, execution_id: str,
               suspended: dict) -> str:
    """按挂起载荷建一条 HITL 中断，返回 interrupt id。

    走的是前端真正会打的那条路（``POST /api/hitl/interrupts``），不直接调
    服务层——否则测的就不是「HTTP 面能不能闭环」而是「服务层能不能闭环」。
    """
    r = client.post("/api/hitl/interrupts", headers=headers, json={
        "execution_id": execution_id,
        "checkpoint": suspended["checkpoint"],
        "context": suspended.get("context", {}),
        "options": suspended["options"],
        "reason": "DSL 治理节点等待裁决",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _decide(client: TestClient, headers: dict, interrupt_id: str,
            decision: str) -> None:
    r = client.post(f"/api/hitl/interrupts/{interrupt_id}/decision",
                    headers=headers, json={"decision": decision})
    assert r.status_code == 200, r.text


def test_third_party_decision_keeps_run_suspended(client: TestClient, tmp_path):
    """第三值 ``return_for_change`` 必须**继续挂起**，绝不放行也绝不失败。

    ``team_approval`` 的决策集是三值（``approve`` / ``reject`` /
    ``return_for_change``，见 ``TEAM_DECISIONS``），而 DSL 的 ``ApprovalSignal``
    只有两值 + ``None``。接缝就在这个降维上：把「退回修改」当 approve 等于
    替治理层签字，把它当 reject 等于伪造否决。正确解只有一个——不映射，落回
    ``None`` 继续挂起，把选择权交回给人。

    这条是前两条的反向护栏：只钉 approve/reject 的话，把读取器写成
    ``lambda *_: row.decision or "approve"`` 一样全绿。
    """
    dsl_canvas.reset_store_for_tests(archive_dir=str(tmp_path / "runs"))
    headers = login_owner(client)
    dsl = _enterprise_dsl(client, headers)
    execution_id = "exec-enterprise-third-value"

    r = client.post("/api/dsl-canvas/runs", headers=headers,
                    json={"dsl": dsl, "execution_id": execution_id})
    assert r.status_code == 202, r.text
    suspended = r.json()["suspended"]

    # 挂起载荷自带的两值之外，补一个治理侧的第三值（真实 team_approval 会给）。
    options = [*suspended["options"], {"value": "return_for_change",
                                       "label": "退回修改"}]
    iid = _open_hitl(client, headers, execution_id,
                     {**suspended, "options": options})
    _decide(client, headers, iid, "return_for_change")

    r2 = client.post("/api/dsl-canvas/runs", headers=headers,
                     json={"dsl": dsl, "execution_id": execution_id})
    # 仍挂起：既没 succeeded（没被猜成 approve），也没 failed（没被猜成 reject）。
    assert r2.status_code == 202, r2.text
    assert r2.json()["status"] == "suspended", r2.json()


def _enterprise_dsl(client: TestClient, headers: dict) -> dict:
    return _template(_modes(client, headers), "enterprise",
                     "enterprise-governed")["dsl"]


def test_enterprise_run_resumes_after_human_approval(client: TestClient, tmp_path):
    """判据 4 的后半程 · 挂起 → 人工裁决 → **带同一 execution_id 续跑成功**。

    这一条是G4-e 的判据。此前 HTTP 面``run_dsl`` 压根没注入
    ``approval_signal``（``api/routes/dsl_canvas.py`` 的 ``/runs`` 只传了
    ``execution_id``），所以治理层就算裁决了 approve，重开一轮仍然恒 202
    挂起——闭环在最后一米断掉。此处钉死「裁决真的能放行」。
    """
    dsl_canvas.reset_store_for_tests(archive_dir=str(tmp_path / "runs"))
    headers = login_owner(client)
    dsl = _enterprise_dsl(client, headers)
    execution_id = "exec-enterprise-approve"

    # 第一步：跑一轮，撞approval 挂起。
    r = client.post("/api/dsl-canvas/runs", headers=headers,
                    json={"dsl": dsl, "execution_id": execution_id})
    assert r.status_code == 202, r.text
    suspended = r.json()["suspended"]
    assert suspended["checkpoint"] == f"{execution_id}#{suspended['node_id']}"

    # 第二步：建中断并由人拍板 approve。
    iid = _open_hitl(client, headers, execution_id, suspended)
    _decide(client, headers, iid, "approve")

    # 第三步：带同一 execution_id 重开一轮 —— 必须真的跑完。
    r2 = client.post("/api/dsl-canvas/runs", headers=headers,
                     json={"dsl": dsl, "execution_id": execution_id})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "succeeded", body
    # 放行后 payload 原样穿过治理节点交给 out（与小白模板同一条数据）。
    assert [n["text"] for n in body["output"]] == ["甲", "丙"]


def test_enterprise_run_fails_after_human_rejection(client: TestClient, tmp_path):
    """反向闸门：驳回必须**显式失败**，绝不能被「读不到就当approve」蒙过去。

    与上一条成对：只钉approve 的话，把读取器写死成 ``lambda *_: "approve"``
    也能全绿——那等于给治理层开后门。
    """
    dsl_canvas.reset_store_for_tests(archive_dir=str(tmp_path / "runs"))
    headers = login_owner(client)
    dsl = _enterprise_dsl(client, headers)
    execution_id = "exec-enterprise-reject"

    r = client.post("/api/dsl-canvas/runs", headers=headers,
                    json={"dsl": dsl, "execution_id": execution_id})
    assert r.status_code == 202, r.text
    suspended = r.json()["suspended"]

    iid = _open_hitl(client, headers, execution_id, suspended)
    _decide(client, headers, iid, "reject")

    r2 = client.post("/api/dsl-canvas/runs", headers=headers,
                     json={"dsl": dsl, "execution_id": execution_id})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "failed", body
    assert "驳回" in (body["error"] or ""), body["error"]
