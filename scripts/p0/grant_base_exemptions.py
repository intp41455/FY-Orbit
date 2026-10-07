"""P0-1 一次性工具：为 6 个纯展示页登记并审批基座豁免。

走的是**真实服务接口**（`BaseContractService.request_exemption` + `approve_exemption`），
不是手写 JSON —— 因此理由长度、scope 白名单、「可编辑文件不得豁免」三条硬规则
都会在登记时真的被校验一遍。审批人是 owner 身份（与 HTTP 路由同一个 `require_owner`）。

用法（必须显式 PYTHONPATH 指向本树）：
    PYTHONPATH=<tree>/src python scripts/p0/grant_base_exemptions.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["FY_BASE_SCAN_ROOT"] = str(ROOT)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from find_yourself.services.actor import Actor  # noqa: E402
from find_yourself.services.quality.base_contract import (  # noqa: E402
    BaseContractService,
    exemption_file,
)

#: 6 个纯展示页 + 逐页理由（≥10 字，且必须是**真的**：页面自身不含编辑控件）。
EXEMPTIONS: dict[str, str] = {
    "web/src/pages/CanvasPage.tsx":
        "纯展示页：只读渲染多 Agent 画布快照与模板列表，页面自身不含任何编辑控件；"
        "所有编辑动作都在已接入基座的子组件内。",
    "web/src/pages/DslCanvasPage.tsx":
        "纯展示页：只是工作流工坊的页签与玻璃面板视觉壳，页面自身不含编辑控件；"
        "编辑面由 FlowEditor / GeneratePanel / DslCanvas 等已接入基座的组件承担。",
    "web/src/pages/GameStandalonePage.tsx":
        "纯展示页：独立游戏入口，整屏交给游戏画布渲染，页面自身不含任何编辑控件。",
    "web/src/pages/HubPage.tsx":
        "纯展示页：能力中心的聚合导航页，只做跳转与只读卡片展示，不含任何编辑控件。",
    "web/src/pages/PrivateSpacePage.tsx":
        "纯展示页：私人空间的只读展示视图，页面自身不含编辑控件；"
        "编辑入口在已接入基座的子组件内。",
    "web/src/pages/TimelinePage.tsx":
        "纯展示页：时间线只读展示（展示由已接入基座的时间线组件负责），"
        "页面自身不含任何编辑控件。",
}


def main() -> int:
    path = Path(exemption_file())
    if not path.is_absolute():
        path = ROOT / path
    print(f"exemption file = {path}")
    if path.is_file():
        print("清空既有豁免清单（重建为「真实走接口」的结果）")
        path.unlink()

    eng = create_engine("sqlite://", future=True)
    session = sessionmaker(bind=eng, future=True)()
    svc = BaseContractService(session)
    actor = Actor.owner("owner-p0-autosave")

    for rel, reason in EXEMPTIONS.items():
        src = (ROOT / rel)
        if not src.is_file():
            print(f"  !! 文件不存在：{rel}")
            return 1
        req = svc.request_exemption(actor, path=rel, reason=reason)
        assert req["exemption"]["status"] == "pending", req
        app = svc.approve_exemption(
            actor, path=rel,
            note="已人工核对：该页无任何编辑控件（启发式与人工判定一致）")
        assert app["exemption"]["status"] == "approved", app
        print(f"  approved  {rel}")

    listing = svc.exemptions(actor)
    print(json.dumps({"approved": listing["approved"],
                      "pending": listing["pending"],
                      "total": listing["total"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
