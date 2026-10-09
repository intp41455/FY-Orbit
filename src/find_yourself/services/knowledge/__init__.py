"""W3 本地知识库（document RAG）门面。

模块分工（任务书 §3 所有权）：

* :mod:`.ingest` —— 清洗/切片/解析/入库/级联删除
* :mod:`.search` —— owner 隔离的混合检索（FTS5 + LIKE 回退）
* :mod:`.sources` —— 外部知识源适配器（ima 真实可用；百度网盘仅骨架）

本文件提供对外的 :class:`KnowledgeService`（API 层用）与
:func:`register_kb_search_tool` / :func:`run_tool_search`（P1-05 工具注册中心用）。

**工具通道的 owner 归属（重要且已知的限制）**：注册中心的 builtin executor 签名
只有 ``(arguments)``，拿不到 HTTP 请求上下文，所以 ``kb.search`` 工具固定绑定
``Settings.owner_id``（本机主账号，桌面端单用户即正确作用域），并在返回体里显式
标注 ``owner_scope="local_primary"``。**其他用户的文档永远不可见**。多租户下若要
按会话 owner 检索，走 ``POST /api/kb/search``（走 ``get_actor``），不要走工具通道。
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.kb_models import KBDocument
from ..actor import Actor
from ..audit import AuditService
from ..errors import NotFound, ValidationFailed
from . import hub_bridge
from .ingest import KnowledgeIngestService, parse_bytes
from .search import DEFAULT_TOP_K, KnowledgeSearchService, score_chunk, tokenize
from .sources import KnowledgeSource, build_source, list_source_status, secret_store

__all__ = [
    "KnowledgeService",
    "register_kb_search_tool",
    "run_tool_search",
    "set_kb_session_factory",
    "KB_SEARCH_TOOL_NAME",
    "DEFAULT_TOP_K",
    "parse_bytes",
    "tokenize",
    "score_chunk",
    "secret_store",
    "list_source_status",
]

KB_SEARCH_TOOL_NAME = "kb.search"
KB_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 500,
            "description": "检索词（中文/英文皆可）",
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "返回切片数上限，默认 5",
        },
    },
    "required": ["query"],
    # 说明：owner_id 不是参数——服务端绑定本机主账号，调用方无法越权指定。
    "additionalProperties": False,
}


class KnowledgeService:
    """Facade over ingest + search + source adapters."""

    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit
        self.ingest = KnowledgeIngestService(session, audit)
        self.searcher = KnowledgeSearchService(session)

    # -- documents ------------------------------------------------------------ #
    def list_documents(self, actor: Actor, *, owner_id: str) -> list[dict[str, Any]]:
        rows = self.ingest.list_documents(actor, owner_id=owner_id)
        return [
            {
                "id": d.id,
                "name": d.name,
                "source": d.source,
                "external_id": d.external_id,
                "size": d.size,
                "status": d.status,
                "error": d.error,
                "chunk_count": d.chunk_count,
                "version": d.version,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ]

    def ingest_bytes(
        self, actor: Actor, *, owner_id: str, name: str, data: bytes,
        source: str = "local", external_id: str = "",
    ) -> dict[str, Any]:
        doc = self.ingest.ingest_bytes(
            actor, owner_id=owner_id, name=name, data=data,
            source=source, external_id=external_id,
        )
        return _doc_payload(doc)

    def delete_document(self, actor: Actor, doc_id: str) -> dict[str, Any]:
        return self.ingest.delete_document(actor, doc_id)

    def search(
        self, actor: Actor, *, owner_id: str, query: str, top_k: int = DEFAULT_TOP_K,
        document_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        hits = self.searcher.search(
            actor, owner_id=owner_id, query=query, top_k=top_k, document_ids=document_ids
        )
        return {"query": query, "count": len(hits), "results": hits}

    # -- sources -------------------------------------------------------------- #
    def list_sources(
        self, actor: Actor, *, owner_id: str, probe: bool = False,
    ) -> list[dict[str, Any]]:
        """W3 原生源 + hub 里的 knowledge_source 连接（验收 F2 桥接）。

        顺序稳定：先原生（ima / baidu_pan），再按创建时间排 hub 连接。
        重复调用不报错——hub 侧无连接就是不加卡片，不影响原生语义。
        """
        actor.require_authenticated()
        out = list_source_status(probe=probe)
        out.extend(
            hub_bridge.list_hub_source_cards(self.s, owner_id or actor.owner_id)
        )
        return out

    def build_source_for(
        self, actor: Actor, *, owner_id: str, source_id: str,
    ) -> KnowledgeSource:
        """按 source_id 造适配器：``hub:<id>`` 走桥接，其余走 W3 原生路径。

        桥接分支的凭证一律来自 hub 加密存储（连接自带 或 共享凭证库）。
        """
        if hub_bridge.is_hub_source(source_id):
            return hub_bridge.build_hub_source(
                self.s, owner_id or actor.owner_id, source_id
            )
        return build_source(source_id)

    def configure_source(self, source_id: str, values: dict[str, str]) -> dict[str, Any]:
        if hub_bridge.is_hub_source(source_id):
            # hub 源的凭证在中台连接上配置，不走 W3 的凭证卡：
            # 两条写路径并存会让「哪份是真的」变得不可知，故显式拒绝并指路。
            raise ValidationFailed(
                "hub_source_configure_in_hub",
                f"'{source_id}' 是中台连接派生的知识源，凭证请在「超级中台」页该连接上配置",
            )
        if source_id not in {"ima", "baidu_pan"}:
            raise NotFound("unknown_source", f"未知知识源：{source_id}")
        if source_id == "baidu_pan":
            raise ValidationFailed(
                "baidu_pan_not_implemented",
                "百度网盘适配器 v1 仅骨架，不接受凭证配置（避免造成「已接入」的错觉）",
            )
        secret_store.set(source_id, values)
        self.audit.append(
            Actor.owner("system"), "kb.source.configured", source_id,
            {"fields": sorted(k for k, v in values.items() if v)},
        )
        # W6 增补 E 后凭证落 hub Fernet 加密存储——返回值必须匹配真实存放位置，
        # 不得再写死 "memory"（前端据此提示「重启是否失效」）。
        storage, persist_restart = secret_store.storage_mode()
        return {
            "source_id": source_id,
            "configured": True,
            "storage": storage,
            "persist_restart": persist_restart,
        }

    def forget_source(self, source_id: str) -> dict[str, Any]:
        if hub_bridge.is_hub_source(source_id):
            raise ValidationFailed(
                "hub_source_forget_in_hub",
                f"'{source_id}' 的连接与凭证由中台管理，请在「超级中台」页删除该连接",
            )
        removed = secret_store.forget(source_id)
        return {"source_id": source_id, "forgotten": removed}

    def sync_source(
        self, actor: Actor, *, owner_id: str, source_id: str, max_docs: int = 50
    ) -> dict[str, Any]:
        """Pull an adapter into the local index. Real HTTP against the real API.

        ``source_id`` 形如 ``hub:<conn_id>`` 时走 hub 桥接：适配器凭证取自 hub
        加密存储（验收 F2）。文档仍以 ``source=<原始 source_id>`` 入库，
        因此同一个 hub 源重复同步保持幂等。
        """
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        source = self.build_source_for(actor, owner_id=owner_id, source_id=source_id)
        source.require_configured()
        refs = source.list_sources()
        imported = replaced = failed = preview_only = 0
        errors: list[dict[str, str]] = []
        for ref in refs:
            if imported + replaced + failed >= max_docs:
                break
            for raw in source.fetch_document(ref):
                if imported + replaced + failed >= max_docs:
                    break
                if raw.metadata.get("preview_only"):
                    preview_only += 1
                existing = self.s.execute(
                    select(KBDocument).where(
                        KBDocument.owner_id == owner_id,
                        KBDocument.source == source_id,
                        KBDocument.external_id == raw.external_id,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    # 幂等：以 external_id 为键整体重导（对齐设计文档 §5.3）。
                    self.ingest.delete_document(actor, existing.id)
                    replaced += 1
                doc = self.ingest.ingest_text(
                    actor, owner_id=owner_id,
                    name=f"{ref.name} · {raw.name}"[:500],
                    text=raw.text, source=source_id, external_id=raw.external_id,
                )
                if doc.status == "ready":
                    imported += 1
                else:
                    failed += 1
                    errors.append({"name": doc.name, "error": doc.error})
        summary = {
            "source_id": source_id,
            "collections": len(refs),
            "imported": imported,
            "replaced": replaced,
            "failed": failed,
            "preview_only": preview_only,
            "errors": errors,
        }
        self.audit.append(actor, "kb.source.synced", source_id, summary)
        return summary


def _doc_payload(doc: KBDocument) -> dict[str, Any]:
    return {
        "id": doc.id,
        "name": doc.name,
        "source": doc.source,
        "external_id": doc.external_id,
        "size": doc.size,
        "status": doc.status,
        "error": doc.error,
        "chunk_count": doc.chunk_count,
        "version": doc.version,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


# --------------------------------------------------------------------------- #
# P1-05 工具注册中心接线
# --------------------------------------------------------------------------- #

def register_kb_search_tool(registry: Any | None = None) -> dict[str, Any]:
    """Register ``kb.search`` in the P1-05 registry (idempotent)."""
    if registry is None:
        from ..tool_registry import tool_registry as default_registry

        registry = default_registry
    return registry.register(
        name=KB_SEARCH_TOOL_NAME,
        description=(
            "检索本机知识库（W3 文档 RAG）：在用户导入的 .md/.txt/.pdf/.docx "
            "切片与已接入适配器内容中做关键词检索，返回切片正文与来源文档名。"
            "只返回真实检索结果，没有命中就说没有命中。"
        ),
        parameters=KB_TOOL_PARAMETERS,
        entry={"type": "builtin", "executor": KB_SEARCH_TOOL_NAME},
    )


def _local_owner_id() -> str:
    from ...config import settings as load_settings

    return load_settings().owner_id


#: 由 API 层在真实请求里绑定的 session 工厂（app.state.session_maker）。
#: 未绑定时回退到按 Settings.database_url 自建短连接——桌面单库场景等价。
_KB_SESSION_FACTORY: Callable[[], Session] | None = None


def set_kb_session_factory(factory: Callable[[], Session]) -> None:
    """Wiring hook used by ``api/routes/knowledge.py`` (and by tests)."""
    global _KB_SESSION_FACTORY
    _KB_SESSION_FACTORY = factory


def _open_session() -> Session:
    if _KB_SESSION_FACTORY is not None:
        return _KB_SESSION_FACTORY()
    from ...config import settings as load_settings
    from ...db.session import engine_from_url, session_factory  # local: avoid import cycle

    return session_factory(engine_from_url(load_settings().database_url))()


# kb.search 的检索模式与回传标签共用同一常量——二者曾因默认值变更而漂移（标签假称 fts5+lexical）
_KB_TOOL_SEARCH_MODE = "hybrid"


def run_tool_search(arguments: dict[str, Any]) -> dict[str, Any]:
    """``kb.search`` builtin executor body (called by ``tool_registry.invoke``)."""
    query = str(arguments.get("query") or "").strip()
    raw_top_k = arguments.get("top_k", 5)
    try:
        top_k = int(raw_top_k)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("invalid_top_k", "top_k 必须是整数") from exc

    owner_id = _local_owner_id()
    session = _open_session()
    try:
        svc = KnowledgeSearchService(session)
        hits = svc.search(
            Actor.owner(owner_id),
            owner_id=owner_id,
            query=query,
            top_k=top_k,
            mode=_KB_TOOL_SEARCH_MODE,
        )
    finally:
        session.close()
    return {
        "query": query,
        "count": len(hits),
        "owner_scope": "local_primary",
        "engine": _KB_TOOL_SEARCH_MODE,
        "results": [
            {
                "doc_name": h["doc_name"],
                "source": h["source"],
                "seq": h["seq"],
                "score": h["score"],
                "matched_terms": h["matched_terms"],
                "content": h["content"],
            }
            for h in hits
        ],
    }
