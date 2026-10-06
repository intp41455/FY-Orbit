"""包6 · A-立体3D知识图谱-04 · 数据层：实体/关系抽取 + 图存储 + 读端点支撑。

架构铁律（任务书原文）：**图谱只存实体/关系/指针，原文不搬运**——

* 节点表 ``kb_graph_nodes`` 存名称/类型/权重 + **首次出现切片指针**
  （``first_chunk_id`` / ``first_doc_id``）；
* 边表 ``kb_graph_edges`` 存关系类型 + 权重 + **证据切片指针**
  （``evidence_chunk_id`` / ``evidence_doc_id``）；
* 前端要证据文本时，读端点按指针**现取** ``kb_chunks.content``
  （:meth:`GraphService.read_graph` 的 ``with_evidence_text``），图存储里没有
  任何一段原文副本。

抽取器可插拔：

* :class:`RuleBasedExtractor` —— 规则 + 词法，**纯离线确定性**（不依赖任何模型）：
  书名号/引号/标题/拉丁词实体 + 「A是B / A又称B / A包括B / A属于B / A依赖B /
  A导致B」关系句式 + 同切片共现兜底；
* :class:`LLMExtractor` —— LLM 抽取走**现有 provider 面**：构造时注入任意具备
  ``invoke(InvokeCall) -> InvokeResult`` 形状的对象（``services/hub/adapters``
  的统一适配器契约），提示词要求输出 JSON；解析失败如实记录
  ``last_error`` 并返回空结果，**绝不编造实体**；
* 注册表 :func:`register_extractor` / :func:`get_extractor` 供后续扩展。

3D 渲染归前端（包E canvas 星图），本模块只供数据与响应形状
（见 :meth:`GraphService.read_graph` 返回结构，交付报告同步登记）。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...db.base import Base
from ...db.kb_models import KBChunk, KBDocument
from ...db.types import ID, TZDateTime, utcnow
from ..actor import Actor
from ..errors import ValidationFailed

__all__ = [
    "KBGraphNode",
    "KBGraphEdge",
    "ExtractedEntity",
    "ExtractedRelation",
    "EntityExtractor",
    "RuleBasedExtractor",
    "LLMExtractor",
    "register_extractor",
    "get_extractor",
    "list_extractors",
    "GraphService",
    "MAX_NODES_PER_BUILD",
    "MAX_EDGES_PER_BUILD",
]

#: 单次构建的节点/边护栏（免费额度与响应体量的双重护栏）
MAX_NODES_PER_BUILD = 2000
MAX_EDGES_PER_BUILD = 6000

NODE_KINDS = ("concept", "work", "term", "person", "org", "place")
RELATION_TYPES = (
    "is_a", "alias", "has_part", "belongs_to", "depends_on", "causes", "co_occurs",
)

_NAME_STOPWORDS = frozenset(
    {
        "我们", "你们", "他们", "它们", "自己", "什么", "这个", "那个", "如何",
        "可以", "需要", "因此", "所以", "但是", "然后", "其中", "其他", "以上",
        "下面", "本章", "本文", "如下", "如图", "例如", "注意", "小结", "摘要",
    }
)


# --------------------------------------------------------------------------- #
# ORM：节点 / 边（迁移 0039 建表；这里在共享 Base 上注册同构模型）
# --------------------------------------------------------------------------- #


class KBGraphNode(Base):
    """图谱节点：实体名 + 类型 + 权重 + 首现指针。**不存任何原文。**"""

    __tablename__ = "kb_graph_nodes"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    # 归一键（lower/去空白）：同 owner 内幂等 upsert 的键
    name_key: Mapped[str] = mapped_column(String(320), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="concept")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_chunk_id: Mapped[str] = mapped_column(ID, nullable=False, default="")
    first_doc_id: Mapped[str] = mapped_column(ID, nullable=False, default="")
    created_at: Mapped[Any] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[Any] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "name_key", name="uq_kb_graph_nodes_owner_key"),
        Index("ix_kb_graph_nodes_owner_weight", "owner_id", "weight"),
    )


class KBGraphEdge(Base):
    """图谱边：关系 + 权重 + 证据切片指针。**不存任何原文。**"""

    __tablename__ = "kb_graph_edges"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    src_node_id: Mapped[str] = mapped_column(ID, nullable=False)
    dst_node_id: Mapped[str] = mapped_column(ID, nullable=False)
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    evidence_chunk_id: Mapped[str] = mapped_column(ID, nullable=False, default="")
    evidence_doc_id: Mapped[str] = mapped_column(ID, nullable=False, default="")
    created_at: Mapped[Any] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "src_node_id", "dst_node_id", "relation",
            name="uq_kb_graph_edges_owner_triple",
        ),
        Index("ix_kb_graph_edges_owner_src", "owner_id", "src_node_id"),
    )


# --------------------------------------------------------------------------- #
# 抽取器协议与实现
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExtractedEntity:
    name: str
    kind: str = "concept"


@dataclass(frozen=True)
class ExtractedRelation:
    src: str
    dst: str
    relation: str


class EntityExtractor:
    """抽取器接口：输入一段切片文本，输出实体与关系（确定性契约）。"""

    name: str = "base"

    def extract(
        self, text: str, *, heading: str = ""
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        raise NotImplementedError


_CJK_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,12}")
_LATIN_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,30}")
_BOOK_RE = re.compile(r"《([^《》]{1,60})》")
_QUOTE_RE = re.compile(r"[「『]([^」』]{1,40})[」』]|“([^”\n]{1,40})”")
_SENT_SPLIT_RE = re.compile(r"[。！？!?\n；;]+")
_RELATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:又称|又叫|也叫|亦称|别名是?)\s*([\u4e00-\u9fffA-Za-z0-9《》]{2,20})$"), "alias"),
    (re.compile(r"^([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:是|即|为|指的是?)\s*(?:一种|一个|一类)?([\u4e00-\u9fffA-Za-z0-9《》]{2,20})$"), "is_a"),
    (re.compile(r"^([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:包括|包含|分为| subdivided into)\s*([\u4e00-\u9fffA-Za-z0-9《》]{2,20})$"), "has_part"),
    (re.compile(r"^([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:属于)\s*([\u4e00-\u9fffA-Za-z0-9《》]{2,20})$"), "belongs_to"),
    (re.compile(r"^([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:依赖|基于)\s*([\u4e00-\u9fffA-Za-z0-9《》]{2,20})$"), "depends_on"),
    (re.compile(r"^([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:导致|引起|造成)\s*([\u4e00-\u9fffA-Za-z0-9《》]{2,20})$"), "causes"),
)


class RuleBasedExtractor(EntityExtractor):
    """规则 + 词法抽取：离线、确定性、可解释（默认抽取器）。

    实体来源（带类型标注）：《书名》→ work；「引号词」→ term；markdown 标题 →
    concept；拉丁词 → concept；出现在关系句式主/宾语的中文短语 → concept。
    关系来源：六个句式正则 + 同切片多实体共现兜底（``co_occurs``，低权重）。
    """

    name = "rules"

    def __init__(self, *, max_entities_per_chunk: int = 12, co_occurs: bool = True):
        self.max_entities_per_chunk = max_entities_per_chunk
        self.co_occurs = co_occurs

    def extract(
        self, text: str, *, heading: str = ""
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        entities: list[ExtractedEntity] = []
        relations: list[ExtractedRelation] = []

        def add_entity(name: str, kind: str) -> str | None:
            clean = _clean_name(name)
            if not clean or clean in _NAME_STOPWORDS or len(clean) < 2:
                return None
            if len(entities) >= self.max_entities_per_chunk:
                return None
            if any(e.name == clean for e in entities):
                return clean
            entities.append(ExtractedEntity(name=clean, kind=kind))
            return clean

        for m in _BOOK_RE.finditer(text):
            add_entity(m.group(1), "work")
        for m in _QUOTE_RE.finditer(text):
            add_entity(m.group(1) or m.group(2) or "", "term")
        for m in _LATIN_TERM_RE.finditer(text):
            add_entity(m.group(0), "concept")
        if heading:
            for part in heading.split(" / "):
                add_entity(part, "concept")

        for sentence in _SENT_SPLIT_RE.split(text):
            sent = sentence.strip()
            if not sent:
                continue
            matched = False
            for pattern, relation in _RELATION_PATTERNS:
                m = pattern.match(sent)
                if not m:
                    continue
                src = _clean_name(m.group(1))
                dst = _clean_name(m.group(2))
                if not src or not dst or src in _NAME_STOPWORDS or dst in _NAME_STOPWORDS:
                    continue
                add_entity(src, "concept")
                add_entity(dst, "concept")
                relations.append(ExtractedRelation(src=src, dst=dst, relation=relation))
                matched = True
                break
            if not matched and self.co_occurs:
                # 兜底：同句内已有的中文实体共现（最多两条，防组合爆炸）。
                present = [
                    e.name for e in entities
                    if e.name in sent and not _LATIN_TERM_RE.fullmatch(e.name)
                ][:2]
                for i in range(len(present) - 1):
                    relations.append(
                        ExtractedRelation(src=present[i], dst=present[i + 1], relation="co_occurs")
                    )
        return entities, relations


class LLMExtractor(EntityExtractor):
    """LLM 抽取：走**现有 provider 面**（hub 统一适配器 ``invoke`` 契约）。

    ``invoker`` 任意具备 ``invoke(InvokeCall) -> InvokeResult`` 形状的对象；
    提示词强制输出 JSON（``entities``/``relations``）。解析失败把原因写进
    ``last_error`` 并返回空结果——绝不用编造的实体冒充抽取成功。
    """

    name = "llm"

    PROMPT = (
        "从下面的资料切片中抽取实体与关系，只输出 JSON，不要输出任何其他文字：\n"
        '{{"entities": [{{"name": "...", "kind": "concept|work|term|person|org|place"}}],'
        ' "relations": [{{"src": "...", "dst": "...", "relation": '
        '"is_a|alias|has_part|belongs_to|depends_on|causes"}}]}}\n'
        "资料：\n{chunk}"
    )

    def __init__(self, invoker: Any, *, timeout_seconds: float = 20.0):
        if invoker is None or not hasattr(invoker, "invoke"):
            raise ValidationFailed(
                "llm_extractor_requires_invoker",
                "LLM 抽取需要一个具备 invoke(InvokeCall)->InvokeResult 形状的 provider"
                "（services/hub/adapters 统一适配器契约）",
            )
        self.invoker = invoker
        self.timeout_seconds = timeout_seconds
        self.last_error: str = ""

    def extract(
        self, text: str, *, heading: str = ""
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        from ..hub.adapters import InvokeCall  # 本地导入：保持模块轻依赖

        self.last_error = ""
        prompt = self.PROMPT.format(chunk=(text or "")[:4000])
        try:
            result = self.invoker.invoke(
                InvokeCall(action="chat", params={"prompt": prompt},
                           timeout_seconds=self.timeout_seconds)
            )
        except Exception as exc:  # noqa: BLE001 — provider 异常如实记录
            self.last_error = f"invoke_error:{type(exc).__name__}"
            return [], []
        if not getattr(result, "ok", False):
            self.last_error = f"invoke_failed:{getattr(result, 'error', '') or 'unknown'}"
            return [], []
        parsed = _parse_llm_json(getattr(result, "output", None))
        if parsed is None:
            self.last_error = "unparseable_output"
            return [], []
        entities = [
            ExtractedEntity(name=_clean_name(e.get("name", "")), kind=e.get("kind", "concept"))
            for e in parsed.get("entities", [])
            if isinstance(e, dict) and _clean_name(str(e.get("name", "")))
        ]
        relations = [
            ExtractedRelation(
                src=_clean_name(str(r.get("src", ""))),
                dst=_clean_name(str(r.get("dst", ""))),
                relation=str(r.get("relation", "co_occurs")),
            )
            for r in parsed.get("relations", [])
            if isinstance(r, dict)
            and _clean_name(str(r.get("src", "")))
            and _clean_name(str(r.get("dst", "")))
        ]
        return entities, relations


_EXTRACTOR_REGISTRY: dict[str, type] = {}


def register_extractor(name: str, cls: type, *, override: bool = False) -> None:
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("extractor name must be non-empty")
    if key in _EXTRACTOR_REGISTRY and not override:
        raise ValueError(f"extractor '{key}' already registered; pass override=True")
    _EXTRACTOR_REGISTRY[key] = cls


def get_extractor(name: str | None, *, invoker: Any = None) -> EntityExtractor:
    key = (name or "rules").strip().lower()
    cls = _EXTRACTOR_REGISTRY.get(key)
    if cls is None:
        raise ValidationFailed(
            "unknown_extractor",
            f"未知抽取器 {name!r}；已注册：{sorted(_EXTRACTOR_REGISTRY)}",
        )
    if key == "llm":
        return cls(invoker)  # type: ignore[misc]
    return cls()  # type: ignore[call-arg]


def list_extractors() -> list[str]:
    return sorted(_EXTRACTOR_REGISTRY)


register_extractor("rules", RuleBasedExtractor, override=True)
register_extractor("llm", LLMExtractor, override=True)


# --------------------------------------------------------------------------- #
# 服务：构建 / 读取
# --------------------------------------------------------------------------- #


class GraphService:
    """知识图谱数据层门面：owner 隔离的构建（upsert 幂等）与读取。"""

    def __init__(self, session: Session):
        self.s = session

    def build(
        self,
        actor: Actor,
        *,
        owner_id: str,
        doc_ids: list[str] | None = None,
        extractor_name: str | None = None,
        llm_invoker: Any | None = None,
        rebuild: bool = True,
    ) -> dict[str, Any]:
        """扫描 owner 的 ready 切片 → 抽取 → upsert 节点/边（指针不搬运原文）。"""
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        extractor = get_extractor(extractor_name, invoker=llm_invoker)

        if rebuild:
            deleted_nodes = self.s.execute(
                KBGraphNode.__table__.delete().where(KBGraphNode.owner_id == owner_id)
            ).rowcount
            deleted_edges = self.s.execute(
                KBGraphEdge.__table__.delete().where(KBGraphEdge.owner_id == owner_id)
            ).rowcount
        else:
            deleted_nodes = deleted_edges = 0

        stmt = (
            sa_select(KBChunk.id, KBChunk.doc_id, KBChunk.content)
            .join(KBDocument, KBDocument.id == KBChunk.doc_id)
            .where(KBChunk.owner_id == owner_id, KBDocument.status == "ready")
            .order_by(KBChunk.doc_id, KBChunk.seq)
        )
        if doc_ids:
            stmt = stmt.where(KBChunk.doc_id.in_(doc_ids))
        rows = self.s.execute(stmt).all()

        node_ids: dict[tuple[str, str], str] = self._existing_node_ids(owner_id)
        nodes_added = edges_added = skipped_chunks = 0
        truncated = False

        for chunk_id, doc_id, content in rows:
            if nodes_added >= MAX_NODES_PER_BUILD or edges_added >= MAX_EDGES_PER_BUILD:
                truncated = True
                break
            try:
                entities, relations = extractor.extract(content or "")
            except Exception as exc:  # noqa: BLE001 — 单片抽取失败不拖垮整图
                skipped_chunks += 1
                self.s.flush()
                last = getattr(extractor, "last_error", "")
                if last:
                    continue
                raise ValidationFailed(
                    "graph_extract_failed", f"切片 {chunk_id} 抽取失败：{type(exc).__name__}"
                ) from exc
            for ent in entities:
                key = _name_key(ent.name)
                if key in node_ids:
                    continue
                if nodes_added >= MAX_NODES_PER_BUILD:
                    truncated = True
                    break
                node = KBGraphNode(
                    id=uuid.uuid4().hex,
                    owner_id=owner_id,
                    name=ent.name[:300],
                    name_key=key[:320],
                    kind=ent.kind if ent.kind in NODE_KINDS else "concept",
                    weight=1,
                    first_chunk_id=chunk_id,
                    first_doc_id=doc_id,
                )
                self.s.add(node)
                node_ids[(owner_id, key)] = node.id
                nodes_added += 1
            for rel in relations:
                src_id = node_ids.get((owner_id, _name_key(rel.src)))
                dst_id = node_ids.get((owner_id, _name_key(rel.dst)))
                if not src_id or not dst_id or src_id == dst_id:
                    continue
                if edges_added >= MAX_EDGES_PER_BUILD:
                    truncated = True
                    break
                existing = self.s.execute(
                    sa_select(KBGraphEdge).where(
                        KBGraphEdge.owner_id == owner_id,
                        KBGraphEdge.src_node_id == src_id,
                        KBGraphEdge.dst_node_id == dst_id,
                        KBGraphEdge.relation == rel.relation,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing.weight = (existing.weight or 1) + 1
                    continue
                self.s.add(
                    KBGraphEdge(
                        id=uuid.uuid4().hex,
                        owner_id=owner_id,
                        src_node_id=src_id,
                        dst_node_id=dst_id,
                        relation=rel.relation if rel.relation in RELATION_TYPES else "co_occurs",
                        weight=1,
                        evidence_chunk_id=chunk_id,
                        evidence_doc_id=doc_id,
                    )
                )
                edges_added += 1
        self.s.flush()
        return {
            "nodes_added": nodes_added,
            "edges_added": edges_added,
            "nodes_purged": int(deleted_nodes or 0),
            "edges_purged": int(deleted_edges or 0),
            "chunks_scanned": len(rows),
            "chunks_skipped": skipped_chunks,
            "truncated": truncated,
            "extractor": extractor.name,
            "extractor_error": getattr(extractor, "last_error", ""),
        }

    def read_graph(
        self,
        actor: Actor,
        *,
        owner_id: str,
        kind: str | None = None,
        limit_nodes: int = 300,
        limit_edges: int = 600,
        with_evidence_text: bool = False,
    ) -> dict[str, Any]:
        """读图（3D 星图数据层响应形状，交付报告同步登记）。"""
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        limit_nodes = max(1, min(int(limit_nodes), 1000))
        limit_edges = max(1, min(int(limit_edges), 2000))

        node_stmt = (
            sa_select(KBGraphNode)
            .where(KBGraphNode.owner_id == owner_id)
            .order_by(KBGraphNode.weight.desc(), KBGraphNode.name)
            .limit(limit_nodes)
        )
        if kind:
            node_stmt = node_stmt.where(KBGraphNode.kind == kind)
        nodes = list(self.s.execute(node_stmt).scalars())
        by_id = {n.id: n for n in nodes}

        edge_stmt = (
            sa_select(KBGraphEdge)
            .where(KBGraphEdge.owner_id == owner_id)
            .order_by(KBGraphEdge.weight.desc(), KBGraphEdge.id)
            .limit(limit_edges * 4)
        )
        edges = [
            e for e in self.s.execute(edge_stmt).scalars()
            if e.src_node_id in by_id and e.dst_node_id in by_id
        ][:limit_edges]

        evidence_chunks: dict[str, dict[str, Any]] = {}
        chunk_ids = sorted({e.evidence_chunk_id for e in edges if e.evidence_chunk_id})
        if chunk_ids:
            rows = self.s.execute(
                sa_select(KBChunk.id, KBChunk.doc_id, KBChunk.seq, KBChunk.content)
                .where(KBChunk.id.in_(chunk_ids))
            ).all()
            evidence_chunks = {
                r[0]: {"chunk_id": r[0], "doc_id": r[1], "seq": r[2], "text": (r[3] or "")[:200]}
                for r in rows
            }
        doc_names: dict[str, str] = {}
        doc_ids = sorted({e.evidence_doc_id for e in edges if e.evidence_doc_id})
        if doc_ids:
            rows = self.s.execute(
                sa_select(KBDocument.id, KBDocument.name).where(KBDocument.id.in_(doc_ids))
            ).all()
            doc_names = {r[0]: r[1] for r in rows}

        total_nodes = self.s.execute(
            sa_select(KBGraphNode.id).where(KBGraphNode.owner_id == owner_id)
        ).all()
        total_edges = self.s.execute(
            sa_select(KBGraphEdge.id).where(KBGraphEdge.owner_id == owner_id)
        ).all()

        return {
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "kind": n.kind,
                    "weight": n.weight,
                    "first_chunk_id": n.first_chunk_id,
                    "first_doc_id": n.first_doc_id,
                }
                for n in nodes
            ],
            "edges": [
                {
                    "id": e.id,
                    "source": e.src_node_id,
                    "target": e.dst_node_id,
                    "relation": e.relation,
                    "weight": e.weight,
                    "evidence": self._evidence(e, evidence_chunks, doc_names, with_evidence_text),
                }
                for e in edges
            ],
            "counts": {
                "nodes": len(total_nodes),
                "edges": len(total_edges),
                "returned_nodes": len(nodes),
                "returned_edges": len(edges),
            },
            "extractor_hint": "POST /api/knowledge/graph/build 重建（rules 离线确定性；llm 可插拔）",
            "no_text_storage_note": "图谱只存实体/关系/指针；证据文本按指针现取，不入图存储",
        }

    # -- internals ------------------------------------------------------------ #
    def _evidence(
        self,
        edge: KBGraphEdge,
        evidence_chunks: dict[str, dict[str, Any]],
        doc_names: dict[str, str],
        with_evidence_text: bool,
    ) -> dict[str, Any]:
        chunk = evidence_chunks.get(edge.evidence_chunk_id, {})
        evidence: dict[str, Any] = {
            "chunk_id": edge.evidence_chunk_id,
            "doc_id": edge.evidence_doc_id,
            "doc_name": doc_names.get(edge.evidence_doc_id, ""),
            "seq": chunk.get("seq"),
        }
        if with_evidence_text:
            # 按指针现取（读时解引用），图存储里没有原文副本。
            evidence["text"] = chunk.get("text", "")
        return evidence

    def _existing_node_ids(self, owner_id: str) -> dict[tuple[str, str], str]:
        rows = self.s.execute(
            sa_select(KBGraphNode.id, KBGraphNode.name_key).where(
                KBGraphNode.owner_id == owner_id
            )
        ).all()
        return {(owner_id, key): nid for nid, key in rows}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _clean_name(raw: str) -> str:
    text = re.sub(r"\s+", "", str(raw or "")).strip("《》「」\"'。，,；;：:")
    text = text.strip("。 ")
    if text.startswith("一") and len(text) > 2 and text[1] in "种个回条套类":
        text = text[2:]
    return text[:60]


def _name_key(name: str) -> str:
    return re.sub(r"\s+", "", (name or "")).lower()


def _parse_llm_json(output: Any) -> dict[str, Any] | None:
    """从 LLM 输出中稳健提取 JSON 对象；失败返回 None（诚实：不猜）。"""
    if isinstance(output, dict):
        return output
    text = str(output or "")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
