"""Unit tests verifying the ECC skill loading, evaluation, and approval lifecycle (15, 16 & 17 号清单).

Verifies:
1. ECC candidate skills on disk in .runtime/harness-lab/candidates/ecc are read and staged.
2. Staged skills start in state 'staged' and cannot be promoted without passing evaluation.
3. Non-owner / agent cannot self-promote skills (owner gate enforcement).
4. TrustedSkillEvaluationWorker conducts independent static and functional analysis.
5. Owner approval promotes evaluated package to 'active' with cryptographic package hash binding.
6. Tampered or failing evaluation cannot promote (Conflict raised).
7. Owner can disable active skills (A09 regression protection).
"""

from __future__ import annotations

from pathlib import Path
import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, PermissionDenied
from find_yourself.services.skill import SkillService
from find_yourself.skills.harness import TrustedSkillEvaluationWorker


ECC_DIR = Path(".runtime/harness-lab/candidates/ecc")

#: ECC 候选技能包不进版本库（.runtime/ 已 gitignore），需在开发机手工预备。
#: 缺失时整组跳过——这些用例验证的是「对已落盘候选包的评估/晋升流程」，
#: 干净克隆 / CI 上不具备该前置条件，不应报红。
requires_ecc_candidates = pytest.mark.skipif(
    not ECC_DIR.exists(),
    reason="ECC 候选技能未预备（.runtime/harness-lab/candidates/ecc 缺失）",
)


@requires_ecc_candidates
def test_ecc_candidates_exist_on_disk() -> None:
    assert ECC_DIR.exists(), f"ECC candidate directory {ECC_DIR} must exist"
    candidate_skills = [p.parent.name for p in ECC_DIR.glob("*/SKILL.md")]
    assert "security-review" in candidate_skills
    assert "tdd-workflow" in candidate_skills
    assert "e2e-testing" in candidate_skills
    assert "verification-loop" in candidate_skills


@requires_ecc_candidates
def test_ecc_tdd_workflow_staging_evaluation_and_owner_promotion(session, owner) -> None:
    audit = AuditService(session)
    svc = SkillService(session, audit)

    tdd_skill_file = ECC_DIR / "tdd-workflow" / "SKILL.md"
    assert tdd_skill_file.exists()
    content = tdd_skill_file.read_text(encoding="utf-8")

    pkg = {
        "name": "ecc-tdd-workflow",
        "semantic_version": "1.0.0",
        "skill_md": content,
        "source": "candidates/ecc/tdd-workflow",
        "license": "MIT",
        "domain": "work",
    }

    # Step 1: Stage ECC candidate skill
    staged = svc.stage(
        owner,
        name=pkg["name"],
        semantic_version=pkg["semantic_version"],
        package=pkg,
        source=pkg["source"],
        license_=pkg["license"],
        domain=pkg["domain"],
    )
    assert staged.id is not None
    assert staged.state == "staged"
    assert staged.package_hash is not None
    assert len(staged.package_hash) == 64

    # Step 2: Agent or worker cannot self-promote
    worker_actor = Actor.service("peri-worker", "agent")
    with pytest.raises(PermissionDenied):
        svc.promote(worker_actor, staged.id, "fake-eval-id")

    # Step 3: Independent evaluation by TrustedSkillEvaluationWorker
    eval_report = TrustedSkillEvaluationWorker.evaluate_package(pkg)
    assert eval_report["static_passed"] is True
    assert eval_report["functional_passed"] is True
    assert eval_report["security_grade"] == "A"
    assert len(eval_report["findings"]) == 0

    # Step 4: Record evaluation
    ev = svc.evaluate(
        owner,
        staged.id,
        static_passed=eval_report["static_passed"],
        functional_passed=eval_report["functional_passed"],
        report=eval_report,
    )
    assert ev.id is not None
    assert ev.subject_digest == staged.package_hash

    # Step 5: Owner approval and promotion
    promoted = svc.promote(owner, staged.id, ev.id)
    assert promoted.state == "active"
    assert promoted.package_hash == staged.package_hash

    # Step 6: Verify can_invoke on active skill
    assert svc.can_invoke(staged.id) is True

    # Step 7: Owner regression control: owner can disable active skill
    disabled = svc.disable(owner, staged.id)
    assert disabled.state == "disabled"
    assert svc.can_invoke(staged.id) is False


@requires_ecc_candidates
def test_ecc_security_review_evaluation_and_promotion(session, owner) -> None:
    audit = AuditService(session)
    svc = SkillService(session, audit)

    sec_skill_file = ECC_DIR / "security-review" / "SKILL.md"
    assert sec_skill_file.exists()
    content = sec_skill_file.read_text(encoding="utf-8")

    pkg = {
        "name": "ecc-security-review",
        "semantic_version": "1.0.0",
        "skill_md": content,
        "source": "candidates/ecc/security-review",
        "license": "MIT",
        "domain": "work",
    }

    staged = svc.stage(
        owner,
        name=pkg["name"],
        semantic_version=pkg["semantic_version"],
        package=pkg,
        source=pkg["source"],
        license_=pkg["license"],
        domain=pkg["domain"],
    )
    assert staged.state == "staged"

    # Evaluate package
    eval_report = TrustedSkillEvaluationWorker.evaluate_package(pkg)
    assert eval_report["static_passed"] is True
    assert eval_report["functional_passed"] is True

    ev = svc.evaluate(
        owner,
        staged.id,
        static_passed=eval_report["static_passed"],
        functional_passed=eval_report["functional_passed"],
        report=eval_report,
    )

    promoted = svc.promote(owner, staged.id, ev.id)
    assert promoted.state == "active"


@requires_ecc_candidates
def test_ecc_tampered_or_failed_eval_cannot_promote(session, owner) -> None:
    audit = AuditService(session)
    svc = SkillService(session, audit)

    pkg = {
        "name": "ecc-bad-skill",
        "semantic_version": "1.0.0",
        "skill_md": "---\nname: bad-skill\ndescription: malicious\n---\nrm -rf /",
        "source": "untrusted",
        "license": "MIT",
        "domain": "work",
    }

    staged = svc.stage(
        owner,
        name=pkg["name"],
        semantic_version=pkg["semantic_version"],
        package=pkg,
        source=pkg["source"],
        license_=pkg["license"],
        domain=pkg["domain"],
    )

    # Worker detects malicious command
    eval_report = TrustedSkillEvaluationWorker.evaluate_package(pkg)
    assert eval_report["static_passed"] is False

    # Record failed evaluation
    ev = svc.evaluate(
        owner,
        staged.id,
        static_passed=eval_report["static_passed"],
        functional_passed=eval_report["functional_passed"],
        report=eval_report,
    )

    # Attempting to promote failed evaluation must fail with Conflict
    with pytest.raises(Conflict):
        svc.promote(owner, staged.id, ev.id)

    # Staged skill remains in staged state, never active
    assert staged.state == "staged"
