"""T1：SVG 同源存储型 XSS —— raw 下发必须 attachment + CSP sandbox。"""
from __future__ import annotations

from helpers import login_owner

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def test_svg_raw_is_attachment_with_sandbox(client) -> None:
    headers = login_owner(client)
    up = client.post("/api/assets?name=x.svg&kind=image", content=SVG,
                     headers={**headers, "Content-Type": "application/octet-stream"})
    assert up.status_code == 200, up.text
    asset_id = up.json()["asset"]["id"]

    raw = client.get(f"/api/assets/{asset_id}/raw", headers=headers)
    assert raw.status_code == 200
    disp = raw.headers.get("content-disposition", "")
    assert disp.startswith("attachment"), disp
    csp = raw.headers.get("content-security-policy", "")
    assert "sandbox" in csp, csp
