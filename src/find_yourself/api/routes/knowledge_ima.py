"""B1 · ima 知识库检索端点（派单书 B1/B2/B3 · B-IMA-01/02/05）。

* ``POST /api/knowledge/ima/search`` —— 真实检索陛下的 ima 公共知识库
  （MCP 优先 / REST 兜底 / 断网走本地缓存），支持分页 + 按类型/标签过滤；
* ``GET  /api/knowledge/ima/status`` —— 通道状态（适配器卡 / 设置页用），
  如实报告 MCP / REST 各自是否配置与可用，绝不假绿灯。

挂载：routes/__init__ 的约定式自动发现只挂各模块的 ``router`` 属性，
故本模块独立成文件（不碰 routes/__init__.py，派单书铁律）。

鉴权：与 ``/api/kb/search`` 同口径（``get_actor``），owner 语义为「本机
知识库通道」，检索不落库、无 owner 行数据，跨账号无隔离面。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ...services.knowledge.ima_channel import get_ima_channel
from ..deps import get_actor

router = APIRouter(prefix="/api/knowledge/ima", tags=["knowledge"])


class ImaSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    page: int = Field(default=1, ge=1, le=100)
    page_size: int = Field(default=10, ge=1, le=50)
    #: 按类型过滤：命中 ``media_type``（如 7=md）或文件夹名精确匹配。
    type: str | None = Field(default=None, max_length=64)
    tag: str | None = Field(default=None, max_length=64)
    #: 缺省用 Settings.ima_kb_id（实测库 7509748362520236）。
    kb_id: str | None = Field(default=None, max_length=64)


@router.post("/search")
async def ima_search(
    body: ImaSearchRequest,
    actor: Actor = Depends(get_actor),
) -> dict:
    """ima 知识库真实检索：结果带 ``src`` 出处回溯（B2 · G4）。"""
    actor.require_authenticated()
    return get_ima_channel().search(
        body.query,
        page=body.page,
        page_size=body.page_size,
        type_=body.type,
        tag=body.tag,
        kb_id=body.kb_id,
    )


@router.get("/status")
async def ima_status(actor: Actor = Depends(get_actor)) -> dict:
    """通道状态：MCP / REST 是否配置与可用、凭证字段是否已填（不回显值）。"""
    actor.require_authenticated()
    return get_ima_channel().status()
