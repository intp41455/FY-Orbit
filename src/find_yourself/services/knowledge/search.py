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

向量演进路径（任务书裁决）：v1 是「倒排 + 哈希嵌入」的轻量方案；切 PostgreSQL 后
在 ``kb_chunks`` 加 pgvector 列，用真实嵌入做向量近邻，再与 tsvector 结果按 RRF
归并。当前实现的 docstring/命名都按该路径预留，**不假装已有语义向量**。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from sqlalchemy import or_, select, text as sql_text
from sqlalchemy.orm import Session

from ...db.kb_models import KBDocument, KBChunk
from ..actor import Actor
from ..errors import ValidationFailed

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
    ) -> list[dict[str, Any]]:
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
        return [item for _score, item in scored[:top_k]]

    # -- internals ---------------------------------------------------------- #
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