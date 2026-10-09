"""包6 · A-云盘RAG-02/03 · 可定制 RAG 工程：方案模板（preset）+ 调试器 + A/B 对比。

模块定位：

* **方案模板**：把「切片 / 嵌入 / 检索 / 重排」各阶段参数收进一个可序列化的
  :class:`RagParams`，内置 4 种预设 + 用户自定义方案存 DB（alembic 0039 的
  ``kb_rag_presets`` 表）。检索本体**不自研**——全部走
  ``services/knowledge/search.py`` 的公开 API（``KnowledgeSearchService.search``
  的 ``mode/fusion/rrf_k/weights/reranker/include_debug`` 形参，即补齐包2 的
  混合检索公开面；本文件零侵入，只做参数编排）。
* **预留融合参数位**：``RagParams.vector_floor``（向量相似度下限覆盖）在包2 的
  ``search()`` 签名中尚为环境变量（``FIND_YOURSELF_KB_VECTOR_FLOOR``）而非形参，
  这里先作为模板字段存档——包2 公开形参就绪后即插即用，不需要改模板结构。
  ``chunk_size`` / ``chunk_overlap`` 是**入库期**参数：随方案存档，供云盘同步 /
  重导管线消费（当前入库管线固定 800/100，见交付报告诚实边界）。
* **调试器**：:meth:`RagPresetService.run_debug` 返回召回片段 / 分数 / 命中词 /
  双路召回与融合诊断 / 耗时；:meth:`RagPresetService.compare` 跑多方案 A/B 对比
  （共享切片重合度 + 各臂耗时 + 各臂 Top-K），可存档到 ``kb_rag_ab_runs``。
* **诚实性**：方案参数一律显式校验（未知键报错、非法值报错），绝不静默纠正；
  对比结果如实标注各臂检索模式与降级 warnings。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Index, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...db.base import Base
from ...db.types import ID, TZDateTime, utcnow
from ..actor import Actor
from ..audit import AuditService
from ..errors import NotFound, ValidationFailed
from .search import (
    FUSION_MODES,
    RETRIEVAL_MODES,
    KnowledgeSearchService,
    create_reranker,
    list_rerankers,
)

__all__ = [
    "RagParams",
    "RagPreset",
    "BUILTIN_PRESETS",
    "DEFAULT_PRESET_ID",
    "RagPresetService",
    "KBRagPreset",
    "KBRagComparison",
]


# --------------------------------------------------------------------------- #
# ORM：方案模板 + A/B 存档（迁移 0039 建表；此处在共享 Base 上注册同构模型，
# 使 conftest 的 create_all 与 alembic 两条建表路径保持同构）
# --------------------------------------------------------------------------- #


class KBRagPreset(Base):
    """用户保存的 RAG 方案模板（内置 4 预设在代码里，不入库）。"""

    __tablename__ = "kb_rag_presets"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # RagParams 的 JSON 序列化（服务层校验后写入）
    params: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[Any] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[Any] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_kb_rag_presets_owner_name"),
    )


class KBRagComparison(Base):
    """一次 A/B 对比存档：query + 各方案 id + 对比快照（切片截断存储）。"""

    __tablename__ = "kb_rag_ab_runs"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    # preset_ids 的 JSON 数组
    preset_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 对比 payload 的 JSON（各臂结果摘要，content 截断到 300 字）
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[Any] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_kb_rag_ab_runs_owner_created", "owner_id", "created_at"),
    )


# --------------------------------------------------------------------------- #
# 方案参数模型
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RagParams:
    """RAG 各阶段参数（可序列化、可校验、可对比）。

    * 检索期参数（直接传给 ``KnowledgeSearchService.search``）：``mode`` /
      ``fusion`` / ``rrf_k`` / ``weight_lexical`` / ``weight_vector`` /
      ``reranker`` / ``top_k``；
    * 预留参数位：``vector_floor``（包2 公开形参就绪后即插即用，当前由环境变量
      ``FIND_YOURSELF_KB_VECTOR_FLOOR`` 全局生效）；
    * 入库期参数：``chunk_size`` / ``chunk_overlap``（随方案存档；当前入库管线
      固定 800/100，云盘同步重导时按方案消费——见交付报告）。
    """

    mode: str = "hybrid"
    fusion: str = "rrf"
    rrf_k: int = 60
    weight_lexical: float = 1.0
    weight_vector: float = 1.0
    reranker: str = "noop"
    top_k: int = 8
    vector_floor: float | None = None
    chunk_size: int = 800
    chunk_overlap: int = 100

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: Any) -> "RagParams":
        if raw is None:
            return RagParams()
        if not isinstance(raw, dict):
            raise ValidationFailed(
                "rag_preset_invalid_params", "RAG 方案参数必须是 JSON object"
            )
        allowed = set(RagParams().__dict__) | {
            "weights",  # 兼容别名：weights=[lex, vec] 展开为两个权重
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValidationFailed(
                "rag_preset_unknown_param",
                f"未知方案参数：{unknown}；允许的参数：{sorted(allowed)}",
            )
        data = dict(raw)
        if "weights" in data:
            pair = data.pop("weights")
            if (
                not isinstance(pair, (list, tuple))
                or len(pair) != 2
                or not all(isinstance(x, (int, float)) and x >= 0 for x in pair)
            ):
                raise ValidationFailed(
                    "rag_preset_invalid_params",
                    "weights 必须是 [词法权, 向量权] 两个非负数",
                )
            data.setdefault("weight_lexical", float(pair[0]))
            data.setdefault("weight_vector", float(pair[1]))
        params = RagParams(
            mode=str(data.get("mode", "hybrid")).strip().lower(),
            fusion=str(data.get("fusion", "rrf")).strip().lower(),
            rrf_k=data.get("rrf_k", 60),
            weight_lexical=data.get("weight_lexical", 1.0),
            weight_vector=data.get("weight_vector", 1.0),
            reranker=str(data.get("reranker", "noop")).strip().lower(),
            top_k=data.get("top_k", 8),
            vector_floor=data.get("vector_floor"),
            chunk_size=data.get("chunk_size", 800),
            chunk_overlap=data.get("chunk_overlap", 100),
        )
        params.validate()
        return params

    def validate(self) -> None:
        if self.mode not in RETRIEVAL_MODES:
            raise ValidationFailed(
                "rag_preset_invalid_mode",
                f"未知检索模式 {self.mode!r}；允许：{list(RETRIEVAL_MODES)}",
            )
        if self.fusion not in FUSION_MODES:
            raise ValidationFailed(
                "rag_preset_invalid_fusion",
                f"未知融合公式 {self.fusion!r}；允许：{list(FUSION_MODES)}",
            )
        known_rerankers = list_rerankers()
        if self.reranker not in known_rerankers:
            raise ValidationFailed(
                "rag_preset_invalid_reranker",
                f"未知重排器 {self.reranker!r}；已注册：{known_rerankers}",
            )
        if not isinstance(self.top_k, int) or not (1 <= self.top_k <= 50):
            raise ValidationFailed("rag_preset_invalid_params", "top_k 必须是 1..50 的整数")
        if not isinstance(self.rrf_k, int) or self.rrf_k < 1:
            raise ValidationFailed("rag_preset_invalid_params", "rrf_k 必须是 >=1 的整数")
        if not (
            isinstance(self.weight_lexical, (int, float))
            and isinstance(self.weight_vector, (int, float))
            and self.weight_lexical >= 0
            and self.weight_vector >= 0
        ):
            raise ValidationFailed(
                "rag_preset_invalid_params", "权重必须是非负数"
            )
        if self.vector_floor is not None and not (
            isinstance(self.vector_floor, (int, float)) and 0 <= self.vector_floor <= 1
        ):
            raise ValidationFailed(
                "rag_preset_invalid_params", "vector_floor 必须在 [0,1]（预留参数位）"
            )
        if not isinstance(self.chunk_size, int) or not (100 <= self.chunk_size <= 4000):
            raise ValidationFailed(
                "rag_preset_invalid_params", "chunk_size 必须是 100..4000 的整数"
            )
        if not isinstance(self.chunk_overlap, int) or not (
            0 <= self.chunk_overlap < self.chunk_size
        ):
            raise ValidationFailed(
                "rag_preset_invalid_params", "chunk_overlap 必须满足 0 <= overlap < chunk_size"
            )


@dataclass(frozen=True)
class RagPreset:
    """一个具名方案：内置（代码）或保存（DB 行）。"""

    preset_id: str
    name: str
    description: str
    params: RagParams
    origin: str = "builtin"  # "builtin" | "saved"

    def as_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "name": self.name,
            "description": self.description,
            "origin": self.origin,
            "params": self.params.as_dict(),
        }


#: 内置 4 预设（A-云盘RAG-03）。检索调用全部落 ``search.py`` 公开 API。
BUILTIN_PRESETS: tuple[RagPreset, ...] = (
    RagPreset(
        preset_id="builtin:lexical_classic",
        name="纯词法（经典）",
        description=(
            "v1 词法语义原样保留：FTS5/LIKE 召回 + 词频/位置打分，无向量、无重排。"
            "确定性最强、零外部依赖、完全可解释。"
        ),
        params=RagParams(mode="lexical", reranker="noop"),
    ),
    RagPreset(
        preset_id="builtin:hybrid_rrf",
        name="混合检索 · RRF 融合",
        description=(
            "词法 + 向量双路召回，Reciprocal Rank Fusion（k=60）融合，不重排。"
            "向量路不可用时自动降级词法路并在 debug 里如实记录。"
        ),
        params=RagParams(mode="hybrid", fusion="rrf", rrf_k=60, reranker="noop"),
    ),
    RagPreset(
        preset_id="builtin:hybrid_weighted",
        name="混合检索 · 加权融合",
        description=(
            "词法 + 向量双路召回，两路分数 min-max 归一后加权（默认词法 1.0 / 向量 0.7，"
            "偏置信得过的词法证据）。适合两路分数可比的语料。"
        ),
        params=RagParams(
            mode="hybrid", fusion="weighted",
            weight_lexical=1.0, weight_vector=0.7, reranker="noop",
        ),
    ),
    RagPreset(
        preset_id="builtin:lexical_rerank",
        name="词法 + 词法重排",
        description=(
            "词法召回进 20 条候选池，再按词法重打分（rerank_score 单列）重排。"
            "无向量依赖、排序更稳，适合纯文本资料库。"
        ),
        params=RagParams(mode="lexical", reranker="lexical"),
    ),
)
BUILTIN_BY_ID: dict[str, RagPreset] = {p.preset_id: p for p in BUILTIN_PRESETS}
#: 缺省方案（对比/调试未指定时）
DEFAULT_PRESET_ID = "builtin:hybrid_rrf"


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #


class RagPresetService:
    """方案模板 CRUD + 调试器 + A/B 对比（owner 隔离，只管 saved 行；builtin 只读）。"""

    def __init__(self, session: Session, audit: AuditService | None = None):
        self.s = session
        self.audit = audit
        self.searcher = KnowledgeSearchService(session)

    # -- 解析 / 枚举 ---------------------------------------------------------- #
    def resolve_preset(self, preset_id: str | None, *, owner_id: str) -> RagPreset:
        key = (preset_id or DEFAULT_PRESET_ID).strip()
        if key in BUILTIN_BY_ID:
            return BUILTIN_BY_ID[key]
        if key.startswith("saved:"):
            row_id = key.split(":", 1)[1]
        else:
            row_id = key  # 也允许直接用 saved 行 id
        row = self._row(row_id, owner_id=owner_id)
        if row is None:
            raise NotFound(
                "rag_preset_not_found",
                f"RAG 方案 {preset_id!r} 不存在；内置方案：{sorted(BUILTIN_BY_ID)}",
            )
        try:
            params = RagParams.from_dict(json.loads(row.params or "{}"))
        except ValueError as exc:
            raise ValidationFailed(
                "rag_preset_corrupt", f"方案 {row.name} 的参数不是合法 JSON"
            ) from exc
        return RagPreset(
            preset_id=f"saved:{row.id}", name=row.name,
            description=row.description, params=params, origin="saved",
        )

    def list_presets(self, actor: Actor, *, owner_id: str) -> dict[str, Any]:
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        saved = [
            {
                "preset_id": f"saved:{row.id}",
                "name": row.name,
                "description": row.description,
                "origin": "saved",
                "params": json.loads(row.params or "{}"),
            }
            for row in self._rows(owner_id=owner_id)
        ]
        return {
            "builtin": [p.as_dict() for p in BUILTIN_PRESETS],
            "saved": saved,
            "builtin_count": len(BUILTIN_PRESETS),
            "saved_count": len(saved),
            "reserved_params": {
                "vector_floor": "包2 公开形参就绪后即插即用（当前环境变量全局生效）",
                "chunk_size/chunk_overlap": "入库期参数：随方案存档，云盘同步重导时消费",
            },
        }

    # -- saved CRUD ----------------------------------------------------------- #
    def save_preset(
        self,
        actor: Actor,
        *,
        owner_id: str,
        name: str,
        params: dict[str, Any] | None = None,
        description: str = "",
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValidationFailed("rag_preset_name_required", "方案名称不能为空")
        if len(clean_name) > 200:
            raise ValidationFailed("rag_preset_name_too_long", "方案名称超过 200 字符")
        validated = RagParams.from_dict(params or {})
        payload = json.dumps(validated.as_dict(), ensure_ascii=False)

        row = self._row((preset_id or "").removeprefix("saved:"), owner_id=owner_id)
        if row is None:
            dup = self.s.execute(
                select(KBRagPreset).where(
                    KBRagPreset.owner_id == owner_id, KBRagPreset.name == clean_name
                )
            ).scalar_one_or_none()
            if dup is not None:
                raise ValidationFailed(
                    "rag_preset_name_conflict",
                    f"方案名称 {clean_name!r} 已存在；如需更新请携带 preset_id",
                )
            import uuid

            row = KBRagPreset(id=uuid.uuid4().hex, owner_id=owner_id)
            self.s.add(row)
        row.name = clean_name
        row.description = (description or "").strip()[:500]
        row.params = payload
        row.updated_at = utcnow()
        self.s.flush()
        if self.audit is not None:
            self.audit.append(
                actor, "kb.rag_preset.saved", row.id, {"name": clean_name}
            )
        return {
            "preset_id": f"saved:{row.id}",
            "name": row.name,
            "description": row.description,
            "origin": "saved",
            "params": validated.as_dict(),
        }

    def delete_preset(self, actor: Actor, *, owner_id: str, preset_id: str) -> dict[str, Any]:
        actor.require_authenticated()
        row = self._row((preset_id or "").removeprefix("saved:"), owner_id=owner_id)
        if row is None:
            raise NotFound("rag_preset_not_found", f"RAG 方案 {preset_id!r} 不存在")
        self.s.delete(row)
        self.s.flush()
        return {"preset_id": preset_id, "deleted": True}

    # -- 调试器 --------------------------------------------------------------- #
    def run_debug(
        self,
        actor: Actor,
        *,
        owner_id: str,
        query: str,
        preset_id: str | None = None,
        params: dict[str, Any] | None = None,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """单方案调试：召回片段 / 分数 / 命中词 / 双路诊断 / 耗时，一次给全。"""
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        norm = (query or "").strip()
        if not norm:
            raise ValidationFailed("query_required", "检索词不能为空")

        preset = self.resolve_preset(preset_id, owner_id=owner_id)
        merged = RagParams.from_dict(
            {**preset.params.as_dict(), **(params or {})}
        ) if params else preset.params
        effective_top_k = (
            max(1, min(int(top_k), 50)) if top_k is not None else merged.top_k
        )

        started = time.perf_counter()
        outcome = self.searcher.search(
            actor,
            owner_id=owner_id,
            query=norm,
            top_k=effective_top_k,
            document_ids=document_ids,
            mode=merged.mode,
            fusion=merged.fusion,
            rrf_k=merged.rrf_k,
            weights=(merged.weight_lexical, merged.weight_vector),
            reranker=create_reranker(merged.reranker),
            include_debug=True,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

        return {
            "query": norm,
            "preset": {
                "preset_id": preset.preset_id,
                "name": preset.name,
                "origin": preset.origin,
            },
            "params": merged.as_dict(),
            "timing_ms": elapsed_ms,
            "count": outcome.get("count", 0),
            "results": outcome.get("results", []),
            "debug": outcome.get("debug", {}),
        }

    # -- A/B 对比 -------------------------------------------------------------- #
    def compare(
        self,
        actor: Actor,
        *,
        owner_id: str,
        query: str,
        preset_ids: list[str],
        top_k: int | None = None,
        document_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """多方案同题对比：各臂片段/分数/耗时 + 重合度矩阵（A/B 决策依据）。"""
        if not isinstance(preset_ids, list) or not (2 <= len(preset_ids) <= 4):
            raise ValidationFailed(
                "rag_compare_arms_invalid",
                "A/B 对比需要 2..4 个方案（builtin:* 或 saved:*）",
            )
        arms: list[dict[str, Any]] = []
        for pid in preset_ids:
            arm = self.run_debug(
                actor, owner_id=owner_id, query=query, preset_id=pid,
                top_k=top_k, document_ids=document_ids,
            )
            arms.append(arm)
        overlap = _overlap_matrix(arms)
        return {
            "query": query,
            "arms": arms,
            "overlap": overlap,
            "timings_ms": {a["preset"]["preset_id"]: a["timing_ms"] for a in arms},
            "counts": {a["preset"]["preset_id"]: a["count"] for a in arms},
        }

    def save_comparison(
        self, actor: Actor, *, owner_id: str, query: str, payload: dict[str, Any],
        preset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        import uuid

        snapshot = _compact_payload(payload)
        ids = preset_ids or [a.get("preset", {}).get("preset_id", "") for a in payload.get("arms", [])]
        row = KBRagComparison(
            id=uuid.uuid4().hex,
            owner_id=owner_id,
            query=(query or "")[:500],
            preset_ids=json.dumps([i for i in ids if i], ensure_ascii=False),
            payload=json.dumps(snapshot, ensure_ascii=False),
        )
        self.s.add(row)
        self.s.flush()
        if self.audit is not None:
            self.audit.append(actor, "kb.rag_compare.saved", row.id, {"arms": len(ids or [])})
        return {"run_id": f"ab:{row.id}", "query": row.query}

    def list_comparisons(self, actor: Actor, *, owner_id: str) -> dict[str, Any]:
        actor.require_authenticated()
        rows = list(
            self.s.execute(
                select(KBRagComparison)
                .where(KBRagComparison.owner_id == owner_id)
                .order_by(KBRagComparison.created_at.desc(), KBRagComparison.id)
                .limit(50)
            ).scalars()
        )
        return {
            "runs": [
                {
                    "run_id": f"ab:{r.id}",
                    "query": r.query,
                    "preset_ids": json.loads(r.preset_ids or "[]"),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "count": len(rows),
        }

    def get_comparison(self, actor: Actor, *, owner_id: str, run_id: str) -> dict[str, Any]:
        actor.require_authenticated()
        row = self.s.execute(
            select(KBRagComparison).where(
                KBRagComparison.id == run_id.removeprefix("ab:"),
                KBRagComparison.owner_id == owner_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("rag_comparison_not_found", f"A/B 存档 {run_id!r} 不存在")
        return {
            "run_id": f"ab:{row.id}",
            "query": row.query,
            "preset_ids": json.loads(row.preset_ids or "[]"),
            "payload": json.loads(row.payload or "{}"),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    # -- internals ------------------------------------------------------------ #
    def _rows(self, *, owner_id: str) -> list[KBRagPreset]:
        return list(
            self.s.execute(
                select(KBRagPreset)
                .where(KBRagPreset.owner_id == owner_id)
                .order_by(KBRagPreset.created_at.desc(), KBRagPreset.id)
            ).scalars()
        )

    def _row(self, row_id: str, *, owner_id: str) -> KBRagPreset | None:
        if not row_id:
            return None
        return self.s.execute(
            select(KBRagPreset).where(
                KBRagPreset.id == row_id, KBRagPreset.owner_id == owner_id
            )
        ).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _overlap_matrix(arms: list[dict[str, Any]]) -> dict[str, Any]:
    """各臂 Top-K chunk 集合的两两重合度（jaccard）+ 共享切片清单。"""
    sets = {
        a["preset"]["preset_id"]: {r["chunk_id"] for r in a.get("results", [])}
        for a in arms
    }
    keys = list(sets)
    pairwise: list[dict[str, Any]] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = sets[keys[i]], sets[keys[j]]
            union = a | b
            pairwise.append(
                {
                    "a": keys[i],
                    "b": keys[j],
                    "shared": len(a & b),
                    "jaccard": round(len(a & b) / len(union), 4) if union else 0.0,
                    "shared_chunk_ids": sorted(a & b),
                }
            )
    shared_all = set.intersection(*(sets[k] for k in keys)) if keys else set()
    return {"pairwise": pairwise, "shared_by_all": sorted(shared_all)}


def _compact_payload(payload: dict[str, Any], *, keep_per_arm: int = 5, content_cap: int = 300) -> dict[str, Any]:
    """A/B 存档瘦身：每臂只留前 N 条、content 截断（存档是证据不是全文备份）。"""
    arms = []
    for arm in payload.get("arms", []):
        results = [
            {
                "chunk_id": r.get("chunk_id"),
                "doc_name": r.get("doc_name"),
                "score": r.get("score"),
                "content": (r.get("content") or "")[:content_cap],
            }
            for r in (arm.get("results") or [])[:keep_per_arm]
        ]
        arms.append(
            {
                "preset": arm.get("preset"),
                "params": arm.get("params"),
                "timing_ms": arm.get("timing_ms"),
                "count": arm.get("count"),
                "results": results,
            }
        )
    return {
        "arms": arms,
        "overlap": payload.get("overlap", {}),
        "timings_ms": payload.get("timings_ms", {}),
        "counts": payload.get("counts", {}),
    }
