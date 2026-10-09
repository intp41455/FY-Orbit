"""插件与模板市场、分层视图与生态兼容 HTTP 接口（A-工具市场-03 · A-开箱模板-04 · A-开箱模板-06 · A-生态兼容-04 · P12）。

端点概览：
--- 插件市场与评分（A-工具市场-03）---
* ``GET  /api/plugins/marketplace``                       — 检索（带评分、支持贝叶斯得分排位）
* ``GET  /api/plugins/marketplace/{skill_id}``            — 插件详情（带评分统计与当前用户打分）
* ``POST /api/plugins/marketplace/{skill_id}/publish``    — 上架（内部走 promote 门禁）
* ``POST /api/plugins/marketplace/{skill_id}/install``    — 安装（需有效授权）
* ``POST /api/plugins/marketplace/{skill_id}/rate``       — 打分与评语（1-5 星，好用被顶上来）
* ``GET  /api/plugins/marketplace/{skill_id}/ratings``    — 评价列表与分布明细

--- 模板分层与市场（A-开箱模板-04 🔒 GATE · A-开箱模板-06）---
* ``GET  /api/plugins/templates/hierarchy``               — 模板三层架构视图（新手默认/进阶可换/技术可拆）
* ``GET  /api/plugins/templates/market``                  — 模板市场检索（带评分与排位分）
* ``POST /api/plugins/templates/impact-preview``          — 模板切换影响预检（与保存点联动）
* ``POST /api/plugins/templates/{template_id}/rate``      — 模板打分
* ``GET  /api/plugins/templates/{template_id}/ratings``   — 模板评价列表
* ``GET  /api/plugins/templates/{template_id}/export``    — 模板导出为 .fytemplate 单文件
* ``POST /api/plugins/templates/security-review``         — 模板导入安全审查门禁
* ``POST /api/plugins/templates/import``                  — 模板安全导入与工具权限裁剪

--- Cursor 级代码上下文极致理解（A-生态兼容-04）---
* ``POST /api/plugins/cursor/analyze``                    — 多语言代码符号与结构解析
* ``POST /api/plugins/cursor/search``                     — 相关性上下文切片检索
* ``POST /api/plugins/cursor/assemble``                   — 字符预算自适应高密度上下文装配
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...services.actor import Actor
from ...services.errors import DomainError
from ...services.grant import GrantService
from ...services.marketplace import DEFAULT_PAGE_LIMIT, MarketplaceService
from ...services.skill import SkillService
from ...services.templates.cursor_context import CursorContextEngine
from ...services.templates.market import TemplateMarketService
from ...services.templates.scaffold import ScaffoldTemplateService
from ..deps import csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/plugins", tags=["plugin-marketplace"])


def _marketplace(services=Depends(get_services)) -> MarketplaceService:
    return MarketplaceService(
        services.session,
        services.audit,
        skills=SkillService(services.session, services.audit),
        grants=GrantService(services.session, services.audit),
    )


def _template_market(services=Depends(get_services)) -> TemplateMarketService:
    scaffold = ScaffoldTemplateService(audit=services.audit)
    return TemplateMarketService(scaffold=scaffold, audit=services.audit)


_cursor_engine = CursorContextEngine()


# ---------------------------------------------------------------------------
# 1. 插件市场与评分路由（A-工具市场-03）
# ---------------------------------------------------------------------------


@router.get("/marketplace")
async def list_marketplace(
    actor: Actor = Depends(get_actor),
    market: MarketplaceService = Depends(_marketplace),
    query: str = "",
    domain: str | None = None,
    capability: str | None = None,
    sort_by: str = "score",
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> dict:
    try:
        return market.list_packages(
            actor,
            query=query,
            domain=domain,
            capability=capability,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
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
    market.s.commit()
    return {
        "skill_id": skill.id,
        "state": skill.state,
        "gate_profile": skill.gate_profile,
        "signature_verified": bool(skill.signature_verified),
        "scan_passed": bool(skill.scan_passed),
    }


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


@router.post("/marketplace/{skill_id}/rate")
async def rate_marketplace_package(
    skill_id: str,
    request: Request,
    actor: Actor = Depends(csrf_protected),
    market: MarketplaceService = Depends(_marketplace),
) -> dict:
    """对插件包打分（1.0 ~ 5.0 星）并提交评语。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    if not isinstance(payload, dict) or "rating" not in payload:
        raise HTTPException(status_code=422, detail="请求体需包含 rating 字段")
    rating_val = payload["rating"]
    comment_val = payload.get("comment")
    try:
        res = market.rate_package(actor, skill_id, rating_val, comment=comment_val)
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    market.s.commit()
    return res


