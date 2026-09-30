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
    assert "Alice" in imp.subject_candidates
    assert "Bob" in imp.subject_candidates


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
