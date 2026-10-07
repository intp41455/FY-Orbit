"""默认离线运行架构 · HTTP 面（A-离线优先-01/03）。

* ``GET /api/offline/status`` —— 离线运行态快照：架构开关（默认离线）、
  远程能力是否被门住、以及一句人话原因。

为什么要有这个端点：前端指示器不该靠 ``navigator.onLine`` **猜**后端的运行态。
应用是「默认离线」而不是「浏览器恰好断网」——这两个状态完全不同（网线插着也
照样是离线模式），所以真源在后端，前端只负责如实展示。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from ..deps import get_actor
from ...services.actor import Actor
from ...services.offline import OFFLINE_MODE_ENV, is_offline, remote_block_reason, status

router = APIRouter(prefix="/api/offline", tags=["offline"])


@router.get("/status")
def offline_status(actor: Actor = Depends(get_actor)) -> dict:
    """离线运行态：``offline_mode`` 缺省 True（默认离线，不打外网）。"""
    snap = status()
    payload = asdict(snap)
    payload["env"] = OFFLINE_MODE_ENV
    # 远程能力门当前是否拦得住——前端据此显示「远程生成不可用」的降级提示，
    # 与浏览器在线与否无关（离线模式下即使有网也不走远程）。
    payload["remote_blocked"] = is_offline()
    payload["remote_block_reason"] = remote_block_reason()
    return payload
