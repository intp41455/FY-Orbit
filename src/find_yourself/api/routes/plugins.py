"""插件市场 HTTP 接口（P5 · 需求 14 后半）。

* ``GET  /api/plugins/marketplace``                 — 检索（服务端过滤 + 有界分页）
* ``GET  /api/plugins/marketplace/{skill_id}``      — 详情（风险等级 + 扫描摘要 + 能力）
* ``POST /api/plugins/marketplace/{skill_id}/publish``  — 上架（内部走 SkillService.promote 门禁）
* ``POST /api/plugins/marketplace/{skill_id}/install``  — 安装（plugin 包必须出示有效授权）

上架/门禁/授权全部复用既有服务（``SkillService.promote``、``GrantService``），
本路由层**不写**任何新门禁；未过门禁的包在服务端就不可见，不是前端隐藏。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import csrf_protected, get_actor, get_services
from ...services.actor import Actor
from ...services.errors import DomainError
from ...services.marketplace import DEFAULT_PAGE_LIMIT, MarketplaceService
from ...services.skill import SkillService
from ...services.grant import GrantService

router = APIRouter(prefix="/api/plugins", tags=["plugin-marketplace"])


def _marketplace(services=Depends(get_services)) -> MarketplaceService:
    return MarketplaceService(
        services.session, services.audit,
        skills=SkillService(services.session, services.audit),
        grants=GrantService(services.session, services.audit),
    )


@router.get("/marketplace")
async def list_marketplace(
    actor: Actor = Depends(get_actor),
    market: MarketplaceService = Depends(_marketplace),
    query: str = "",
    domain: str | None = None,
    capability: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> dict:
    try:
        return market.list_packages(
            actor, query=query, domain=domain, capability=capability,
            limit=limit, offset=offset,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.get("/marketplace/{skill_id}")
async def get_marketplace_package(
    skill_id: str,
    actor: Actor = Depends(get_actor),
    market: MarketplaceService = Depends(_marketplace),
) -> dict:
    try:
        return market.get_package(actor, skill_id)
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


class PublishBody:
    """上架请求体：evaluation_id 绑定不可变包版本（BUG-07 语义，原样复用）。"""

    def __init__(self, evaluation_id: str) -> None:
        self.evaluation_id = evaluation_id


@router.post("/marketplace/{skill_id}/publish")
async def publish_package(
    skill_id: str,
    request: Request,
    actor: Actor = Depends(csrf_protected),
    market: MarketplaceService = Depends(_marketplace),
) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("evaluation_id"), str):
        raise HTTPException(status_code=422, detail="请求体需为 {\"evaluation_id\": \"...\"}")
    try:
        skill = market.publish(actor, skill_id, payload["evaluation_id"])
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    market.s.commit()  # 与 skills 路由同纪律：写路径成功即提交
    return {"skill_id": skill.id, "state": skill.state,
            "gate_profile": skill.gate_profile,
            "signature_verified": bool(skill.signature_verified),
            "scan_passed": bool(skill.scan_passed)}


@router.post("/marketplace/{skill_id}/install")
async def install_package(
    skill_id: str,
    request: Request,
    actor: Actor = Depends(csrf_protected),
    market: MarketplaceService = Depends(_marketplace),
) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    grant_id = payload.get("grant_id") if isinstance(payload, dict) else None
    if grant_id is not None and not isinstance(grant_id, str):
        raise HTTPException(status_code=422, detail="grant_id 必须是字符串")
    try:
        card = market.install(actor, skill_id, grant_id=grant_id)
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    market.s.commit()
    return card
