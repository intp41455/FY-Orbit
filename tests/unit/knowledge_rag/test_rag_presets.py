"""包6 · A-云盘RAG-02/03 · RAG 方案模板 + 调试器 + A/B 对比单测。

检索调用全部走 ``services/knowledge/search.py`` 公开 API（包2 面），
本文件验证方案层的参数编排 / 校验 / 对比 / 存档语义。
"""

from __future__ import annotations

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.knowledge.ingest import KnowledgeIngestService
from find_yourself.services.knowledge.rag_presets import (
    BUILTIN_PRESETS,
    DEFAULT_PRESET_ID,
    RagParams,
    RagPresetService,
)

OWNER = "owner-1"
DOC_TEXT = """# 命理笔记

八字用神，专求月令。以日干配月支，而生克不同，格局分焉。

五行贵在中和，过旺过弱皆宜调候。财官印食为四吉神。

十神是命理分析的工具。命理依赖五行生克。
"""


@pytest.fixture()
def svc(session):
    return RagPresetService(session, AuditService(session))


@pytest.fixture()
def ingested(session, owner):
    ingest = KnowledgeIngestService(session, AuditService(session))
    doc = ingest.ingest_text(
        owner, owner_id=OWNER, name="子平真诠笔记.md", text=DOC_TEXT, source="baidu_pan",
    )
    assert doc.status == "ready"
    return doc


# --------------------------------------------------------------------------- #
# 内置预设与参数校验
# --------------------------------------------------------------------------- #

def test_builtin_presets_at_least_four_with_distinct_modes():
    assert len(BUILTIN_PRESETS) >= 4
    ids = [p.preset_id for p in BUILTIN_PRESETS]
    assert len(set(ids)) == len(ids)
    modes = {(p.params.mode, p.params.reranker, p.params.fusion) for p in BUILTIN_PRESETS}
    # 4 预设覆盖：纯词法 / RRF / 加权 / 词法+重排 四种形态
    assert ("lexical", "noop", "rrf") in modes
    assert ("hybrid", "noop", "rrf") in modes
    assert ("hybrid", "noop", "weighted") in modes
    assert ("lexical", "lexical", "rrf") in modes
    for preset in BUILTIN_PRESETS:
        preset.params.validate()  # 内置预设自身必须合法


def test_params_reject_unknown_key():
    with pytest.raises(ValidationFailed) as err:
        RagParams.from_dict({"bogus_param": 1})
    assert err.value.code == "rag_preset_unknown_param"


def test_params_reject_invalid_mode_and_reranker():
    with pytest.raises(ValidationFailed):
        RagParams.from_dict({"mode": "quantum"})
    with pytest.raises(ValidationFailed):
        RagParams.from_dict({"reranker": "gpt9"})
    with pytest.raises(ValidationFailed):
        RagParams.from_dict({"chunk_overlap": 999, "chunk_size": 800})


def test_weights_alias_expands_to_pair():
    params = RagParams.from_dict({"weights": [1.0, 0.5]})
    assert params.weight_lexical == 1.0
    assert params.weight_vector == 0.5


# --------------------------------------------------------------------------- #
# 方案 CRUD（owner 隔离）
# --------------------------------------------------------------------------- #

def test_save_list_delete_preset_cycle(svc, owner):
    saved = svc.save_preset(
        owner, owner_id=OWNER, name="我的混合方案",
        params={"mode": "hybrid", "fusion": "weighted", "weights": [1.2, 0.8]},
        description="偏词法的加权混合",
    )
    assert saved["preset_id"].startswith("saved:")
    listed = svc.list_presets(owner, owner_id=OWNER)
    assert listed["builtin_count"] >= 4 and listed["saved_count"] == 1
    assert listed["saved"][0]["name"] == "我的混合方案"
    deleted = svc.delete_preset(owner, owner_id=OWNER, preset_id=saved["preset_id"])
    assert deleted["deleted"] is True
    assert svc.list_presets(owner, owner_id=OWNER)["saved_count"] == 0


def test_preset_name_conflict_is_explicit(svc, owner):
    svc.save_preset(owner, owner_id=OWNER, name="方案A", params={"mode": "lexical"})
    with pytest.raises(ValidationFailed) as err:
        svc.save_preset(owner, owner_id=OWNER, name="方案A", params={"mode": "lexical"})
    assert err.value.code == "rag_preset_name_conflict"


def test_preset_owner_isolation(svc, owner):
    other = Actor.owner("owner-2")
    svc.save_preset(owner, owner_id=OWNER, name="私有方案", params={"mode": "lexical"})
    with pytest.raises(NotFound):
        svc.resolve_preset("saved:nonexistent", owner_id="owner-2")
    assert svc.list_presets(other, owner_id="owner-2")["saved_count"] == 0


def test_resolve_unknown_preset_is_not_found(svc, owner):
    with pytest.raises(NotFound):
        svc.resolve_preset("builtin:missing", owner_id=OWNER)


# --------------------------------------------------------------------------- #
# 调试器：片段 / 分数 / 命中词 / 双路诊断 / 耗时
# --------------------------------------------------------------------------- #

