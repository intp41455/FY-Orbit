"""W3 本地知识库 API（导入 / 检索 / 适配器）。

鉴权与 owner 隔离（任务书 §4）：

* 所有端点都要身份（``get_actor``）；写操作还要 CSRF/Origin（``csrf_protected``）。
* owner 一律取 ``actor.owner_id``（服务端解析），**不接受请求体里的 owner_id**。
* 跨 owner 的文档一律 404，不泄露「存在但不属于你」。

上传协议：``POST /api/kb/documents?name=foo.md`` + **原始字节请求体**
（``Content-Type: application/octet-stream``）。选它而不是 multipart 是为了不在
后端引入 ``python-multipart`` 依赖；前端 ``web/src/api/knowledge.ts`` 用
``fetch`` + ``credentials: same-origin`` + ``X-CSRF-Token`` 直接发字节。

诚实性（总纲铁律 3）：解析失败返回 200 + ``status='failed'`` + ``error`` 原因，
让前端如实展示；只有请求本身不合法（扩展名/空文件/超 20MB）才返回 4xx。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ...services.knowledge import (
    DEFAULT_TOP_K,
    KnowledgeService,
    register_kb_search_tool,
    set_kb_session_factory,
)
from ...services.knowledge.ingest import MAX_FILE_BYTES, SUPPORTED_EXTENSIONS
from ...services.knowledge import hub_bridge
from ...services.knowledge.graph import GraphService
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/kb", tags=["knowledge"])

# P1-05 注册中心接线（幂等）：导入本模块即注册 kb.search builtin executor。
register_kb_search_tool()


def kb_services(request: Request, svc: Services = Depends(get_services)) -> KnowledgeService:
    """Per-request facade + bind the session factory the tool executor reuses."""
    set_kb_session_factory(request.app.state.session_maker)
    return KnowledgeService(svc.session, svc.audit)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=50)
    document_ids: list[str] | None = Field(default=None, max_length=50)


class ConfigureSourceRequest(BaseModel):
    api_key: str = Field(default="", max_length=400)
    base_url: str = Field(default="", max_length=400)
    app_key: str = Field(default="", max_length=400)
    app_secret: str = Field(default="", max_length=400)
    redirect_uri: str = Field(default="", max_length=400)
    # B1 · G2：ima 凭证三件套（App ID / API Key / Secret Key）走设置页 →
    # hub Fernet 加密存储（scope ima），MCP 通道发起连接时并入 env / 头。
    app_id: str = Field(default="", max_length=400)
    secret_key: str = Field(default="", max_length=400)


# --------------------------------------------------------------------------- #
# 文档
# --------------------------------------------------------------------------- #

@router.get("/documents")
async def list_documents(
    actor: Actor = Depends(get_actor),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    docs = kb.list_documents(actor, owner_id=actor.owner_id)
    return {
        "documents": docs,
        "count": len(docs),
        "limits": {
            "max_bytes": MAX_FILE_BYTES,
            "extensions": list(SUPPORTED_EXTENSIONS),
        },
    }


@router.post("/documents")
async def upload_document(
    request: Request,
    name: str = Query(min_length=1, max_length=500),
    actor: Actor = Depends(csrf_protected),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    data = await request.body()
    doc = kb.ingest_bytes(actor, owner_id=actor.owner_id, name=name, data=data)
    kb.s.commit()
    # 200 + status='failed' 是**有意的诚实返回**（前端要显示失败原因）。
    return {"document": doc}


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    actor: Actor = Depends(csrf_protected),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    result = kb.delete_document(actor, doc_id)
    kb.s.commit()
    return result


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #

@router.post("/search")
async def search_knowledge(
    body: SearchRequest,
    actor: Actor = Depends(get_actor),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    return kb.search(
        actor,
        owner_id=actor.owner_id,
        query=body.query,
        top_k=body.top_k,
        document_ids=body.document_ids,
    )


@router.get("/documents/{doc_id}/chunks")
async def list_document_chunks(
    doc_id: str,
    actor: Actor = Depends(get_actor),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    """Chunks of one document (owner-scoped) — 用于前端「查看切片」。"""
    docs = {d["id"]: d for d in kb.list_documents(actor, owner_id=actor.owner_id)}
    if doc_id not in docs:
        from ...services.errors import NotFound

        raise NotFound("kb_document_not_found", "文档不存在")
    from sqlalchemy import select as sa_select

    from ...db.kb_models import KBChunk

    rows = kb.s.execute(
        sa_select(KBChunk)
        .where(KBChunk.doc_id == doc_id, KBChunk.owner_id == actor.owner_id)
        .order_by(KBChunk.seq)
    ).scalars().all()
    return {
        "doc_id": doc_id,
        "chunks": [
            {"id": r.id, "seq": r.seq, "content": r.content, "content_hash": r.content_hash}
            for r in rows
        ],
        "count": len(rows),
    }


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #

@router.get("/sources")
async def list_sources(
    probe: bool = Query(default=False),
    actor: Actor = Depends(get_actor),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    actor.require_authenticated()
    # W3 原生源（ima / baidu_pan）+ hub 里 kind=knowledge_source 的连接（验收 F2）。
    # 桥接在 KnowledgeService.list_sources 内完成；hub 侧无连接时不加卡片，
    # 原生语义与 W3 测试完全不受影响。
    sources = kb.list_sources(actor, owner_id=actor.owner_id, probe=probe)
    return {"sources": sources, "count": len(sources), "probed": probe}


@router.post("/sources/{source_id}/configure")
async def configure_source(
    source_id: str,
    body: ConfigureSourceRequest,
    actor: Actor = Depends(csrf_protected),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    values = body.model_dump(exclude_none=True)
    result = kb.configure_source(source_id, values)
    kb.s.commit()
    return result


@router.delete("/sources/{source_id}")
async def forget_source(
    source_id: str,
    actor: Actor = Depends(csrf_protected),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    result = kb.forget_source(source_id)
    kb.s.commit()
    return result


@router.post("/sources/{source_id}/probe")
async def probe_source(
    source_id: str,
    actor: Actor = Depends(csrf_protected),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    """Real reachability probe against the adapter (no network when not configured).

    ``hub:<id>`` 走桥接：拿 hub 凭证造适配器后真实探活，失败原因原样返回。
    """
    actor.require_authenticated()
    if hub_bridge.is_hub_source(source_id):
        source = kb.build_source_for(actor, owner_id=actor.owner_id, source_id=source_id)
        return source.status(probe=True)
    for status in kb.list_sources(actor, owner_id=actor.owner_id, probe=True):
        if status["source_id"] == source_id:
            return status
    return {"source_id": source_id, "available": False, "detail": "未知知识源"}


@router.post("/sources/{source_id}/sync")
async def sync_source(
    source_id: str,
    actor: Actor = Depends(csrf_protected),
    kb: KnowledgeService = Depends(kb_services),
) -> dict:
    summary = kb.sync_source(actor, owner_id=actor.owner_id, source_id=source_id)
    kb.s.commit()
    return summary


# --------------------------------------------------------------------------- #
# 包6 · A-立体3D知识图谱-04 · 图谱端点（数据层；3D 渲染归前端 canvas 星图）
#
# 挂载说明：本模块的 ``router`` 前缀是 /api/kb（存量端点契约不变），而图谱端点
# 规格是 /api/knowledge/graph —— 故单列 ``graph_router``（prefix=/api/knowledge），
# 由 ``api/routes/rag_presets.py`` 的组合路由 include 装配（routes/__init__ 的
# 约定式自动发现会挂载 rag_presets.router）。图谱只存实体/关系/指针，证据文本
# 按指针现取（``with_evidence_text=true`` 时读时解引用），原文不搬运。
# --------------------------------------------------------------------------- #

graph_router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class GraphBuildRequest(BaseModel):
    doc_ids: list[str] | None = Field(default=None, max_length=100)
    extractor: str = Field(default="rules", max_length=32)
    rebuild: bool = True


@graph_router.get("/graph")
async def knowledge_graph(
    kind: str | None = Query(default=None, max_length=32),
    limit_nodes: int = Query(default=300, ge=1, le=1000),
    limit_edges: int = Query(default=600, ge=1, le=2000),
    with_evidence_text: bool = Query(default=False),
    actor: Actor = Depends(get_actor),
    services: Services = Depends(get_services),
) -> dict:
    """读当前 owner 的知识图谱（节点/边/计数；空库返回空图 + 重建提示）。"""
    actor.require_authenticated()
    svc = GraphService(services.session)
    return svc.read_graph(
        actor,
        owner_id=actor.owner_id,
        kind=kind,
        limit_nodes=limit_nodes,
        limit_edges=limit_edges,
        with_evidence_text=with_evidence_text,
    )


@graph_router.post("/graph/build")
async def rebuild_knowledge_graph(
    body: GraphBuildRequest,
    actor: Actor = Depends(csrf_protected),
    services: Services = Depends(get_services),
) -> dict:
    """重建图谱：扫描 ready 切片 → 抽取（rules 离线确定性 / llm 可插拔）→ upsert。"""
    actor.require_authenticated()
    svc = GraphService(services.session)
    summary = svc.build(
        actor,
        owner_id=actor.owner_id,
        doc_ids=body.doc_ids,
        extractor_name=body.extractor,
        rebuild=body.rebuild,
    )
    services.session.commit()
    return summary