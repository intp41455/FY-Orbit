"""P0 save-500：PUT 草稿保存不得 500（实机复现：认证后 PUT 即 internal_error）。"""
from __future__ import annotations

from helpers import login_owner

DOC = {
    "version": "1",
    "nodes": [
        {"id": "in1", "type": "input",
         "params": {"kind": "literal", "value": [{"text": "x"}]}},
        {"id": "out1", "type": "output", "params": {"format": "text"}},
    ],
    "edges": [{"from": "in1", "to": "out1"}],
}


def test_save_draft_new_flow_returns_version(client) -> None:
    headers = login_owner(client)
    c = client.post("/api/dsl-lifecycle/flows",
                    json={"name": "probe", "flow_type": "workflow", "doc": DOC},
                    headers=headers)
    assert c.status_code == 201, c.text
    flow_id = c.json()["id"]
    r = client.put(f"/api/dsl-lifecycle/flows/{flow_id}/draft",
                   json={"doc": DOC}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["draft_version"] >= 1


def test_save_draft_missing_flow_is_404_not_500(client) -> None:
    headers = login_owner(client)
    r = client.put("/api/dsl-lifecycle/flows/no-such-flow/draft",
                   json={"doc": DOC}, headers=headers)
    assert r.status_code == 404, r.text
