"""画布热保存（autosave）API（需求 1.2.4 / §5.5 / §8.3）。

实现界面级实时热保存（几秒级）：

1. **防抖保存**：前端 debounce 3-5 秒后触发保存
2. **冲突检测**：基于 version 乐观锁，检测并发修改
3. **心跳检测**：定期检查画布是否被其他人修改
4. **差异对比**：返回修改差异供前端展示
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from ..deps import csrf_protected, get_actor
from ...services.actor import Actor
from ...services.errors import DomainError, Conflict

router = APIRouter(prefix="/api/dsl-canvas/autosave", tags=["dsl-canvas-autosave"])


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #

class AutosaveDocument(BaseModel):
    """画布文档快照"""
    canvas_id: str = Field(min_length=1, max_length=200)
    content: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(ge=1)
    checksum: str | None = Field(default=None, max_length=128)  # SHA-256 前16位


class AutosaveConflict(BaseModel):
    """保存冲突信息"""
    current_version: int
    server_version: int
    server_content: dict[str, Any] | None = None
    server_updated_at: datetime


class AutosaveResponse(BaseModel):
    """保存响应"""
    saved: bool
    canvas_id: str
    version: int
    updated_at: datetime
    conflict: AutosaveConflict | None = None


class AutosaveSnapshot(BaseModel):
    """画布快照"""
    id: str
    canvas_id: str
    content: dict[str, Any]
    version: int
    checksum: str | None
    owner_id: str
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# 存储（内存 + 可选持久化）
# --------------------------------------------------------------------------- #

# 内存存储（生产环境建议用 Redis）
_autosave_store: dict[str, dict[str, Any]] = {}
_autosave_locks: dict[str, asyncio.Lock] = {}


def _get_lock(canvas_id: str) -> asyncio.Lock:
    """获取画布级别的锁"""
    if canvas_id not in _autosave_locks:
        _autosave_locks[canvas_id] = asyncio.Lock()
    return _autosave_locks[canvas_id]


def _compute_checksum(content: dict) -> str:
    """计算内容校验和"""
    import hashlib
    canonical = json.dumps(content, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# API 端点
# --------------------------------------------------------------------------- #

@router.post("/save", response_model=AutosaveResponse)
async def autosave(
    doc: AutosaveDocument,
    actor: Actor = Depends(csrf_protected),
) -> AutosaveResponse:
    """保存画布快照。

    使用乐观锁：如果传入 version 与服务端不匹配，返回冲突信息。

    返回：
    - saved=True: 保存成功
    - saved=False + conflict: 版本冲突，需要用户决定如何处理
    """
    lock = _get_lock(doc.canvas_id)

    async with lock:
        # 获取当前版本
        current = _autosave_store.get(doc.canvas_id)
        now = datetime.utcnow()

        if current is None:
            # 新建
            snapshot = {
                "id": f"autosave-{uuid4().hex[:12]}",
                "canvas_id": doc.canvas_id,
                "content": doc.content,
                "version": 1,
                "checksum": doc.checksum or _compute_checksum(doc.content),
                "owner_id": actor.owner_id or actor.service_id,
                "created_at": now,
                "updated_at": now,
            }
            _autosave_store[doc.canvas_id] = snapshot

            return AutosaveResponse(
                saved=True,
                canvas_id=doc.canvas_id,
                version=1,
                updated_at=now,
                conflict=None,
            )

        # 检查版本冲突
        if current["version"] != doc.version:
            return AutosaveResponse(
                saved=False,
                canvas_id=doc.canvas_id,
                version=current["version"],
                updated_at=current["updated_at"],
                conflict=AutosaveConflict(
                    current_version=doc.version,
                    server_version=current["version"],
                    server_content=current["content"],
                    server_updated_at=current["updated_at"],
                ),
            )

        # 版本匹配，更新
        new_version = doc.version + 1
        snapshot = {
            **current,
            "content": doc.content,
            "version": new_version,
            "checksum": doc.checksum or _compute_checksum(doc.content),
            "updated_at": now,
        }
        _autosave_store[doc.canvas_id] = snapshot

        return AutosaveResponse(
            saved=True,
            canvas_id=doc.canvas_id,
            version=new_version,
            updated_at=now,
            conflict=None,
        )


@router.get("/load/{canvas_id}")
async def load_autosave(
    canvas_id: str,
    actor: Actor = Depends(get_actor),
) -> AutosaveSnapshot | None:
    """加载画布的最新快照。"""
    snapshot = _autosave_store.get(canvas_id)

    if snapshot is None:
        return None

    return AutosaveSnapshot(**snapshot)


@router.get("/status/{canvas_id}")
async def check_status(
    canvas_id: str,
    since_version: int | None = None,
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """检查画布状态。

    用于前端心跳检测，发现画布是否被其他人修改。

    Query 参数：
    - since_version: 可选，检查自该版本后是否有更新

    返回：
    - modified: 是否被修改
    - version: 当前版本
    - updated_at: 最后更新时间
    """
    snapshot = _autosave_store.get(canvas_id)

    if snapshot is None:
        return {
            "canvas_id": canvas_id,
            "modified": False,
            "version": 0,
            "updated_at": None,
        }

    modified = since_version is None or snapshot["version"] > since_version

    return {
        "canvas_id": canvas_id,
        "modified": modified,
        "version": snapshot["version"],
        "updated_at": snapshot["updated_at"].isoformat(),
    }


@router.get("/history/{canvas_id}")
async def get_history(
    canvas_id: str,
    limit: int = Field(default=10, ge=1, le=100),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """获取画布的版本历史。

    注意：当前实现仅返回最新版本。完整实现需要额外的版本历史存储。
    """
    snapshot = _autosave_store.get(canvas_id)

    if snapshot is None:
        return {
            "canvas_id": canvas_id,
            "count": 0,
            "items": [],
        }

    # 当前实现只返回最新版本
    return {
        "canvas_id": canvas_id,
        "count": 1,
        "items": [
            {
                "version": snapshot["version"],
                "checksum": snapshot["checksum"],
                "updated_at": snapshot["updated_at"].isoformat(),
                "owner_id": snapshot["owner_id"],
            }
        ],
    }


@router.delete("/{canvas_id}")
async def delete_autosave(
    canvas_id: str,
    actor: Actor = Depends(csrf_protected),
) -> dict[str, str]:
    """删除画布的自动保存快照。"""
    if canvas_id in _autosave_store:
        del _autosave_store[canvas_id]

    return {"canvas_id": canvas_id, "deleted": "true"}
