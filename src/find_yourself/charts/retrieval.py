"""Dual-path retrieval service for astrological and psychological interpretation.

Architectural boundaries:
1. Public Web Path: retrieves public cultural/encyclopedic references with real citation URLs, titles, and timestamps.
2. Personal Memory Path: strictly scoped to caller's owner_id and authorized domain using Find Yourself MemoryService.
3. Complete separation: private memory content is never leaked into public queries or logs.

包6 · A-命理画像-01（2026-10-06）：公共知识路从**硬编码常量**改为**真调知识库
检索**（云盘 RAG 路）：owner 的 kb_chunks（含百度网盘/ima 同步入库的资料）经
``services/knowledge/search.py`` 的公开混合检索 API 召回，切片原文作为引用
（title=文档名、source=入库来源、confidence=kb_rag）。owner 从调用方传入
（``actor`` 或 ``owner_id``）；**拿不到 owner 上下文时**（如 charts 路由未接线）
显式走 ``_curated_fallback`` 并打 ``degraded=True`` 标注——这是显式降级，不是
把旧常量继续伪装成检索结果。
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..services.actor import Actor
from ..services.memory import MemoryService


class DualPathRetrievalService:
    """Manages dual-path retrieval: verified public sources + authorized personal memory."""

    def __init__(
        self,
        session: Session,
        memory_service: MemoryService | None = None,
        *,
        owner_id: str | None = None,
    ):
        self.session = session
        self.memory = memory_service
        # 可选：装配点直接绑定 owner（等价于每次调用传 owner_id）。
        self.default_owner_id = owner_id

    # ------------------------------------------------------------------ #
    # 路径 1：公共知识（云盘 RAG 路 —— A-命理画像-01）
    # ------------------------------------------------------------------ #
    def retrieve_public_knowledge(
        self,
        query: str,
        system: str = "bazi",
        *,
        actor: Actor | None = None,
        owner_id: str | None = None,
        top_k: int = 4,
    ) -> list[dict[str, Any]]:
        """公共知识引用：优先真调知识库检索（云盘资料→RAG），owner 缺失才降级。

        * owner 解析顺序：显式 ``actor``（owner 身份）> 显式 ``owner_id`` >
          构造时绑定的 ``owner_id``；
        * 有 owner：走 ``KnowledgeSearchService.search``（owner 谓词先于排序，
          与 kb 检索同一条授权边界）；**空结果就是空结果**，绝不编造引用；
        * 无 owner 上下文：``_curated_fallback``（confidence=curated_fallback、
          degraded=True），保证 charts 路由现有行为不炸但如实标注降级。
        """
        resolved_owner = self._resolve_owner(actor, owner_id)
        if resolved_owner:
            hits = self._kb_rag_hits(query, system, resolved_owner, actor=actor, top_k=top_k)
            if hits is not None:
                return hits
        return self._curated_fallback(query, system)

    def _resolve_owner(self, actor: Actor | None, owner_id: str | None) -> str:
        if actor is not None:
            if getattr(actor, "subject_type", "") == "owner" and getattr(actor, "owner_id", ""):
                return actor.owner_id
            return ""
        if owner_id:
            return owner_id
        return self.default_owner_id or ""

    def _kb_rag_hits(
        self,
        query: str,
        system: str,
        owner_id: str,
        *,
        actor: Actor | None,
        top_k: int,
    ) -> list[dict[str, Any]] | None:
        """真调 kb 检索。返回 None 表示检索基建不可用（调用方决定是否降级）。"""
        try:
            from ..services.knowledge.search import KnowledgeSearchService

            search_actor = actor if actor is not None else Actor.owner(owner_id)
            hits = KnowledgeSearchService(self.session).search(
                search_actor,
                owner_id=owner_id,
                query=(query or "").strip(),
                top_k=max(1, min(int(top_k), 20)),
            )
        except Exception:
            # 与个人记忆路同一容错约定：检索基建故障不 500 整个解读，
            # 返回 None 让调用方走显式降级（不算「检索到了空」）。
            return None

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        citations: list[dict[str, Any]] = []
        for h in hits:
            source = str(h.get("source") or "kb")
            citations.append(
                {
                    "title": str(h.get("doc_name") or ""),
                    "source": source,
                    "url": "",
                    "snippet": str(h.get("content") or "")[:200],
                    "retrieved_at": now_iso,
                    "query": query,
                    "confidence": "kb_rag",
                    "path": "cloud_rag" if source not in ("local",) else "local_rag",
                    "chunk_id": h.get("chunk_id"),
                    "doc_id": h.get("doc_id"),
                    "score": h.get("score"),
                    "degraded": False,
                }
            )
        return citations

    @staticmethod
    def _curated_fallback(query: str, system: str) -> list[dict[str, Any]]:
        """显式降级：无 owner 上下文（kb 检索需要 owner 谓词）时的精选常量引用。

        与 A-命理画像-01 之前的唯一区别是**诚实标注**：confidence=
        ``curated_fallback``、``degraded=True``——绝不再冒充「检索结果」。
        生产链路接线（charts 路由传 actor）后此分支即不再触达。
        """
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if system == "bazi":
            entries = [
                (
                    "《子平真诠》论十神格局与用神",
                    "古典文献·四库全书子部",
                    "https://ctext.org/wiki.pl?if=gb&chapter=382910",
                    "八字用神，专求月令。以日干配月支，而生克不同，格局分焉。财官印食为四吉神，煞伤劫刃为四凶神。",
                    "authoritative_classic",
                ),
                (
                    "《滴天髓阐微》论五行生克与中和",
                    "古典文献·清代任铁樵注本",
                    "https://ctext.org/wiki.pl?if=gb&chapter=249012",
                    "戴九履一，左三右七，二四为肩，六八为足。阴阳顺逆之说，洛书之义也。五行贵在中和，过旺过弱皆宜调候。",
                    "authoritative_classic",
                ),
                (
                    "荣格《共时性：非因果联系的原则》与占星原型",
                    "现代心理学分析·Routledge学术出版社",
                    "https://www.routledge.com/Synchronicity-An-Acausal-Connecting-Principle/Jung/p/book/9780415730242",
                    "卡尔·荣格提出共时性原理，将传统占星学视为无意识投射与心理原型在特定时间节点的时间性质象征，而非物质因果决定论。",
                    "academic_reference",
                ),
            ]
        else:
            entries = [
                (
                    "NASA Planetary Ephemeris DE440/441 Documentation",
                    "NASA Jet Propulsion Laboratory (JPL)",
                    "https://ssd.jpl.nasa.gov/planets/eph_export.html",
                    "High-precision solar system planetary positions calculated from numerical integration based on relativistic equations of motion.",
                    "scientific_ephemeris",
                ),
                (
                    "Dane Rudhyar: The Astrology of Personality (心理占星学导论)",
                    "心理占星经典文献",
                    "https://www.worldcat.org/title/astrology-of-personality/oclc/1036814",
                    "倡导人本主义与荣格心理学视角的星盘解读，强调命盘是个人内在心理冲突、潜能整合与个性化进程的象征地图。",
                    "psychological_astrology",
                ),
            ]
        return [
            {
                "title": title,
                "source": source,
                "url": url,
                "snippet": snippet,
                "retrieved_at": now_iso,
                "query": query,
                # provenance 如实降级标注：confidence 统一 curated_fallback，
                # 原分级保存在 curated_tier（authoritative_classic 等）。
                "confidence": "curated_fallback",
                "curated_tier": confidence,
                "path": "curated_fallback",
                "degraded": True,
            }
            for title, source, url, snippet, confidence in entries
        ]

    # ------------------------------------------------------------------ #
    # 路径 2：授权个人记忆（MemoryService 路，原样保留）
    # ------------------------------------------------------------------ #
    def retrieve_personal_memory(
        self,
        actor: Actor,
        domain: str = "personal",
        query: str = "self traits",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve authorized personal memories bounded strictly by tenant and domain."""
        actor.require_owner()

        # If memory service is wired, query memories scoped to owner and domain
        if not self.memory:
            return []

        try:
            memories = self.memory.search(
                consumer_domain=domain,
                query=query,
                limit=limit,
                owner_id=actor.owner_id,
            )
            return [
                {
                    "record_id": m.id,
                    "domain": m.domain,
                    "content": m.content,
                    "version": getattr(m, "version", 1),
                    "created_at": getattr(m, "created_at", None),
                }
                for m in memories
            ]
        except Exception:
            # Fallback safe empty list on search error
            return []
