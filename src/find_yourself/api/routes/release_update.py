"""发行更新 HTTP 面（显式触发，默认离线，不静默联网）。

* ``GET  /api/release/update/status`` — 当前版本、最新版本、更新是否可用、
  签名状态（当前一律 ``unsigned``，不宣称已签名）。
* ``POST /api/release/update/check`` — 主动探测 GitHub Releases 最新版
  （离线模式下直接返回已离线，不联网）。
* ``POST /api/release/update/stage`` — 下载并校验 ZIP 到 ``.update/pending/``。
* ``POST /api/release/update/apply`` — 把已暂存更新移入 ``.update/incoming/``，
  下次启动自动替换；**不**在本请求内替换运行中的 exe。
* ``POST /api/release/update/rollback`` — 清除暂存状态回退到当前版本。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from ...services import release_update
from ...services.actor import Actor
from ..deps import csrf_protected, get_actor

router = APIRouter(prefix="/api/release/update", tags=["release-update"])


def _to_payload(verdict: release_update.UpdateVerdict) -> dict:
    return asdict(verdict)


@router.get("/status")
def current_status(actor: Actor = Depends(get_actor)) -> dict:
    """报告当前已安装版本、是否处于离线模式，以及更新源通不通。"""
    cur = release_update.current_version()
    offline = release_update.is_offline_mode()
    return {
        "current_version": cur,
        "offline_mode": offline,
        "offline_blocked": offline,
        "reason": ("离线模式（FY_OFFLINE_MODE=1）：不联网检查更新。"
                   if offline else "已显式联网（FY_OFFLINE_MODE=0），可查询更新源。"),
        "signature_status": "unsigned",
        "asset_sha256": "",
    }


@router.post("/check")
def check_for_update(actor: Actor = Depends(get_actor)) -> dict:
    """查询 GitHub Releases 的最新版本号（只读网络，不落盘）。"""
    verdict = release_update.check_update()
    return _to_payload(verdict)


@router.post("/stage")
def stage_update(actor: Actor = Depends(csrf_protected)) -> dict:
    """下载并校验最新 ZIP 到 ``.update/pending/``。这是重活，会落盘。"""
    root = _install_root()
    meta = release_update.stage_update(root)
    return meta


@router.post("/apply")
def apply_update(actor: Actor = Depends(csrf_protected)) -> dict:
    """把已暂存的更新标记到 ``.update/incoming/``，下次启动自动替换。"""
    root = _install_root()
    return release_update.apply_staged_update(root)


@router.post("/rollback")
def rollback(actor: Actor = Depends(csrf_protected)) -> dict:
    """清除已暂存/已下载的更新状态，回退到当前版本。"""
    root = _install_root()
    return release_update.rollback_update(root)


def _install_root():
    from pathlib import Path
    env = __import__("os").environ
    root = env.get("FY_INSTALL_ROOT", "")
    if root:
        return Path(root)
    # 发行形态下，exe 位于 <root>/find-yourself-backend/find-yourself-backend.exe；
    # 其父目录的父目录即发行根。开发形态退回到 cwd。
    import sys
    exe = Path(sys.executable).resolve()
    if exe.parent.name == "find-yourself-backend":
        return exe.parent.parent
    return Path.cwd()
