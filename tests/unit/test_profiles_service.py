"""Unit tests for ProfileService (04 specification)."""

from datetime import datetime
import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from find_yourself.services.profile import ProfileService


@pytest.fixture
def profile_svc(session, audit):
    return ProfileService(session, audit)


def test_create_and_list_subjects(profile_svc, owner):
    s1 = profile_svc.create_subject(owner, label="My Self", kind="self")
    assert s1.kind == "self"
    assert s1.confirmed is True

    # Re-creating self returns existing
    s1_dup = profile_svc.create_subject(owner, label="Another Self", kind="self")
    assert s1_dup.id == s1.id

    # Create other object subjects (e.g. project, topic)
    s2 = profile_svc.create_subject(owner, label="Project Falcon", kind="project", description="An AI infra project")
    assert s2.kind == "project"
    assert s2.label == "Project Falcon"

    subjs = profile_svc.list_subjects(owner)
    labels = [s.label for s in subjs]
    assert "My Self" in labels
    assert "Project Falcon" in labels


def test_import_document_and_segmentation(profile_svc, owner):
    doc = """
    Alice: 我喜欢用清晰的模块化架构设计系统。
    Bob: 这有助于长期维护。
    Alice: 我的目标是确保系统具有防篡改的审计链与完备的安全边界。
    """
    s = profile_svc.create_subject(owner, label="Self", kind="self")
    imp = profile_svc.import_document(owner, content=doc, filename="chat.txt", subject_id=s.id)

    assert imp.status == "parsed"
    assert imp.size > 0
    spk_names = [c["speaker"] if isinstance(c, dict) else c for c in imp.subject_candidates]
    assert "Alice" in spk_names
    assert "Bob" in spk_names


def test_run_profiling_and_clusters_synthesis(profile_svc, owner):
    doc = """
    我倾向于逻辑严密的系统设计与深度反思。
    我的目标是构建可靠的长期记忆与多Agent协作框架。
    """
    s = profile_svc.create_subject(owner, label="Self", kind="self")
    profile_svc.import_document(owner, content=doc, filename="notes.txt", subject_id=s.id)

    rev = profile_svc.run_profiling(owner, s.id, rule_version="v1.0")

    assert rev.revision == 1
    assert rev.subject_id == s.id
    assert "title" in rev.core_summary
    assert len(rev.clusters) >= 2
    assert len(rev.metrics) >= 4
    assert len(rev.limitations) >= 2

    # Check that metric types declare corpus stats or self reports, strictly not clinical fake tests
    metric_types = {m["metric_type"] for m in rev.metrics}
    assert metric_types.issubset({"corpus_stat", "self_report"})


def test_submit_feedback_and_review_state(profile_svc, owner):
    doc = "我喜欢分析底层协议。"
    s = profile_svc.create_subject(owner, label="Self", kind="self")
    profile_svc.import_document(owner, content=doc, filename="log.txt", subject_id=s.id)
    rev = profile_svc.run_profiling(owner, s.id)

    # Pick first node's evidence ref
    node = rev.clusters[0]["nodes"][0]
    ev_id = node["evidence_refs"][0]

    # Feedback: reject
    fb = profile_svc.submit_feedback(owner, evidence_id=ev_id, action="reject", feedback_text="不是长期偏好")
    assert fb.action == "reject"

    from find_yourself.db.models import ProfileEvidence
    ev = profile_svc.session.get(ProfileEvidence, ev_id)
    assert ev.review_status == "rejected"


def test_delete_import_cascades_and_tombstone(profile_svc, owner):
    doc = "Alice: 测试删除级联。"
    s = profile_svc.create_subject(owner, label="Alice", kind="person")
    imp = profile_svc.import_document(owner, content=doc, filename="to_delete.txt", subject_id=s.id)

    from find_yourself.db.models import ProfileImport, SourceSegment, Tombstone
    assert profile_svc.session.get(ProfileImport, imp.id) is not None

    # Delete import
    profile_svc.delete_import(owner, imp.id)
    assert profile_svc.session.get(ProfileImport, imp.id) is None

    # Check tombstone
    tomb = profile_svc.session.query(Tombstone).filter_by(target_id=imp.id).first()
    assert tomb is not None
    assert tomb.target_kind == "profile_import"


