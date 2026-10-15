"""GA-03（并入 W 族质量机制）：空态遍历 —— 空库下列表端点必须返回 [] 而非 null。

前端空状态依赖数组可渲染；后端一旦返 null，前端 `.map` 即崩。
覆盖主要列表端点（团队 / 画布实例 / DSL 流程 / 通知 / 会话）。
"""
from __future__ import annotations

from helpers import login_owner

EMPTY_LIST_ROUTES = [
    "/api/teams",
    "/api/canvas/instances",
    "/api/dsl-lifecycle/flows",
    "/api/collaboration/notifications",
    "/api/conversations",
]


def test_empty_list_endpoints_return_arrays_not_null(client) -> None:
    headers = login_owner(client)
    for route in EMPTY_LIST_ROUTES:
        r = client.get(route, headers=headers)
        assert r.status_code == 200, (route, r.text)
        body = r.json()
        # 允许裸数组或 {items:[...]} 两种形状，但绝不许 null。
        items = body if isinstance(body, list) else body.get("items", body)
        assert isinstance(items, list), (route, body)
        assert items == [], (route, body)