@router.get("/marketplace/{skill_id}/ratings")
async def get_marketplace_package_ratings(
    skill_id: str,
    actor: Actor = Depends(get_actor),
    market: MarketplaceService = Depends(_marketplace),
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """获取插件包的评价列表与星级分布统计。"""
    try:
        return market.get_package_ratings(actor, skill_id, limit=limit, offset=offset)
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


# ---------------------------------------------------------------------------
# 2. 模板分层与模板市场路由（A-开箱模板-04 🔒 GATE · A-开箱模板-06）
# ---------------------------------------------------------------------------


@router.get("/templates/hierarchy")
async def get_templates_hierarchy(
    actor: Actor = Depends(get_actor),
    tpl_market: TemplateMarketService = Depends(_template_market),
) -> dict:
    """获取模板三层架构（新手默认/进阶可换/技术可拆一级入口）。"""
    try:
        return tpl_market.get_hierarchy(actor)
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.get("/templates/market")
async def list_templates_market(
    actor: Actor = Depends(get_actor),
    tpl_market: TemplateMarketService = Depends(_template_market),
    query: str = "",
    scenario: str | None = None,
    layer: str | None = None,
    sort_by: str = "score",
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """模板市场检索。带评分加权与好评优先排序。"""
    try:
        return tpl_market.list_market(
            actor,
            query=query,
            scenario=scenario,
            layer=layer,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.post("/templates/impact-preview")
async def preview_template_switch_impact(
    request: Request,
    actor: Actor = Depends(get_actor),
    tpl_market: TemplateMarketService = Depends(_template_market),
) -> dict:
    """预览模板切换的影响（成员增减、工具白名单差异、保存点联动）。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    if not isinstance(payload, dict) or "from_template_id" not in payload or "to_template_id" not in payload:
        raise HTTPException(status_code=422, detail="请求体需包含 from_template_id 与 to_template_id")
    try:
        return tpl_market.preview_switch_impact(
            actor, payload["from_template_id"], payload["to_template_id"]
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.post("/templates/{template_id}/rate")
async def rate_template(
    template_id: str,
    request: Request,
    actor: Actor = Depends(csrf_protected),
    tpl_market: TemplateMarketService = Depends(_template_market),
) -> dict:
    """对系统模板打分。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    if not isinstance(payload, dict) or "rating" not in payload:
        raise HTTPException(status_code=422, detail="请求体需包含 rating 字段")
    try:
        return tpl_market.rate_template(
            actor, template_id, payload["rating"], comment=payload.get("comment")
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.get("/templates/{template_id}/ratings")
async def get_template_ratings(
    template_id: str,
    actor: Actor = Depends(get_actor),
    tpl_market: TemplateMarketService = Depends(_template_market),
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """获取系统模板的评价列表与分布统计。"""
    try:
        return tpl_market.get_template_ratings(actor, template_id, limit=limit, offset=offset)
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.get("/templates/{template_id}/export")
async def export_template_package(
    template_id: str,
    actor: Actor = Depends(get_actor),
    tpl_market: TemplateMarketService = Depends(_template_market),
    author: str | None = None,
    notes: str | None = None,
) -> dict:
    """导出模板为单文件自描述 .fytemplate 包（含 SHA-256 校验和）。"""
    try:
        return tpl_market.export_package(actor, template_id, author=author, notes=notes)
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.post("/templates/security-review")
async def security_review_template(
    request: Request,
    actor: Actor = Depends(get_actor),
    tpl_market: TemplateMarketService = Depends(_template_market),
) -> dict:
    """导入前强制安全审查门禁（高危工具扫描、权限审计、防篡改校验）。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    try:
        return tpl_market.security_review(actor, payload)
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


@router.post("/templates/import")
async def import_template_package(
    request: Request,
    actor: Actor = Depends(csrf_protected),
    tpl_market: TemplateMarketService = Depends(_template_market),
) -> dict:
    """安全导入模板包（支持工具白名单逐项裁剪与确认）。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    if not isinstance(payload, dict) or "package" not in payload:
        raise HTTPException(status_code=422, detail="请求体需为 {\"package\": {...}, \"confirmed_tools\": [...]}")
    pkg_data = payload["package"]
    confirmed_tools = payload.get("confirmed_tools")
    custom_name = payload.get("custom_name")
    try:
        return tpl_market.import_package(
            actor, pkg_data, confirmed_tools=confirmed_tools, custom_name=custom_name
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc


# ---------------------------------------------------------------------------
# 3. Cursor 级代码上下文极致理解路由（A-生态兼容-04）
# ---------------------------------------------------------------------------


@router.post("/cursor/analyze")
async def cursor_analyze_symbols(
    request: Request,
    actor: Actor = Depends(get_actor),
) -> dict:
    """分析代码文件的符号表（类、接口、函数、方法、签名、Docstring）。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    file_path = payload.get("file_path", "source.py")
    code = payload.get("code", "")
    symbols = _cursor_engine.extract_symbols(file_path, code)
    from dataclasses import asdict
    return {
        "file_path": file_path,
        "symbol_count": len(symbols),
        "symbols": [asdict(s) for s in symbols],
    }


@router.post("/cursor/search")
async def cursor_search_slices(
    request: Request,
    actor: Actor = Depends(get_actor),
) -> dict:
    """按查询检索最相关的代码上下文切片。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    query = payload.get("query", "")
    files = payload.get("files", {})
    limit = int(payload.get("limit", 10))
    from dataclasses import asdict
    slices = _cursor_engine.search_context_slices(files, query, limit=limit)
    return {
        "query": query,
        "match_count": len(slices),
        "slices": [asdict(s) for s in slices],
    }


@router.post("/cursor/assemble")
async def cursor_assemble_prompt_context(
    request: Request,
    actor: Actor = Depends(get_actor),
) -> dict:
    """按字符/Token 预算组装 Cursor 级高密度代码上下文 Markdown 提示词。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    query = payload.get("query", "")
    files = payload.get("files", {})
    max_chars = int(payload.get("max_chars", 4000))
    slices = _cursor_engine.search_context_slices(files, query, limit=20)
    return _cursor_engine.assemble_context_prompt(slices, max_chars=max_chars)
