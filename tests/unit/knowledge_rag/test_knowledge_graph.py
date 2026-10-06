"""包6 · A-立体3D知识图谱-04 · 图谱数据层单测（抽取 / 存储 / 指针 / 端点）。

架构铁律验证点：图谱只存实体/关系/指针，**原文不搬运**。
"""

from __future__ import annotations

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.hub.adapters import InvokeResult
from find_yourself.services.knowledge.graph import (
    KBGraphEdge,
    KBGraphNode,
    LLMExtractor,
    RuleBasedExtractor,
    GraphService,
    get_extractor,
    list_extractors,
)
from find_yourself.services.knowledge.ingest import KnowledgeIngestService

OWNER = "owner-1"

DOC_TEXT = """# 命理基础

八字是命理工具。八字包括天干。

《子平真诠》是一部命理经典。命理依赖五行生克。

过旺导致失衡。正官属于十神。RRF 是融合算法。
"""


@pytest.fixture()
def graph(session):
    return GraphService(session)


@pytest.fixture()
def ingested(session, owner):
    ingest = KnowledgeIngestService(session, AuditService(session))
    doc = ingest.ingest_text(owner, owner_id=OWNER, name="命理基础.md", text=DOC_TEXT)
    assert doc.status == "ready"
    return doc


# --------------------------------------------------------------------------- #
# 规则抽取器（离线确定性）
# --------------------------------------------------------------------------- #

def test_rule_extractor_finds_typed_entities():
    entities, _relations = RuleBasedExtractor().extract(
        "《子平真诠》讲格局。RRF 是融合算法。", heading="第一章 / 总论"
    )
    names = {e.name for e in entities}
    assert "子平真诠" in names and "RRF" in names
    kinds = {e.name: e.kind for e in entities}
    assert kinds["子平真诠"] == "work"
    assert kinds["RRF"] == "concept"


def test_rule_extractor_relation_patterns():
    entities, relations = RuleBasedExtractor().extract(
        "八字是命理工具。八字包括天干。正官属于十神。命理依赖五行。过旺导致失衡。子平真诠又称子平书。"
    )
    by_rel = {(r.src, r.relation): r.dst for r in relations}
    assert by_rel[("八字", "is_a")] == "命理工具"
    assert by_rel[("八字", "has_part")] == "天干"
    assert by_rel[("正官", "belongs_to")] == "十神"
    assert by_rel[("命理", "depends_on")] == "五行"
    assert by_rel[("过旺", "causes")] == "失衡"
    assert by_rel[("子平真诠", "alias")] == "子平书"


def test_rule_extractor_is_deterministic():
    ext = RuleBasedExtractor()
    a = ext.extract(DOC_TEXT)
    b = ext.extract(DOC_TEXT)
    assert a == b


def test_rule_extractor_skips_stopword_entities():
    entities, _ = RuleBasedExtractor().extract("我们包括这个。它们是什么。")
    assert all(e.name not in ("我们", "这个", "它们") for e in entities)


# --------------------------------------------------------------------------- #
# LLM 抽取器（现有 provider 面可插拔）
# --------------------------------------------------------------------------- #

class _StubInvoker:
    def __init__(self, output, ok=True):
        self._output = output
        self._ok = ok
        self.calls = []

    def invoke(self, call):
        self.calls.append(call)
        return InvokeResult(ok=self._ok, output=self._output, error="" if self._ok else "boom")


def test_llm_extractor_parses_provider_json():
    stub = _StubInvoker(
        '{"entities": [{"name": "八字", "kind": "concept"}, {"name": "五行", "kind": "concept"}],'
        ' "relations": [{"src": "八字", "dst": "五行", "relation": "depends_on"}]}'
    )
    extractor = LLMExtractor(stub)
    entities, relations = extractor.extract("任意切片")
    assert [e.name for e in entities] == ["八字", "五行"]
    assert relations[0].relation == "depends_on"
    assert stub.calls and stub.calls[0].action == "chat"


def test_llm_extractor_unparseable_output_is_honest():
    stub = _StubInvoker("我觉得无法输出 JSON")
    extractor = LLMExtractor(stub)
    entities, relations = extractor.extract("任意切片")
    assert entities == [] and relations == []
    assert extractor.last_error == "unparseable_output"