def test_run_debug_returns_hits_scores_timing_and_debug(svc, owner, ingested):
    outcome = svc.run_debug(
        owner, owner_id=OWNER, query="八字 用神", preset_id="builtin:lexical_classic",
    )
    assert outcome["count"] >= 1
    assert outcome["timing_ms"] >= 0
    assert outcome["preset"]["preset_id"] == "builtin:lexical_classic"
    top = outcome["results"][0]
    assert top["doc_id"] == ingested.id
    assert top["score"] > 0
    assert top["matched_terms"]
    debug = outcome["debug"]
    assert debug["mode"] == "lexical"
    assert debug["lexical_hits"]  # 词法召回命中（chunk_id 列表）
    assert all(h["doc_id"] == ingested.id for h in outcome["results"])


def test_run_debug_preset_modes_are_recorded_and_respected(svc, owner, ingested):
    lex = svc.run_debug(owner, owner_id=OWNER, query="五行 中和", preset_id="builtin:lexical_classic")
    rerank = svc.run_debug(owner, owner_id=OWNER, query="五行 中和", preset_id="builtin:lexical_rerank")
    assert lex["debug"]["mode"] == "lexical" and lex["debug"]["reranker"] == "noop"
    assert rerank["debug"]["reranker"] == "lexical"
    assert rerank["results"][0]["doc_id"] == ingested.id


def test_run_debug_hybrid_degrades_honestly_without_vector_backend(svc, owner, ingested):
    outcome = svc.run_debug(owner, owner_id=OWNER, query="命理 工具", preset_id="builtin:hybrid_rrf")
    # 向量路任何环节不可用 → 自动降级词法并在 warnings 如实记录；绝不中断。
    # 向量后端可用时 warnings 为空、结果照常——两种环境都必须语义自洽。
    assert outcome["debug"]["mode"] == "hybrid"
    assert all(("vector" in w or "embedding" in w) for w in outcome["debug"]["warnings"])
    if outcome["count"] == 0:
        assert outcome["debug"]["warnings"], "空结果必须能从 warnings 里看出原因"


def test_run_debug_with_inline_params_overrides_preset(svc, owner, ingested):
    outcome = svc.run_debug(
        owner, owner_id=OWNER, query="八字", preset_id="builtin:lexical_classic",
        params={"top_k": 2, "reranker": "lexical"},
    )
    assert outcome["params"]["top_k"] == 2
    assert outcome["params"]["reranker"] == "lexical"


def test_run_debug_empty_query_is_rejected(svc, owner, ingested):
    with pytest.raises(ValidationFailed) as err:
        svc.run_debug(owner, owner_id=OWNER, query="   ")
    assert err.value.code == "query_required"


# --------------------------------------------------------------------------- #
# A/B 对比与存档
# --------------------------------------------------------------------------- #

def test_compare_two_arms_with_overlap_and_timings(svc, owner, ingested):
    report = svc.compare(
        owner, owner_id=OWNER, query="八字 用神",
        preset_ids=["builtin:lexical_classic", "builtin:lexical_rerank"],
    )
    assert len(report["arms"]) == 2
    assert set(report["timings_ms"]) == {"builtin:lexical_classic", "builtin:lexical_rerank"}
    assert set(report["counts"]) == set(report["timings_ms"])
    pair = report["overlap"]["pairwise"][0]
    # 两臂同为词法：重合度应为 1
    assert pair["shared"] == report["counts"]["builtin:lexical_classic"]
    assert pair["jaccard"] == 1.0


def test_compare_requires_two_to_four_arms(svc, owner, ingested):
    with pytest.raises(ValidationFailed) as err:
        svc.compare(owner, owner_id=OWNER, query="八字", preset_ids=["builtin:lexical_classic"])
    assert err.value.code == "rag_compare_arms_invalid"
    with pytest.raises(ValidationFailed):
        svc.compare(owner, owner_id=OWNER, query="八字", preset_ids=[DEFAULT_PRESET_ID] * 5)


def test_compare_rejects_unknown_arm(svc, owner, ingested):
    with pytest.raises(NotFound):
        svc.compare(owner, owner_id=OWNER, query="八字",
                    preset_ids=["builtin:lexical_classic", "builtin:missing"])


def test_save_comparison_truncates_content_and_roundtrips(svc, owner, ingested):
    report = svc.compare(
        owner, owner_id=OWNER, query="八字 用神",
        preset_ids=["builtin:lexical_classic", "builtin:hybrid_rrf"],
    )
    saved = svc.save_comparison(owner, owner_id=OWNER, query="八字 用神", payload=report)
    assert saved["run_id"].startswith("ab:")
    runs = svc.list_comparisons(owner, owner_id=OWNER)
    assert runs["count"] == 1
    detail = svc.get_comparison(owner, owner_id=OWNER, run_id=saved["run_id"])
    for arm in detail["payload"]["arms"]:
        for hit in arm["results"]:
            assert len(hit["content"]) <= 300
    assert detail["preset_ids"] == ["builtin:lexical_classic", "builtin:hybrid_rrf"]
