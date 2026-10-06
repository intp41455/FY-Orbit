"""W3 知识库检索：owner 隔离的混合检索（倒排关键词 + 词频/位置 rerank）。

检索顺序严格遵守 FROZEN_CONTRACT §8.2 / BUG-11 教训：**授权谓词先于排序**。

1. 候选集：``kb_chunks.owner_id == owner_id``（ORM 层强制），并且只保留
   ``kb_documents.status == 'ready'`` 的切片；owner_id 是**签名必填参数**，
   没有它直接抛 ``ValidationFailed``——与 ``services/memory.py`` 的教训一致。
2. 候选召回（双路，自动降级）：
   * SQLite 且 FTS5 可用 → ``kb_chunks_fts``（trigram 分词，中文子串可命中）；
   * 否则 → ``LIKE`` 回退逐词过滤。
   两条路都拿不到候选时**返回空列表**，绝不「假装有结果」。
3. 排序：词频重叠 + 标题命中 + 位置衰减 + 长度归一（纯词法，确定性，可解释）。

混合检索 v2（补齐包2 · A-向量库-07/08，本文件后半段）：

* 向量召回路：Embedding 可插拔（``services.knowledge.embeddings``，默认离线
  hash 嵌入）+ 向量后端可插拔（``services.knowledge.vectorstores``，默认
  SQLite+sqlite-vec 内嵌后端，vec0 虚表运行时建表）；
* 双路融合：RRF（默认）与加权公式可配置（环境变量 / 调用参数）；
* Rerank 可插拔：``Reranker`` 接口，内置 noop / lexical，注册表可扩展；
* 三种模式：``lexical``（纯词法，v1 语义原样保留）/ ``vector`` / ``hybrid``
  （默认，可用 ``FIND_YOURSELF_KB_RETRIEVAL_MODE`` 切换）。向量路在任何环节
  不可用（缺 sqlite-vec、远程嵌入无 key、索引失败）时**自动降级词法路**，
  并在 debug 诊断里如实记录降级原因——绝不中断、绝不假装。

公开 API 形状（包6 消费）见模块末尾「混合检索公开 API」注释与交付报告。
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Iterable

from sqlalchemy import or_, select, text as sql_text
from sqlalchemy.orm import Session

from ...db.kb_models import KBDocument, KBChunk
from ..actor import Actor
from ..errors import ValidationFailed
from .embeddings import EmbeddingProvider, resolve_default_embedding
from .vectorstores import (
    VectorFilterUnsupported,
    VectorStore,
    create_vector_store,
)
from .vectorstores.base import VectorRecord

DEFAULT_TOP_K = 8
MAX_TOP_K = 50
_CANDIDATE_LIMIT = 400
#: 命中词覆盖率下限：中文逐字分词会带出大量单字，只有「法」这种单字命中不算数
#: （否则“区块链共识算法”会因为命中一个“法”就召回一堆无关切片）。
MIN_QUERY_COVERAGE = 0.34

_LATIN_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

FTS_TABLE = "kb_chunks_fts"


# --------------------------------------------------------------------------- #
# 分词（中文逐字 + 二元组，拉丁词）
# --------------------------------------------------------------------------- #

def tokenize(text: str) -> list[str]:
    """Deterministic lexical tokenizer (no model, no randomness)."""
    lowered = text.lower()
    tokens: list[str] = list(_LATIN_RE.findall(lowered))
    for run in _CJK_RE.findall(lowered):
        tokens.extend(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return [t for t in tokens if t]


def _query_terms(query: str) -> list[str]:
    """Distinct, length-filtered query terms (CJK >=1 char, latin >=2 chars)."""
    seen: list[str] = []
    for tok in tokenize(query):
        if _CJK_RE.fullmatch(tok):
            ok = len(tok) >= 1
        else:
            ok = len(tok) >= 2
        if ok and tok not in seen:
            seen.append(tok)
    return seen


# --------------------------------------------------------------------------- #
# FTS5 影子索引（SQLite 惰性建表；不可用则回退 LIKE）
# --------------------------------------------------------------------------- #

def _is_sqlite(session: Session) -> bool:
    return session.get_bind().dialect.name == "sqlite"


def ensure_fts_table(session: Session) -> bool:
    """Create the FTS5 shadow table if missing. False ⇒ caller must use LIKE.

    DDL runs inside a SAVEPOINT: a SQLite build without FTS5 must not roll back
    the caller's pending ingest work.
    """
    if not _is_sqlite(session):
        return False
    try:
        with session.begin_nested():
            session.execute(
                sql_text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
                    "USING fts5(chunk_id UNINDEXED, doc_id UNINDEXED, owner_id UNINDEXED, "
                    "content, tokenize='trigram')"
                )
            )
        return True
    except Exception:  # noqa: BLE001 — SQLite 未编译 FTS5：回退 LIKE，不致命
        return False


def index_chunks_fts(session: Session, chunks: Iterable[KBChunk]) -> None:
    if not _is_sqlite(session):
        return
    payload = [(c.id, c.doc_id, c.owner_id, c.content) for c in chunks]
    if not payload:
        return
    try:
        with session.begin_nested():
            session.execute(
                sql_text(
                    f"INSERT INTO {FTS_TABLE} (chunk_id, doc_id, owner_id, content) "
                    "VALUES (:cid, :did, :oid, :content)"
                ),
                [
                    {"cid": cid, "did": did, "oid": oid, "content": content}
                    for cid, did, oid, content in payload
                ],
            )
    except Exception:  # noqa: BLE001 — 索引失败不阻塞入库，检索自动走 LIKE
        return


def delete_chunks_fts(session: Session, chunk_ids: Iterable[str]) -> bool:
    ids = [c for c in chunk_ids if c]
    if not ids or not _is_sqlite(session):
        return False
    try:
        with session.begin_nested():
            session.execute(
                sql_text(f"DELETE FROM {FTS_TABLE} WHERE chunk_id IN :ids"),
                {"ids": tuple(ids)},
            )
        return True
    except Exception:  # noqa: BLE001
        return False


def _fts_candidates(session: Session, owner_id: str, phrase: str) -> set[str] | None:
    """Owner-filtered FTS5 candidate ids, or None when FTS5 is unusable."""
    if not _is_sqlite(session) or len(phrase) < 3:
        return None
    if not ensure_fts_table(session):
        return None
    try:
        rows = session.execute(
            sql_text(
                f"SELECT chunk_id FROM {FTS_TABLE} "
                f"WHERE {FTS_TABLE} MATCH :q AND owner_id = :owner LIMIT :lim"
            ),
            {"q": f'"{phrase}"', "owner": owner_id, "lim": _CANDIDATE_LIMIT},
        ).all()
    except Exception:  # noqa: BLE001
        return None
    return {r[0] for r in rows}


def _like_candidates(session: Session, owner_id: str, terms: list[str]) -> set[str]:
    """LIKE fallback: OR of escaped per-term substring matches (owner-scoped)."""
    clauses = []
    for term in terms:
        safe = term.replace("%", r"\%").replace("_", r"\_")
        clauses.append(KBChunk.content.like(f"%{safe}%", escape="\\"))
    if not clauses:
        return set()
    rows = session.execute(
        select(KBChunk.id)
        .join(KBDocument, KBDocument.id == KBChunk.doc_id)
        .where(
            KBChunk.owner_id == owner_id,
            KBDocument.status == "ready",
            or_(*clauses),
        )
        .limit(_CANDIDATE_LIMIT)
    ).all()
    return {r[0] for r in rows}


# --------------------------------------------------------------------------- #
# 检索服务
# --------------------------------------------------------------------------- #

class KnowledgeSearchService:
    """Owner-isolated hybrid retrieval over ``kb_chunks``."""

    def __init__(self, session: Session):
        self.s = session

    def search(
        self,
        actor: Actor,
        *,
        owner_id: str,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        document_ids: list[str] | None = None,
        mode: str | None = None,
        fusion: str | None = None,
        rrf_k: int | None = None,
        weights: tuple[float, float] | None = None,
        reranker: Reranker | None = None,
        include_debug: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """混合检索。

        旧调用（只传 ``actor/owner_id/query/top_k/document_ids``）形状与 v1
        完全一致：返回 ``list[dict]``，item 含 ``chunk_id/doc_id/doc_name/
        source/seq/content/content_hash/score/matched_terms``，按 score 降序。
        新增可选参数：

        * ``mode``：``lexical`` | ``vector`` | ``hybrid``（缺省读
          ``FIND_YOURSELF_KB_RETRIEVAL_MODE``，再缺省 ``hybrid``）；
        * ``fusion``：``rrf`` | ``weighted``（缺省读 ``FIND_YOURSELF_KB_FUSION``）；
        * ``rrf_k`` / ``weights``：融合公式参数（缺省读环境变量）；
        * ``reranker``：:class:`Reranker` 实例（缺省读 ``FIND_YOURSELF_KB_RERANKER``，
          默认 noop=不重排）；
        * ``include_debug``：True 时返回 ``{"query","mode","fusion","count",
          "results","debug"}``，``debug`` 含两路命中、融合序、重排前后对比
          与降级 warnings（A/B 对比与排查用）。
        """
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "kb search requires an owner_id")
        norm = (query or "").strip()
        if not norm:
            raise ValidationFailed("query_required", "检索词不能为空")
        top_k = max(1, min(int(top_k or DEFAULT_TOP_K), MAX_TOP_K))
        terms = _query_terms(norm)
        if not terms:
            raise ValidationFailed("query_too_short", "检索词过短（中文至少 1 个字/英文至少 2 个字母）")

        mode = _env_choice(RETRIEVAL_MODE_ENV, DEFAULT_RETRIEVAL_MODE, RETRIEVAL_MODES) \
            if mode is None else mode.strip().lower()
        if mode not in RETRIEVAL_MODES:
            raise ValidationFailed("invalid_retrieval_mode", f"未知检索模式：{mode}")
        fusion = _env_choice(FUSION_MODE_ENV, DEFAULT_FUSION_MODE, FUSION_MODES) \
            if fusion is None else fusion.strip().lower()
        if fusion not in FUSION_MODES:
            raise ValidationFailed("invalid_fusion_mode", f"未知融合公式：{fusion}")
        if reranker is None:
            reranker = create_reranker()

        debug: dict[str, Any] = {
            "mode": mode,
            "fusion": fusion if mode == "hybrid" else None,
            "reranker": reranker.name,
            "backend": None,
            "embedding": None,
            "warnings": [],
            "lexical_hits": [],
            "vector_hits": [],
            "fused": [],
            "rerank": None,
        }

        # 1) 词法召回（v1 逻辑原样：owner 谓词先行 → FTS5/LIKE → 词法打分）。
        lexical_items = self._lexical_recall(
            owner_id=owner_id, norm=norm, terms=terms, document_ids=document_ids
        )
        debug["lexical_hits"] = [h["chunk_id"] for h in lexical_items]

        # 2) 向量召回 / 融合（任何环节不可用都降级，warnings 如实记录）。
        ranked: list[dict[str, Any]] = lexical_items
        if mode in ("vector", "hybrid"):
            store, embedding, warnings = self._resolve_vector_stack()
            debug["warnings"].extend(warnings)
            if store is not None:
                debug["backend"] = store.name
            if embedding is not None:
                debug["embedding"] = embedding.name
            vector_items: list[dict[str, Any]] = []
            if store is not None and embedding is not None:
                vector_items, vec_warnings = self._vector_recall(
                    owner_id=owner_id,
                    query=norm,
                    store=store,
                    embedding=embedding,
                    top_k=top_k,
                    document_ids=document_ids,
                )
                debug["warnings"].extend(vec_warnings)
            debug["vector_hits"] = [h["chunk_id"] for h in vector_items]
            if mode == "vector":
                ranked = vector_items
            else:
                ranked = _fuse(
                    lexical_items,
                    vector_items,
                    fusion=fusion,
                    rrf_k=rrf_k if rrf_k is not None else None,
                    weights=weights,
                )
                debug["fused"] = [
                    {"chunk_id": h["chunk_id"], "score": h["score"]} for h in ranked
                ]

        # 3) Rerank（可选，保成员重排：只换顺序，不增删条目）。
        pool_size = max(top_k, DEFAULT_RERANK_POOL)
        pool, tail = ranked[:pool_size], ranked[pool_size:]
        if reranker is not None and reranker.name != "noop" and pool:
            before = [h["chunk_id"] for h in pool]
            pool = reranker.rerank(norm, pool)
            after = [h["chunk_id"] for h in pool]
            debug["rerank"] = {"name": reranker.name, "before": before, "after": after,
                               "changed": before != after}
        ranked = pool + tail

        results = ranked[:top_k]
        if include_debug:
            return {
                "query": norm,
                "mode": mode,
                "fusion": debug["fusion"],
                "count": len(results),
                "results": results,
                "debug": debug,
            }
        return results

    # -- internals ---------------------------------------------------------- #
    def _lexical_recall(
        self, *, owner_id: str, norm: str, terms: list[str],
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """v1 词法召回，原样保留（v2 改判已达部分，不推倒）。

        FTS5 first；on miss（或不可用）回退 per-term LIKE；词法打分 +
        ``MIN_QUERY_COVERAGE`` 护栏；score 降序 → 长块优先 → id 升序。
        """
        # 1) candidate set — owner predicate applied BEFORE any ranking.
        candidates = self._candidates(owner_id=owner_id, norm=norm, terms=terms)
        if not candidates:
            return []

        stmt = (
            select(KBChunk, KBDocument.name, KBDocument.source)
            .join(KBDocument, KBDocument.id == KBChunk.doc_id)
            .where(
                KBChunk.owner_id == owner_id,
                KBChunk.id.in_(candidates),
                KBDocument.status == "ready",
            )
        )
        if document_ids:
            stmt = stmt.where(KBChunk.doc_id.in_(document_ids))
        rows = self.s.execute(stmt.order_by(KBChunk.doc_id, KBChunk.seq)).all()

        # 2) lexical ranking (deterministic, explainable).
        scored: list[tuple[float, dict[str, Any]]] = []
        term_set = list(dict.fromkeys(terms))
        for chunk, doc_name, doc_source in rows:
            score, hits = score_chunk(
                chunk.content, term_set, min_coverage=MIN_QUERY_COVERAGE
            )
            if score <= 0:
                continue
            scored.append((score, {
                "chunk_id": chunk.id,
                "doc_id": chunk.doc_id,
                "doc_name": doc_name,
                "source": doc_source,
                "seq": chunk.seq,
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "score": round(score, 4),
                "matched_terms": hits,
            }))
        # 3) sort: score desc, then longer chunks first (more context), id asc.
        scored.sort(key=lambda x: (-x[0], -len(x[1]["content"]), x[1]["chunk_id"]))
        return [item for _score, item in scored]

    def _resolve_vector_stack(
        self,
    ) -> tuple[VectorStore | None, EmbeddingProvider | None, list[str]]:
        """组装（embedding, store）；任一环节失败 → (None/部分, warnings) 降级词法。"""
        warnings: list[str] = []
        try:
            embedding = resolve_default_embedding()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"embedding_setup_error:{type(exc).__name__}")
            return None, None, warnings
        dim = embedding.ensure_dim()
        if dim <= 0:
            warnings.append("embedding_dim_unresolved")
            return None, embedding, warnings
        try:
            store = create_vector_store(session=self.s, dim=dim)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"vector_backend_setup_error:{type(exc).__name__}")
            return None, embedding, warnings
        if not store.is_available():
            warnings.append("vector_backend_unavailable")
        return store, embedding, warnings

    def _vector_recall(
        self,
        *,
        owner_id: str,
        query: str,
        store: VectorStore,
        embedding: EmbeddingProvider,
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """向量语义召回路。字段与词法 item 同构（``score``=相似度，可对比可融合）。"""
        if not store.is_available():
            return [], ["vector_backend_unavailable"]
        try:
            ensure_vector_index(self.s, owner_id, store=store, embedding=embedding)
        except Exception as exc:  # noqa: BLE001
            return [], [f"vector_index_error:{type(exc).__name__}:{exc}"]
        try:
            qvec = embedding.embed_query(query)
        except Exception as exc:  # noqa: BLE001
            return [], [f"embedding_query_error:{type(exc).__name__}"]
        if len(qvec) != store.dim:
            return [], [f"vector_dim_mismatch:{len(qvec)}!={store.dim}"]
        floor = _env_float(VECTOR_FLOOR_ENV, DEFAULT_VECTOR_FLOOR)
        fetch_k = max(top_k * 2, DEFAULT_VECTOR_POOL)
        filters = {"doc_ids": list(document_ids)} if document_ids else None
        try:
            hits = store.query(qvec, owner_id=owner_id, top_k=fetch_k, filters=filters)
        except VectorFilterUnsupported as exc:
            return [], [f"vector_filter_error:{exc}"]
        except Exception as exc:  # noqa: BLE001
            return [], [f"vector_query_error:{type(exc).__name__}"]
        # 相似度下限：低于它的近邻是噪声而非证据——「宁可少召回，也不假装有结果」。
        hits = [h for h in hits if h.score >= floor]
        if not hits:
            return [], []
        # 授权谓词先于排序的纵深防御：向量命中的 chunk 再按 owner+ready 复核一遍，
        # 并补全 doc_name/source 等展示字段（vec 表里只有 id 与 doc_id）。
        rows = self.s.execute(
            select(KBChunk, KBDocument.name, KBDocument.source)
            .join(KBDocument, KBDocument.id == KBChunk.doc_id)
            .where(
                KBChunk.owner_id == owner_id,
                KBChunk.id.in_([h.chunk_id for h in hits]),
                KBDocument.status == "ready",
            )
        ).all()
        by_id = {chunk.id: (chunk, name, source) for chunk, name, source in rows}
        items: list[dict[str, Any]] = []
        for hit in hits:  # vec0 已按 distance 升序返回（越近越前）
            entry = by_id.get(hit.chunk_id)
            if entry is None:
                continue
            chunk, doc_name, doc_source = entry
            if document_ids and chunk.doc_id not in document_ids:
                continue
            items.append({
                "chunk_id": chunk.id,
                "doc_id": chunk.doc_id,
                "doc_name": doc_name,
                "source": doc_source,
                "seq": chunk.seq,
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "score": round(hit.score, 4),
                "vector_score": round(hit.score, 4),
                "matched_terms": [],
            })
        return items, []
    def _candidates(self, *, owner_id: str, norm: str, terms: list[str]) -> set[str]:
        """FTS5 first; on miss (or unavailability) fall back to per-term LIKE.

        The LIKE pass also rescues multi-word queries: an FTS *phrase* query like
        ``"采集 玩法"`` cannot match, while the per-term OR still can.
        """
        fts = _fts_candidates(self.s, owner_id, norm)
        if fts:
            return fts
        return _like_candidates(self.s, owner_id, terms)


def score_chunk(
    content: str, terms: list[str], *, min_coverage: float = 0.0
) -> tuple[float, list[str]]:
    """Lexical score for one chunk.

    * term frequency (log-scaled, so one huge chunk cannot dominate)
    * +0.5 per distinct matched term (coverage)
    * * 0.9 position decay (earlier matches are usually the definition)
    * divided by a mild length normalisation (sqrt of char count)

    ``min_coverage`` drops chunks that only match a small fraction of the query
    terms — 中文单字分词的必要护栏，否则单个常见字就会召回无关切片。
    """
    lowered = content.lower()
    if not lowered or not terms:
        return 0.0, []
    total = 0.0
    matched: list[str] = []
    for term in terms:
        count = lowered.count(term)
        if not count:
            continue
        matched.append(term)
        freq = 1.0 + math.log(count)
        pos = lowered.find(term)
        decay = 0.9 if pos <= 200 else 1.0
        total += freq * decay
    if not matched:
        return 0.0, []
    if len(matched) / len(terms) < min_coverage:
        return 0.0, []
    coverage = 0.5 * len(matched)
    norm_len = math.sqrt(max(len(content), 1) / 400.0)
    return (total + coverage) / norm_len, matched


# --------------------------------------------------------------------------- #
# 混合检索 v2（补齐包2 · A-向量库-07/08）
#
# 混合检索公开 API（包6 消费清单）：
#   * KnowledgeSearchService.search(actor, *, owner_id, query, top_k,
#       document_ids, mode, fusion, rrf_k, weights, reranker, include_debug)
#       —— 旧调用形状不变（list[dict]，字段同 v1）；新 kwargs 全部可选。
#   * include_debug=True → {"query","mode","fusion","count","results","debug"}，
#       debug={"lexical_hits","vector_hits","fused","rerank","warnings",
#              "backend","embedding"}。
#   * rrf_fuse / weighted_fuse —— 纯函数，输入两路 item 列表，输出融合列表。
#   * Reranker / NoopReranker / LexicalReranker + register_reranker /
#       get_reranker / list_rerankers / create_reranker —— 重排可插拔。
#   * ensure_vector_index(session, owner_id) —— 向量影子索引惰性对账回填。
# 环境变量（运行期直读，不碰 config.py——包1 独占）：
#   FIND_YOURSELF_KB_RETRIEVAL_MODE / _FUSION / _FUSION_WEIGHTS / _RRF_K /
#   _VECTOR_FLOOR / _RERANKER；embeddings/vectorstores 各自的注册表环境变量
#   见其模块 docstring。
# --------------------------------------------------------------------------- #

RETRIEVAL_MODES = ("lexical", "vector", "hybrid")
FUSION_MODES = ("rrf", "weighted")
#: 默认检索模式：hybrid（双路召回 → 融合；向量路不可用时自动降级词法路）
DEFAULT_RETRIEVAL_MODE = "hybrid"
#: 默认融合公式：RRF
DEFAULT_FUSION_MODE = "rrf"
#: RRF 常数（论文经典取值 60）：rank 越靠前贡献越大，但不过度压制另一路
DEFAULT_RRF_K = 60
#: 词法/向量两路默认等权
DEFAULT_FUSION_WEIGHTS = (1.0, 1.0)
#: 向量相似度下限：低于它的近邻按噪声丢弃（保证「无命中→空列表」语义不破）
DEFAULT_VECTOR_FLOOR = 0.2
#: 向量路候选池大小（融合前）
DEFAULT_VECTOR_POOL = 24
#: Rerank 参与重排的候选池大小（池内重排、池外原序保留）
DEFAULT_RERANK_POOL = 20

RETRIEVAL_MODE_ENV = "FIND_YOURSELF_KB_RETRIEVAL_MODE"
FUSION_MODE_ENV = "FIND_YOURSELF_KB_FUSION"
FUSION_WEIGHTS_ENV = "FIND_YOURSELF_KB_FUSION_WEIGHTS"
RRF_K_ENV = "FIND_YOURSELF_KB_RRF_K"
VECTOR_FLOOR_ENV = "FIND_YOURSELF_KB_VECTOR_FLOOR"
RERANKER_ENV = "FIND_YOURSELF_KB_RERANKER"


def _env_choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    raw = (os.getenv(name) or "").strip().lower()
    return raw if raw in allowed else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except (TypeError, ValueError):
        return default


def _env_weights(name: str, default: tuple[float, float]) -> tuple[float, float]:
    """解析 "词法权,向量权"（如 "1.0,0.7"）；非法/缺失回默认，绝不抛错。"""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parts = [float(p) for p in raw.split(",")]
    except ValueError:
        return default
    if len(parts) == 2 and all(p >= 0 for p in parts):
        return (parts[0], parts[1])
    return default


# --------------------------------------------------------------------------- #
# 融合（A-向量库-08：RRF / 加权，公式可配置）
# --------------------------------------------------------------------------- #

def _merge_fused(base: dict[str, Any], incoming: dict[str, Any], path_key: str) -> None:
    """把一路 item 并进合并行：展示字段先到先得（词法路先并入），该路分数单列。"""
    for key, value in incoming.items():
        if key == "score":
            continue
        base.setdefault(key, value)
    base[path_key] = round(float(incoming.get("score", 0.0)), 4)


def _finalize_fused(
    merged: dict[str, dict[str, Any]], scores: dict[str, float]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cid, item in merged.items():
        item["score"] = round(scores[cid], 6)
        item.setdefault("lexical_score", None)
        item.setdefault("vector_score", None)
        out.append(item)
    out.sort(key=lambda x: (-x["score"], -len(x.get("content", "")), x["chunk_id"]))
    return out


def rrf_fuse(
    lexical_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: tuple[float, float] = DEFAULT_FUSION_WEIGHTS,
) -> list[dict[str, Any]]:
    """RRF（Reciprocal Rank Fusion）：``fused(d) = Σ_path w_path / (k + rank_path(d))``。

    * rank 从 1 起，按该路自身分数降序；某 chunk 缺席某路时不计项（不罚分）；
    * ``k`` 越大两路越平滑，越小头部越 dominate；
    * ``weights = (词法权, 向量权)`` 允许偏置某一路；
    * 返回按融合分降序的合并 item（``score``=融合分；原始分数在
      ``lexical_score`` / ``vector_score`` 字段，调试与 A/B 对比用）。
    """
    k = max(int(k), 1)
    merged: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    for items, weight, path_key in (
        (lexical_hits, float(weights[0]), "lexical_score"),
        (vector_hits, float(weights[1]), "vector_score"),
    ):
        ranked = [i for i in items if float(i.get("score", 0.0)) > 0.0]
        ranked.sort(key=lambda x: (-float(x.get("score", 0.0)), str(x.get("chunk_id"))))
        for rank, item in enumerate(ranked, start=1):
            cid = str(item["chunk_id"])
            base = merged.setdefault(cid, dict(item))
            _merge_fused(base, item, path_key)
            scores[cid] = scores.get(cid, 0.0) + weight / (k + rank)
    return _finalize_fused(merged, scores)


def weighted_fuse(
    lexical_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    *,
    weights: tuple[float, float] = DEFAULT_FUSION_WEIGHTS,
) -> list[dict[str, Any]]:
    """加权融合：两路分数各自 min-max 归一到 [0,1] 后 ``w_lex*n_lex + w_vec*n_vec``。

    * 单路分数全部相同（极差≈0）时该路并列取 1.0；
    * 某 chunk 缺席某路记 0 贡献（不额外罚分）；
    * 适合两路分数可比（如都要语义化打分）的场景；RRF 更稳健于量纲差异。
    """

    def normalize(items: list[dict[str, Any]]) -> dict[str, float]:
        pairs = [
            (str(i["chunk_id"]), float(i.get("score", 0.0)))
            for i in items
            if float(i.get("score", 0.0)) > 0.0
        ]
        if not pairs:
            return {}
        lo = min(v for _c, v in pairs)
        hi = max(v for _c, v in pairs)
        if hi - lo < 1e-9:
            return {cid: 1.0 for cid, _v in pairs}
        return {cid: (v - lo) / (hi - lo) for cid, v in pairs}

    lex_norm = normalize(lexical_hits)
    vec_norm = normalize(vector_hits)
    merged: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    for items, weight, path_key, norm in (
        (lexical_hits, float(weights[0]), "lexical_score", lex_norm),
        (vector_hits, float(weights[1]), "vector_score", vec_norm),
    ):
        for item in items:
            cid = str(item["chunk_id"])
            if cid not in norm:
                continue
            base = merged.setdefault(cid, dict(item))
            _merge_fused(base, item, path_key)
            scores[cid] = scores.get(cid, 0.0) + weight * norm[cid]
    return _finalize_fused(merged, scores)


def _fuse(
    lexical_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    *,
    fusion: str,
    rrf_k: int | None = None,
    weights: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    """按配置分发融合公式（``search(mode="hybrid")`` 内部用）。"""
    w = weights if weights is not None else _env_weights(
        FUSION_WEIGHTS_ENV, DEFAULT_FUSION_WEIGHTS
    )
    if fusion == "weighted":
        return weighted_fuse(lexical_hits, vector_hits, weights=w)
    k = rrf_k if rrf_k is not None else _env_int(RRF_K_ENV, DEFAULT_RRF_K)
    return rrf_fuse(lexical_hits, vector_hits, k=k, weights=w)


# --------------------------------------------------------------------------- #
# Rerank（A-向量库-08：模型重排可插拔接口，默认 noop / 词法实现）
# --------------------------------------------------------------------------- #

class Reranker:
    """重排接口：输入 query 与融合后候选（保完整 item 字段），输出同构列表。

    契约：**保成员重排**——只调整顺序/补充诊断字段，不增删条目、不改
    ``chunk_id``；模型类实现（cross-encoder / LTR 等）应把模型分写进
    ``rerank_score`` 字段供 A/B 对比。
    """

    name: str = "base"

    def rerank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError


class NoopReranker(Reranker):
    """默认实现：不重排，保留融合序（重排前=重排后）。"""

    name = "noop"

    def rerank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return hits


class LexicalReranker(Reranker):
    """词法重排：用 ``score_chunk`` 对候选重打分（v1 词法重排的接口化封装）。

    以 ``min_coverage=0.0`` 重打（进池的候选已经过护栏，这里只重排不筛除）；
    词法分写入 ``rerank_score``，按其降序稳定排序（同 v1：长块优先、id 升序）。
    """

    name = "lexical"

    def rerank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        terms = _query_terms(query)
        rescored: list[tuple[float, dict[str, Any]]] = []
        for item in hits:
            score, matched = score_chunk(item.get("content", ""), terms, min_coverage=0.0)
            out = dict(item)
            out["rerank_score"] = round(score, 4)
            if matched:
                out["matched_terms"] = matched
            rescored.append((score, out))
        rescored.sort(
            key=lambda x: (-x[0], -len(x[1].get("content", "")), x[1]["chunk_id"])
        )
        return [item for _score, item in rescored]


_RERANKER_REGISTRY: dict[str, type[Reranker]] = {}


def register_reranker(name: str, reranker_cls: type[Reranker], *, override: bool = False) -> None:
    """注册重排实现（模型 rerank 在此接入：一个类 + 一次注册）。"""
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("reranker name must be non-empty")
    if key in _RERANKER_REGISTRY and not override:
        raise ValueError(f"reranker '{key}' already registered; pass override=True")
    _RERANKER_REGISTRY[key] = reranker_cls


def get_reranker(name: str) -> Reranker:
    key = (name or "").strip().lower()
    cls = _RERANKER_REGISTRY.get(key)
    if cls is None:
        raise KeyError(f"unknown reranker '{name}'; known: {sorted(_RERANKER_REGISTRY)}")
    return cls()


def list_rerankers() -> list[str]:
    return sorted(_RERANKER_REGISTRY)


def create_reranker(name: str | None = None) -> Reranker:
    """按名构建；缺省读 ``FIND_YOURSELF_KB_RERANKER``，再缺省 ``noop``。"""
    chosen = (name or os.getenv(RERANKER_ENV) or "noop").strip().lower()
    return get_reranker(chosen)


register_reranker("noop", NoopReranker, override=True)
register_reranker("lexical", LexicalReranker, override=True)


# --------------------------------------------------------------------------- #
# 向量影子索引（惰性对账回填）
# --------------------------------------------------------------------------- #

def ensure_vector_index(
    session: Session,
    owner_id: str,
    *,
    store: VectorStore | None = None,
    embedding: EmbeddingProvider | None = None,
) -> dict[str, Any]:
    """把 owner 的 ready 切片增量灌入向量影子索引（幂等，缺多少补多少）。

    ``ingest.py`` 归属其他包，本模块挂不进入库回调，故采用**检索期惰性对账**：
    以 ``store.list_chunk_ids`` 对 ``kb_chunks``（owner+ready）做差集——缺的
    embed 后 upsert，文档已删除/重导后残留的 stale 行清除。向量索引是可再生的
    派生数据（同 FTS5 影子表），重建零风险。

    返回 ``{"indexed","purged","total","backend","embedding"[,"skipped"]}``；
    后端/嵌入不可用时返回带 ``skipped`` 原因的空操作结果，**不抛异常**。
    """
    if not owner_id:
        raise ValidationFailed("owner_required", "vector index requires an owner_id")
    if embedding is None:
        embedding = resolve_default_embedding()
    if store is None:
        dim = embedding.ensure_dim()
        if dim <= 0:
            return {"indexed": 0, "purged": 0, "total": 0,
                    "backend": None, "embedding": embedding.name,
                    "skipped": "embedding_dim_unresolved"}
        store = create_vector_store(session=session, dim=dim)
    if not store.is_available():
        return {"indexed": 0, "purged": 0, "total": 0,
                "backend": store.name, "embedding": embedding.name,
                "skipped": "vector_backend_unavailable"}
    rows = session.execute(
        select(KBChunk.id, KBChunk.content, KBChunk.doc_id, KBChunk.seq, KBChunk.content_hash)
        .join(KBDocument, KBDocument.id == KBChunk.doc_id)
        .where(KBChunk.owner_id == owner_id, KBDocument.status == "ready")
    ).all()
    existing_ids = store.list_chunk_ids(owner_id=owner_id)
    wanted_ids = {r.id for r in rows}
    stale_ids = existing_ids - wanted_ids
    if stale_ids:
        store.delete(sorted(stale_ids), owner_id=owner_id)
    missing = [r for r in rows if r.id not in existing_ids]
    if missing:
        vectors = embedding.embed_documents([r.content for r in missing])
        records = [
            VectorRecord(
                chunk_id=r.id,
                doc_id=r.doc_id,
                embedding=vec,
                metadata={"seq": r.seq, "content_hash": r.content_hash},
            )
            for r, vec in zip(missing, vectors)
        ]
        store.upsert(records, owner_id=owner_id)
    return {
        "indexed": len(missing),
        "purged": len(stale_ids),
        "total": len(rows),
        "backend": store.name,
        "embedding": embedding.name,
    }