def test_llm_extractor_provider_failure_is_honest():
    stub = _StubInvoker(None, ok=False)
    extractor = LLMExtractor(stub)
    assert extractor.extract("x")[0] == []
    assert "invoke_failed" in extractor.last_error


def test_llm_extractor_requires_invoker():
    with pytest.raises(ValidationFailed):
        LLMExtractor(None)


def test_extractor_registry_lists_rules_and_llm():
    assert set(list_extractors()) >= {"rules", "llm"}
    assert get_extractor("rules").name == "rules"


# --------------------------------------------------------------------------- #
# 构建与读取（指针不搬运原文）
# --------------------------------------------------------------------------- #

def test_graph_build_and_read_with_pointers(graph, owner, ingested):
    summary = graph.build(owner, owner_id=OWNER)
    assert summary["nodes_added"] > 0 and summary["edges_added"] > 0
    assert summary["chunks_scanned"] >= 1
    assert summary["truncated"] is False

    payload = graph.read_graph(owner, owner_id=OWNER)
    names = {n["name"] for n in payload["nodes"]}
    assert "八字" in names
    assert payload["counts"]["nodes"] >= 1
    edge = payload["edges"][0]
    # 指针语义：证据指向真实切片与文档
    assert edge["evidence"]["chunk_id"]
    assert edge["evidence"]["doc_id"] == ingested.id
    # 默认不携带原文（图存储没有原文副本）
    assert "text" not in edge["evidence"]


def test_graph_evidence_text_is_dereferenced_on_read(graph, owner, ingested):
    graph.build(owner, owner_id=OWNER)
    payload = graph.read_graph(owner, owner_id=OWNER, with_evidence_text=True, limit_edges=1)
    text = payload["edges"][0]["evidence"]["text"]
    assert text  # 读时按指针现取
    assert len(text) <= 200


def test_graph_tables_store_no_document_text(graph, owner, ingested, session):
    """架构铁律：节点/边表的列里没有任何原文内容。"""
    graph.build(owner, owner_id=OWNER)
    node_columns = {c.name for c in KBGraphNode.__table__.columns}
    edge_columns = {c.name for c in KBGraphEdge.__table__.columns}
    assert not (node_columns & {"content", "text", "snippet"})
    assert not (edge_columns & {"content", "text", "snippet"})


def test_graph_rebuild_is_idempotent_and_accumulates_weight(graph, owner, ingested):
    first = graph.build(owner, owner_id=OWNER)
    second = graph.build(owner, owner_id=OWNER, rebuild=True)
    # 重建清空重来：节点数一致，不会翻倍
    assert second["nodes_purged"] >= first["nodes_added"]
    assert second["nodes_added"] == first["nodes_added"]
    # 同关系多证据 → weight 累加（rebuild 后首条证据权重至少为 1）
    payload = graph.read_graph(owner, owner_id=OWNER)
    assert all(e["weight"] >= 1 for e in payload["edges"])


def test_graph_owner_isolation(graph, owner, ingested):
    graph.build(owner, owner_id=OWNER)
    other = Actor.owner("owner-2")
    payload = graph.read_graph(other, owner_id="owner-2")
    assert payload["nodes"] == [] and payload["edges"] == []
    assert payload["counts"]["nodes"] == 0


def test_graph_requires_owner(graph, owner):
    with pytest.raises(ValidationFailed) as err:
        graph.build(owner, owner_id="")
    assert err.value.code == "owner_required"


def test_graph_unknown_extractor_is_rejected(graph, owner, ingested):
    with pytest.raises(ValidationFailed) as err:
        graph.build(owner, owner_id=OWNER, extractor_name="psychic")
    assert err.value.code == "unknown_extractor"


def test_graph_build_with_llm_extractor_via_provider_seam(graph, owner, ingested):
    stub = _StubInvoker(
        '{"entities": [{"name": "八字", "kind": "concept"}, {"name": "五行", "kind": "concept"}],'
        ' "relations": [{"src": "八字", "dst": "五行", "relation": "depends_on"}]}'
    )
    summary = graph.build(owner, owner_id=OWNER, extractor_name="llm", llm_invoker=stub)
    assert summary["extractor"] == "llm"
    payload = graph.read_graph(owner, owner_id=OWNER)
    assert {n["name"] for n in payload["nodes"]} == {"八字", "五行"}
