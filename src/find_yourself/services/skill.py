"""Skill staging, immutable hashing, multi-dimensional evaluation and promotion (§13.2).

A skill package is addressed by an immutable ``package_hash``. Promotion is NOT
a static-screening shortcut: it requires a recorded evaluation that binds the
exact subject digest and passes the required dimensions (static + functional).
Earlier code hard-coded ``passed=False`` and could never graduate; that is
BUG-07. Script-bearing packages must be evaluated in isolation and are refused
here until a real sandbox evaluator is wired.

插件生态上架门禁（需求 14）
--------------------------
``stage`` 现在**服务端**对每个包做两件事，结果落库、调用方无法伪造：

* **自动静态扫描**（:func:`~find_yourself.services.plugin_signing.scan_package`）
  —— 写 ``scan_report`` / ``scan_passed``；
* **包签名校验**（:func:`~find_yourself.services.plugin_signing.verify_package_signature`）
  —— 若调用方提供了签名，则必须用登记的公钥**真的验过**才落
  ``signature_verified=True``；验不过即拒（最早失效点）。

``promote`` 再加一道**服务端策略**门禁
（:data:`~find_yourself.services.plugin_signing.PROMOTION_GATE_POLICY`）：含可执行
条目的 ``plugin`` 包**必须**签名已校验 + 扫描已通过；纯指令包必须扫描已通过。
这道判定不接受任何「本次跳过」参数——调用方无法削弱它。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from ..db.models import Skill, SkillEvaluation
from .actor import Actor
from .audit import AuditService
from .errors import Conflict, NotFound, ValidationFailed
from .hasher import digest
from .plugin_signing import (
    DEFAULT_SIGNING_ALGORITHM,
    classify_package,
    evaluate_promotion_gate,
    lookup_signing_key,
    scan_package,
    verify_package_signature,
)


class SkillService:
    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    def stage(self, actor: Actor, *, name: str, semantic_version: str, package: dict,
              source: str, license_: str, domain: str,
              signature: str | None = None, signature_algorithm: str | None = None,
              signing_key_id: str | None = None) -> Skill:
        actor.require_authenticated()
        if package.get("permissions"):
            raise ValidationFailed("skill_permissions", "Instruction skills may not carry direct tool permissions")
        package_hash = digest(package)

        # 服务端分类 + 真实静态扫描。两者都由服务算，调用方无法传参覆盖。
        profile = classify_package(package)
        report = scan_package(package)

        # 服务端签名校验：调用方若声称已签名，必须用登记的公钥**真的验过**。
        sig_value: str | None = None
        sig_algorithm: str | None = None
        sig_key_id: str | None = None
        sig_verified = False
        if signature is not None:
            algorithm = signature_algorithm or DEFAULT_SIGNING_ALGORITHM
            if not signing_key_id:
                raise ValidationFailed(
                    "signing_key_required", "signature supplied without signing_key_id"
                )
            key = lookup_signing_key(self.s, signing_key_id)
            if key is None or key.state != "active":
                # 未知/已吊销 key：无法验证，直接拒（fail closed），不落一行假签名。
                raise ValidationFailed(
                    "unknown_signing_key", f"active signing key {signing_key_id!r} not found"
                )
            if key.algorithm != algorithm:
                raise ValidationFailed(
                    "algorithm_mismatch",
                    f"key {signing_key_id!r} is {key.algorithm!r}, signature claims {algorithm!r}",
                )
            sig_verified = verify_package_signature(
                package=package, signature=signature, public_key=key.public_key,
                algorithm=algorithm,
            )
            if not sig_verified:
                # 🔴 签名失败即拒——在最早点失效，绝不落一条坏签名。
                raise ValidationFailed(
                    "signature_invalid", "package signature did not verify against the registered key"
                )
            sig_value, sig_algorithm, sig_key_id = signature, algorithm, signing_key_id

        row = Skill(
            id=uuid4().hex, name=name, semantic_version=semantic_version, package_hash=package_hash,
            domain=domain, state="staged", source=source, license=license_,
            signature=sig_value, signature_algorithm=sig_algorithm, signing_key_id=sig_key_id,
            signature_verified=sig_verified, scan_report=report,
            scan_passed=bool(report["passed"]), gate_profile=profile,
        )
        self.s.add(row)
        self.s.flush()
        # 审计只落 key_id / 结论，绝不落签名值或任何密钥材料。
        self.audit.append(actor, "skill.staged", row.id, {
            "package_hash": package_hash[:12], "gate_profile": profile,
            "scan_passed": bool(report["passed"]), "scan_findings": report["finding_count"],
            "signature_verified": sig_verified, "signing_key_id": sig_key_id,
        })
        return row

    def evaluate(self, actor: Actor, skill_id: str, *, static_passed: bool,
                 functional_passed: bool, dynamic_passed: bool = False,
                 report: dict | None = None) -> SkillEvaluation:
        actor.require_authenticated()
        skill = self.s.get(Skill, skill_id)
        if skill is None:
            raise NotFound("skill_not_found", "Skill not found")
        if skill.state != "staged":
            raise Conflict("skill_state", "Only staged skills can be evaluated")
        ev = SkillEvaluation(
            id=uuid4().hex, skill_id=skill.id, subject_digest=skill.package_hash,
            static_passed=static_passed, dynamic_passed=dynamic_passed,
            functional_passed=functional_passed, professional_passed=False,
            report=report or {}, evaluator=actor.service_id or "core",
        )
        self.s.add(ev)
        self.s.flush()
        self.audit.append(actor, "skill.evaluated", skill.id,
                          {"static": static_passed, "functional": functional_passed, "dynamic": dynamic_passed})
        return ev

    def run_sandbox_evaluation(
        self,
        actor: Actor,
        skill_id: str,
        command: list[str],
        workspace: str | None = None,
        timeout_s: float = 10,
    ) -> dict[str, Any]:
        """Run dynamic execution of a plugin script in the container sandbox (Batch H).

        Uses ContainerSandboxRunner (docker/sandbox/Dockerfile image).
        Returns execution result dict with exit_code, stdout, stderr, and whether it passed.
        """
        actor.require_authenticated()
        skill = self.s.get(Skill, skill_id)
        if skill is None:
            raise NotFound("skill_not_found", "Skill not found")
        if skill.state != "staged":
            raise Conflict("skill_state", "Only staged skills can be evaluated in sandbox")

        try:
            from ..runtime.sandbox_container import ContainerSandboxRunner
            runner = ContainerSandboxRunner()
            isolation = runner.describe_isolation()
            result = runner.run(command, workspace=workspace, timeout_s=timeout_s)
            passed = (result.get("exit_code") == 0) and not result.get("timed_out")
            return {
                "sandbox_available": True,
                "isolation": isolation,
                "passed": passed,
                "result": result,
            }
        except Exception as exc:
            return {
                "sandbox_available": False,
                "passed": False,
                "error": str(exc),
            }

    def promote(self, actor: Actor, skill_id: str, evaluation_id: str) -> Skill:
        # Only the owner may promote; an agent cannot enable itself (§5.3).
        actor.require_owner()
        skill = self.s.get(Skill, skill_id)
        if skill is None:
            raise NotFound("skill_not_found", "Skill not found")
        if skill.state != "staged":
            raise Conflict("skill_state", "Only staged skills can be promoted")
        ev = self.s.get(SkillEvaluation, evaluation_id)
        if ev is None or ev.skill_id != skill.id:
            raise Conflict("missing_evaluation", "Evaluation must bind this exact skill")
        # The evaluation must cover the immutable package version (BUG-07).
        if ev.subject_digest != skill.package_hash:
            raise Conflict("evaluation_drift", "Evaluation does not match this skill version")
        if not (ev.static_passed and ev.functional_passed):
            raise Conflict("failed_evaluation", "Skill evaluation did not pass required dimensions")
        # 🔴 服务端上架门禁（需求14）：策略来自 PROMOTION_GATE_POLICY，**不是**调用方
        # 参数。plugin 包必须签名已校验 + 扫描已通过；instruction 包必须扫描已通过。
        decision = evaluate_promotion_gate(
            profile=skill.gate_profile,
            scan_passed=bool(skill.scan_passed),
            signature_verified=bool(skill.signature_verified),
        )
        if not decision["allowed"]:
            raise Conflict(
                "plugin_gate_blocked",
                "promotion blocked by server-side gate: " + ", ".join(decision["reasons"]),
            )
        skill.state = "active"
        self.s.flush()
        self.audit.append(actor, "skill.promoted", skill.id, {
            "package_hash": skill.package_hash[:12], "gate_profile": decision["profile"],
            "scan_passed": bool(skill.scan_passed),
            "signature_verified": bool(skill.signature_verified),
        })
        return skill

    def promotion_gate_status(self, skill_id: str) -> dict:
        """只读地报告某技能的当前门禁判定（供 API/测试解释「为什么被拒」）。"""
        skill = self.s.get(Skill, skill_id)
        if skill is None:
            raise NotFound("skill_not_found", "Skill not found")
        return evaluate_promotion_gate(
            profile=skill.gate_profile,
            scan_passed=bool(skill.scan_passed),
            signature_verified=bool(skill.signature_verified),
        )

    def disable(self, actor: Actor, skill_id: str) -> Skill:
        actor.require_owner()
        skill = self.s.get(Skill, skill_id)
        if skill is None:
            raise NotFound("skill_not_found", "Skill not found")
        skill.state = "disabled"
        self.s.flush()
        self.audit.append(actor, "skill.disabled", skill.id)
        return skill

    def rollback(self, actor: Actor, current_skill_id: str, target_skill_id: str) -> tuple[Skill, Skill]:
        """Roll back a regressed skill by disabling current and re-activating target."""
        actor.require_owner()
        curr = self.s.get(Skill, current_skill_id)
        if curr is None:
            raise NotFound("skill_not_found", f"Current skill {current_skill_id} not found")
        target = self.s.get(Skill, target_skill_id)
        if target is None:
            raise NotFound("skill_not_found", f"Target rollback skill {target_skill_id} not found")
        if curr.name != target.name:
            raise Conflict("skill_name_mismatch", "Can only roll back between versions of the same skill")

        curr.state = "disabled"
        target.state = "active"
        self.s.flush()
        self.audit.append(
            actor, "skill.rolled_back", curr.id,
            {"from_version": curr.semantic_version, "to_version": target.semantic_version}
        )
        return curr, target

    def can_invoke(self, skill_id: str) -> bool:
        """Only promoted 'active' skills can be invoked. Staged or disabled skills are rejected."""
        skill = self.s.get(Skill, skill_id)
        if skill is None:
            return False
        return skill.state == "active"
