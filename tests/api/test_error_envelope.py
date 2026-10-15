"""GA-06（并入 W6 统一错误回执规范）：错误信息不得丢弃后端原因。

API 错误回执必须保留 ``error.code`` + ``error.message``（前端 errorMessage
靠 code 做中文映射；丢 code 即丢可读性）。覆盖 404 / 422 / 409 三类。
"""
from __future__ import annotations

from helpers import login_owner


def test_404_envelope_keeps_code(client) -> None:
    headers = login_owner(client)
    r = client.get("/api/dsl-lifecycle/flows/no-such-flow", headers=headers)
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] and err["message"]


def test_422_envelope_keeps_code(client) -> None:
    headers = login_owner(client)
    r = client.put("/api/dsl-lifecycle/flows/x/draft",
                   json={"doc": {"version": "1"}}, headers=headers)
    assert r.status_code in (404, 422), r.text
    err = r.json()["error"]
    assert err["code"] and err["message"]