def test_cross_subject_isolation_mixed_samples(profile_svc, owner):
    """Threshold 1: 4 subjects (self, project, work, person) with distinct corpora. Zero bleed."""
    s_self = profile_svc.create_subject(owner, label="本人", kind="self")
    s_proj = profile_svc.create_subject(owner, label="项目Alpha", kind="project")
    s_work = profile_svc.create_subject(owner, label="工作任务Beta", kind="work")
    s_other = profile_svc.create_subject(owner, label="同事Charlie", kind="person")

    # Import distinct corpora for each
    profile_svc.import_document(owner, content="我喜欢深入推演底层协议与防篡改审计链。", filename="self.txt", subject_id=s_self.id)
    profile_svc.import_document(owner, content="项目Alpha采用分布式流式架构与零信任网关。", filename="proj.txt", subject_id=s_proj.id)
    profile_svc.import_document(owner, content="工作任务Beta聚焦于自动化回归流水线与安全合规核验。", filename="work.txt", subject_id=s_work.id)

    # Profiling s_proj
    rev_proj = profile_svc.run_profiling(owner, s_proj.id)
    proj_claims = [node["description"] for cl in rev_proj.clusters for node in cl["nodes"]]
    for claim in proj_claims:
        assert "分布式流式架构" in claim or "零信任网关" in claim
        assert "底层协议" not in claim  # Zero bleed from self!
        assert "回归流水线" not in claim  # Zero bleed from work!

    # Subject s_other has no imports -> must raise ValidationFailed, never fallback!
    with pytest.raises(ValidationFailed) as exc_info:
        profile_svc.run_profiling(owner, s_other.id)
    assert "No imported corpus segments associated with subject" in str(exc_info.value)


def test_unconfirmed_multi_speaker_not_self_report(profile_svc, owner):
    """Threshold 1: Multi-speaker transcripts without confirmation do not enter formal self profile."""
    doc = """
    Alice: 我喜欢微内核架构与极简接口。
    Bob: 我喜欢重度依赖集中式中间件与全局缓存。
    Charlie: 每天需要做多次例会同步。
    """
    s_self = profile_svc.create_subject(owner, label="本人", kind="self")
    imp = profile_svc.import_document(owner, content=doc, filename="multi_speaker.txt", subject_id=s_self.id)

    rev = profile_svc.run_profiling(owner, s_self.id)

    # Bob's statement has '我喜欢', but speaker is Bob (unconfirmed) -> MUST NOT be self_report or accepted!
    all_nodes = [node for cl in rev.clusters for node in cl["nodes"]]
    bob_node = next((n for n in all_nodes if "集中式中间件" in n["description"]), None)
    assert bob_node is not None
    assert bob_node["claim_kind"] == "third_party_statement"
    assert bob_node["review_status"] == "candidate"  # Not auto accepted

    # Check limitations note unconfirmed speakers
    limit_text = " ".join(rev.limitations)
    assert "未确认说话人" in limit_text
    assert "Bob" in limit_text or "Alice" in limit_text


def test_confirm_speakers_attribution(profile_svc, owner):
    """Explicit speaker confirmation maps speaker to self."""
    doc = """
    Alice: 我倾向于使用强类型契约与严谨边界。
    """
    s_self = profile_svc.create_subject(owner, label="本人", kind="self")
    imp = profile_svc.import_document(owner, content=doc, filename="dialogue.txt", subject_id=s_self.id)

    # Confirm Alice is self
    res = profile_svc.confirm_speakers(owner, imp.id, {"Alice": "self"})
    assert res["updated_segments"] >= 1

    rev = profile_svc.run_profiling(owner, s_self.id)
    all_nodes = [node for cl in rev.clusters for node in cl["nodes"]]
    alice_node = next((n for n in all_nodes if "强类型契约" in n["description"]), None)
    assert alice_node is not None
    assert alice_node["claim_kind"] == "self_report"
    assert alice_node["review_status"] == "accepted"


