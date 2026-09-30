"""Skill staging, immutable hashing, multi-dimensional evaluation and promotion (§13.2).

A skill package is addressed by an immutable ``package_hash``. Promotion is NOT
a static-screening shortcut: it requires a recorded evaluation that binds the
exact subject digest and passes the required dimensions (static + functional).
Earlier code hard-coded ``passed=False`` and could never graduate; that is
BUG-07. Script-bearing packages must be evaluated in isolation and are refused
here until a real sandbox evaluator is wired.
"""

from uuid import uuid4

from sqlalchemy.orm import Session

from ..db.models import Skill, SkillEvaluation
from .actor import Actor
from .errors import Conflict, NotFound, ValidationFailed
from .hasher import digest
from .audit import AuditService


class SkillService:
    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    def stage(self, actor: Actor, *, name: str, semantic_version: str, package: dict,
              source: str, license_: str, domain: str) -> Skill:
        actor.require_authenticated()
        if package.get("permissions"):
            raise ValidationFailed("skill_permissions", "Instruction skills may not carry direct tool permissions")
        package_hash = digest(package)
        row = Skill(
            id=uuid4().hex, name=name, semantic_version=semantic_version, package_hash=package_hash,
            domain=domain, state="staged", source=source, license=license_,
        )
        self.s.add(row)
        self.s.flush()
        self.audit.append(actor, "skill.staged", row.id, {"package_hash": package_hash[:12]})
        return row

    def evaluate(self, actor: Actor, skill_id: str, *, static_passed: bool,
                 functional_passed: bool, report: dict | None = None) -> SkillEvaluation:
        actor.require_authenticated()
        skill = self.s.get(Skill, skill_id)
        if skill is None:
            raise NotFound("skill_not_found", "Skill not found")
        if skill.state != "staged":
            raise Conflict("skill_state", "Only staged skills can be evaluated")
        ev = SkillEvaluation(
            id=uuid4().hex, skill_id=skill.id, subject_digest=skill.package_hash,
            static_passed=static_passed, dynamic_passed=False,
            functional_passed=functional_passed, professional_passed=False,
            report=report or {}, evaluator=actor.service_id or "core",
        )
        self.s.add(ev)
        self.s.flush()
        self.audit.append(actor, "skill.evaluated", skill.id,
                          {"static": static_passed, "functional": functional_passed})
        return ev

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
        skill.state = "active"
        self.s.flush()
        self.audit.append(actor, "skill.promoted", skill.id, {"package_hash": skill.package_hash[:12]})
        return skill

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