def test_reproducible_text_metrics_different_inputs(profile_svc, owner):
    """Threshold 2: Deterministic reproducible metrics. Different inputs -> different metrics. No fake scores."""
    doc_a = "我喜欢推演架构设计。我倾向于逻辑严密。"
    doc_b = "今天天气很好，阳光充足，去公园散步并整理了书架，阅读了三本摄影集。"

    s_a = profile_svc.create_subject(owner, label="Subject A", kind="person")
    s_b = profile_svc.create_subject(owner, label="Subject B", kind="person")

    profile_svc.import_document(owner, content=doc_a, filename="doc_a.txt", subject_id=s_a.id)
    profile_svc.import_document(owner, content=doc_b, filename="doc_b.txt", subject_id=s_b.id)

    rev_a1 = profile_svc.run_profiling(owner, s_a.id)
    rev_a2 = profile_svc.run_profiling(owner, s_a.id)
    rev_b = profile_svc.run_profiling(owner, s_b.id)

    # Determinism: rev_a1 and rev_a2 metrics must be identical
    metrics_a1 = {m["dimension"]: m["raw_value"] for m in rev_a1.metrics}
    metrics_a2 = {m["dimension"]: m["raw_value"] for m in rev_a2.metrics}
    assert metrics_a1 == metrics_a2

    # Different inputs: metrics must differ
    metrics_b = {m["dimension"]: m["raw_value"] for m in rev_b.metrics}
    assert metrics_a1["有效语料总字数"] != metrics_b["有效语料总字数"]
    assert metrics_a1["工程与逻辑聚焦度"] != metrics_b["工程与逻辑聚焦度"]

    # Verify NO fixed constant scores (88, 92, 85, 80, 95)
    for m in rev_a1.metrics + rev_b.metrics:
        assert "score" not in m or m.get("calculation_formula") is not None
        assert "raw_value" in m
        assert "display_value" in m

    # Verify formal_norm is False and norm note is present
    assert rev_a1.core_summary.get("formal_norm") is False
    assert "未接入标准化心理量表" in rev_a1.core_summary.get("norm_note", "")


def test_delete_import_invalidates_derived_revisions(profile_svc, owner):
    """Threshold 1: Deletion cascades to derived revisions and marks them invalidated."""
    doc = "我喜欢逻辑严谨的代码。"
    s = profile_svc.create_subject(owner, label="Self", kind="self")
    imp = profile_svc.import_document(owner, content=doc, filename="data.txt", subject_id=s.id)
    rev = profile_svc.run_profiling(owner, s.id)
    assert rev.user_review_state != "invalidated"

    # Delete import
    profile_svc.delete_import(owner, imp.id)

    from find_yourself.db.models import ProfileRevision
    rev_refetched = profile_svc.session.get(ProfileRevision, rev.id)
    assert rev_refetched.user_review_state == "invalidated"
    assert "deleted" in rev_refetched.core_summary.get("invalidation_note", "")


def test_feedback_rejection_cascades_to_revision_clusters(profile_svc, owner):
    """Threshold 1: Evidence rejection updates clusters node review_status to rejected."""
    doc = "我喜欢推演架构设计。"
    s = profile_svc.create_subject(owner, label="Self", kind="self")
    profile_svc.import_document(owner, content=doc, filename="notes.txt", subject_id=s.id)
    rev = profile_svc.run_profiling(owner, s.id)

    node = rev.clusters[0]["nodes"][0]
    ev_id = node["evidence_refs"][0]

    # User rejects the evidence
    profile_svc.submit_feedback(owner, evidence_id=ev_id, action="reject", feedback_text="否认此条特征")

    from find_yourself.db.models import ProfileRevision
    rev_refetched = profile_svc.session.get(ProfileRevision, rev.id)
    assert rev_refetched.user_review_state == "feedback_applied"
    matching_node = next(n for cl in rev_refetched.clusters for n in cl["nodes"] if ev_id in n["evidence_refs"])
    assert matching_node["review_status"] == "rejected"
    assert matching_node["confidence"] == 0.0


def test_delete_import_deep_redaction_and_revision_filtering(profile_svc, owner):
    """07 Threshold 3: Deletion physically scrubs text in revisions and filters from default read APIs."""
    doc = "我喜欢微服务治理与可观测性。"
    s = profile_svc.create_subject(owner, label="本人", kind="self")
    imp = profile_svc.import_document(owner, content=doc, filename="sensitive_doc.txt", subject_id=s.id)
    rev = profile_svc.run_profiling(owner, s.id)

    # Pre-deletion: original text is in clusters
    assert any("可观测性" in n["description"] for cl in rev.clusters for n in cl["nodes"])
    assert "基于" in rev.core_summary.get("summary", "")

    # Perform physical deletion
    profile_svc.delete_import(owner, imp.id)

    # Post-deletion: text scrubbed in DB
    from find_yourself.db.models import ProfileRevision
    rev_refetched = profile_svc.session.get(ProfileRevision, rev.id)
    assert rev_refetched.user_review_state == "invalidated"
    assert "REDACTED" in rev_refetched.core_summary.get("summary", "")
    assert "可观测性" not in rev_refetched.core_summary.get("summary", "")

    for cl in rev_refetched.clusters:
        for node in cl["nodes"]:
            assert "REDACTED" in node["description"]
            assert "可观测性" not in node["description"]
            assert node.get("source_segment_id") is None
            assert node.get("locator") == "[DELETED]"
            assert node.get("speaker") == "[REDACTED]"

    # list_revisions defaults to include_invalidated=False -> empty!
    revs_default = profile_svc.list_revisions(owner, s.id, include_invalidated=False)
    assert len(revs_default) == 0

    # list_revisions with include_invalidated=True -> returns redacted version
    revs_all = profile_svc.list_revisions(owner, s.id, include_invalidated=True)
    assert len(revs_all) == 1
    assert revs_all[0].id == rev.id

    # get_revision defaults to allow_invalidated=False -> raises NotFound
    with pytest.raises(NotFound) as exc_info:
        profile_svc.get_revision(owner, rev.id, allow_invalidated=False)
    assert "has been invalidated" in str(exc_info.value)

    # get_revision with allow_invalidated=True -> succeeds with scrubbed content
    rev_redacted = profile_svc.get_revision(owner, rev.id, allow_invalidated=True)
    assert rev_redacted.id == rev.id


def test_dialogue_vs_self_metrics_strict_separation(profile_svc, owner):
    """07 Threshold 5: Third-party dialogue statements do not contaminate self preference metrics."""
    doc = """
    Alice: 我喜欢用Rust编写高性能网络代理。
    Bob: 我喜欢用Python写数据脚本。我喜欢用动态类型。
    """
    s = profile_svc.create_subject(owner, label="本人", kind="self")
    imp = profile_svc.import_document(owner, content=doc, filename="dialogue_chat.txt", subject_id=s.id)

    # Confirm Alice is self
    profile_svc.confirm_speakers(owner, imp.id, {"Alice": "self"})

    rev = profile_svc.run_profiling(owner, s.id)

    # Check metrics
    pref_metric = next(m for m in rev.metrics if m["dimension"] == "自述偏好表达密度")
    # Only Alice's 1 '我喜欢' should be counted in self numerator, not Bob's 2!
    assert pref_metric["numerator"] == 1
    assert "本人确认自述切片字数" in pref_metric["denominator_source"]

    # Dialogue context metrics are explicitly distinct
    dialogue_seg_metric = next(m for m in rev.metrics if m["dimension"] == "对话总切片数")
    assert dialogue_seg_metric["raw_value"] == 2

    third_party_seg_metric = next(m for m in rev.metrics if m["dimension"] == "第三方切片数")
    assert third_party_seg_metric["raw_value"] == 1


def test_speaker_confirmation_preserves_raw_speaker(profile_svc, owner):
    """07 P1-3: confirm_speakers updates speaker while preserving immutable raw_speaker."""
    doc = """
    Speaker1: 这是一个测试语句。
    """
    s = profile_svc.create_subject(owner, label="本人", kind="self")
    imp = profile_svc.import_document(owner, content=doc, filename="raw_spk.txt", subject_id=s.id)

    from find_yourself.db.models import SourceSegment
    seg = profile_svc.session.query(SourceSegment).filter_by(import_id=imp.id).first()
    assert seg.speaker == "Speaker1"
    assert seg.raw_speaker == "Speaker1"

    # Confirm Speaker1 is self
    profile_svc.confirm_speakers(owner, imp.id, {"Speaker1": "self"})

    seg_after = profile_svc.session.query(SourceSegment).filter_by(import_id=imp.id).first()
    assert seg_after.speaker == "self"
    assert seg_after.raw_speaker == "Speaker1"  # Immutable original preserved!

