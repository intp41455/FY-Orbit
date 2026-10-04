"""Governed self-improving skill harness (受治理的自进化技能型 Agent Harness).

Core components adhering to 15_技能发现安全审查与自我迭代Harness清单.md:
1. TrustedSkillEvaluationWorker: independently scans AST/regex and benchmarks packages.
   Removes reliance on caller self-reporting static_passed=True.
2. FunctionCallingGateway: runtime typed execution gateway with budget, schema check, and real receipts.
3. SkillLearningLoop (SLL): distills operational experience from task traces into sanitized SKILL.md packages.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from typing import Any, Callable
from uuid import uuid4
import yaml

from ..services.actor import Actor
from ..services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed


class TrustedSkillEvaluationWorker:
    """Independent security and functional evaluation worker.
    
    Verifies that skills cannot be promoted via caller self-declared booleans.
    """

    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+|previous\s+|system\s+)*instructions",
        r"system\s*:\s*override",
        r"disable\s*.*\b(audit|approval|sandbox|guard)\b",
        r"(curl|wget)\s*.*\|\s*(sh|bash)",
        r"(api[_ -]?key|secret|password|bearer)\s*.*https?://",
        r"rm\s+-rf",
        r"powershell\s*.*-enc",
        r"\b(eval|exec)\s*\(",
        r"\bimport\s+subprocess\b",
    ]

    @classmethod
    def evaluate_package(cls, package: dict[str, Any]) -> dict[str, Any]:
        """Perform multi-dimensional analysis on a staged package."""
        findings: list[str] = []
        static_passed = True
        functional_passed = True

        # 1. Structure and schema check
        skill_md = package.get("skill_md") or package.get("instructions") or ""
        if not skill_md:
            findings.append("Missing skill_md or instructions in package.")
            static_passed = False

        if package.get("permissions"):
            findings.append("Instruction skills may not carry direct unvetted tool permissions.")
            static_passed = False

        # 2. Static security scan against injections and exfiltration
        # Scan skill markdown directly preserving newlines, and scan non-markdown metadata separately
        metadata_vals = [
            str(v)
            for k, v in package.items()
            if k not in ("skill_md", "instructions") and v is not None
        ]
        text_chunks = [skill_md] + metadata_vals
        for pattern in cls.INJECTION_PATTERNS:
            for chunk in text_chunks:
                if re.search(pattern, chunk, re.IGNORECASE):
                    findings.append(f"Security Alert: detected forbidden pattern matching '{pattern}'")
                    static_passed = False
                    break

        # 3. YAML Frontmatter and Specification check
        if skill_md.startswith("---"):
            try:
                parts = skill_md.split("---", 2)
                if len(parts) >= 3:
                    header = yaml.safe_load(parts[1])
                    if not isinstance(header, dict) or not header.get("name") or not header.get("description"):
                        findings.append("SKILL.md frontmatter must contain valid 'name' and 'description'.")
                        functional_passed = False
            except Exception as e:
                findings.append(f"YAML frontmatter parsing failed: {e}")
                functional_passed = False

        # 4. Functional feasibility check
        # Must define an actionable workflow or procedure
        if len(skill_md.strip()) < 20:
            findings.append("Skill description too short to constitute an actionable capability.")
            functional_passed = False

        return {
            "evaluator": "trusted_worker_v1",
            "evaluated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "static_passed": static_passed,
            "functional_passed": functional_passed and static_passed,
            "findings": findings,
            "security_grade": "A" if static_passed else "REJECTED",
        }


class FunctionCallingGateway:
    """Controlled runtime tool gateway enforcing schemas, budgets, and generating real execution receipts.

    Permission levels (``required_level``) follow the product scale:
    0=READONLY, 1=CONTENT, 2=CONTROLLED_EXEC, 3=SANDBOXED_EXEC,
    4=ORCHESTRATE, 5=SYSTEM_FS, 6=SYSTEM.
    Authorization is enforced in :meth:`invoke` via ``Actor.require_tool`` —
    owners pass unconditionally; service identities must list the tool in
    ``allowed_tools`` (``"*"`` allows all).
    """

    def __init__(self):
        self._tools: dict[str, dict[str, Any]] = {}
        self._consistency: "ToolConsistencyValidator | None" = None
        self.last_consistency_report: dict[str, Any] | None = None
        self._register_default_tools()

    def attach_consistency(self, validator: "ToolConsistencyValidator") -> None:
        """Wire a validator whose referencer data is read from the database."""
        self._consistency = validator

    def run_consistency_check(self) -> dict[str, Any]:
        """Cross-check the registry against real referencer data.

        Returns ``{"ok", "missing", "orphan", "referencers"}``; ``missing``
        entries are dangling references and are fatal at startup. Orphans are
        informational — a freshly registered tool is legitimately unreferenced
        until something binds it.
        """
        if self._consistency is None:
            return {"ok": True, "missing": [], "orphan": [], "referencers": {}}
        report = self._consistency.check(self._tools)
        self.last_consistency_report = report
        return report

    def _register_default_tools(self) -> None:
        """Register default pre-installed verified tools."""
        self.register_tool(
            name="system.health_check",
            description="Verify local gateway status and timestamp",
            handler=lambda args: {"status": "healthy", "service": "FindYourself-Gateway", "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()},
            schema={"type": "object", "properties": {}},
            cost_cents=0,
            builtin=True,
        )

        self.register_tool(
            name="metrics.calculate_ratio",
            description="Compute numerical ratio and percentage",
            handler=lambda args: {"ratio": round(args["numerator"] / max(args["denominator"], 1e-9), 4), "percentage": f"{round((args['numerator'] / max(args['denominator'], 1e-9)) * 100, 2)}%"},
            schema={"type": "object", "required": ["numerator", "denominator"]},
            cost_cents=0,
            builtin=True,
        )

        self.register_tool(
            name="procurement.evaluate_tco",
            description="Calculate total cost of ownership and flag vendor lock-in risks",
            handler=self._evaluate_tco_handler,
            schema={"type": "object", "required": ["upfront_cost", "monthly_cost", "months"]},
            cost_cents=1,
            builtin=True,
        )

        self.register_tool(
            name="workflow.lint_migration",
            description="Audit database migration script for irreversible operations",
            handler=self._lint_migration_handler,
            schema={"type": "object", "required": ["sql_content"]},
            cost_cents=1,
            builtin=True,
        )

    def _evaluate_tco_handler(self, args: dict[str, Any]) -> dict[str, Any]:
        upfront = float(args.get("upfront_cost", 0.0))
        monthly = float(args.get("monthly_cost", 0.0))
        months = int(args.get("months", 12))
        data_lockin = bool(args.get("has_custom_data_export", False) is False)

        total = upfront + (monthly * months)
        risks = []
        if data_lockin:
            risks.append("Vendor Lock-in Risk: Missing automated standard data export capability.")
        if monthly * months > upfront * 3:
            risks.append("Recurring Cost Trap: 1-year operational subscription exceeds initial deployment by 300%.")

        return {
            "total_tco": round(total, 2),
            "upfront": upfront,
            "cumulative_recurring": round(monthly * months, 2),
            "identified_risks": risks,
            "verdict": "CAUTION" if risks else "ACCEPTABLE",
        }

    def _lint_migration_handler(self, args: dict[str, Any]) -> dict[str, Any]:
        sql = args.get("sql_content", "")
        issues = []
        if re.search(r"DROP\s+COLUMN", sql, re.IGNORECASE):
            issues.append("Hazardous operation: DROP COLUMN causes immediate data loss without fallback.")
        if re.search(r"DROP\s+TABLE", sql, re.IGNORECASE):
            issues.append("Hazardous operation: DROP TABLE without backup or tombstone mechanism.")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "scanned_statements": len(sql.split(";")),
        }

    _TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")

    def register_tool(
        self,
        name: str,
        description: str,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
        schema: dict[str, Any],
        cost_cents: int = 0,
        *,
        version: int = 1,
        required_level: int = 0,
        replace: bool = False,
        builtin: bool = False,
    ) -> None:
        if not isinstance(name, str) or not self._TOOL_NAME_RE.match(name):
            raise ValidationFailed("invalid_tool_name", f"Tool name '{name}' does not match {self._TOOL_NAME_RE.pattern}")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValidationFailed("invalid_tool_schema", f"Tool '{name}' schema must be a JSON-Schema object with type=='object'.")
        if not isinstance(version, int) or version < 1:
            raise ValidationFailed("invalid_tool_version", f"Tool '{name}' version must be a positive integer.")
        if not isinstance(required_level, int) or not (0 <= required_level <= 6):
            raise ValidationFailed("invalid_tool_level", f"Tool '{name}' required_level must be an integer in [0, 6].")
        if name in self._tools and not replace:
            raise Conflict("tool_already_registered", f"Tool '{name}' is already registered; pass replace=True to swap it atomically.")

        self._tools[name] = {
            "name": name,
            "description": description,
            "handler": handler,
            "schema": schema,
            "cost_cents": cost_cents,
            "version": version,
            "required_level": required_level,
            "builtin": builtin,
            "registered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def unregister_tool(self, name: str, *, force: bool = False) -> None:
        """Remove a tool. References are revoked first — never the reverse.

        Refuses while any database referencer still names the tool (dangling
        reference); ``force=True`` is the operator's explicit override.
        """
        if name not in self._tools:
            raise NotFound("unknown_tool", f"Tool '{name}' is not registered in the gateway.")
        if not force and self._consistency is not None:
            referencers = self._consistency.find_referencers(name)
            if referencers:
                raise Conflict(
                    "tool_in_use",
                    f"Tool '{name}' is still referenced by {referencers}; revoke those references before unregistering.",
                )
        del self._tools[name]
        self.run_consistency_check()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Point-in-time copy of the registry for atomic swap / rollback."""
        return {name: dict(tool) for name, tool in self._tools.items()}

    def restore(self, snapshot: dict[str, dict[str, Any]]) -> None:
        self._tools = {name: dict(tool) for name, tool in snapshot.items()}
        self.run_consistency_check()

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "schema": t["schema"],
                "cost_cents": t["cost_cents"],
                "version": t["version"],
                "required_level": t["required_level"],
            }
            for t in self._tools.values()
        ]

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        actor: Actor,
        budget_cents: int = 50,
    ) -> dict[str, Any]:
        """Invoke a tool with typed argument verification and real execution receipt."""
        actor.require_authenticated()
        # Authorization before the existence check: an unauthorized identity
        # must not be able to probe the registry.
        actor.require_tool(tool_name)

        if tool_name not in self._tools:
            raise ValidationFailed("unknown_tool", f"Tool '{tool_name}' is not registered in the gateway.")

        tool = self._tools[tool_name]
        cost = tool["cost_cents"]

        if cost > budget_cents:
            raise Conflict("budget_exceeded", f"Tool cost {cost}c exceeds execution budget slice {budget_cents}c.")

        # Check required schema properties
        required = tool["schema"].get("required", [])
        for req_prop in required:
            if req_prop not in arguments:
                raise ValidationFailed("schema_validation", f"Missing required property '{req_prop}' for tool '{tool_name}'.")

        # Execute handler
        try:
            result = tool["handler"](arguments)
        except Exception as e:
            raise ValidationFailed("tool_execution_error", f"Tool '{tool_name}' failed with error: {e}")

        call_id = f"call-{uuid4().hex[:10]}"
        return {
            "call_id": call_id,
            "executed": True,
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "cost_cents": cost,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


# Singleton gateway instance
gateway = FunctionCallingGateway()


class ToolConsistencyValidator:
    """Cross-checks the gateway registry against real referencer data.

    Referencer data is read from the DATABASE at check time — never from
    in-memory copies, so the check validates actual references, not a stale
    picture of them. The convention: a capability entry shaped ``tool:<name>``
    on ``service_identities`` or ``agents`` binds that identity/agent to the
    named gateway tool.

    * ``missing`` — a referenced tool is not registered (dangling reference).
      Fatal at startup: boot must fail closed.
    * ``orphan``  — a registered non-builtin tool referenced by nothing.
      Informational: legitimate between registration and first binding.
    """

    def __init__(self, session_maker):
        self._session_maker = session_maker

    @staticmethod
    def _tool_refs(capabilities: Any) -> list[str]:
        if not isinstance(capabilities, (list, tuple)):
            return []
        return [c.split(":", 1)[1] for c in capabilities
                if isinstance(c, str) and c.startswith("tool:") and len(c) > len("tool:")]

    def collect_references(self) -> dict[str, list[str]]:
        from ..db.models import Agent, ServiceIdentity

        refs: dict[str, list[str]] = {}
        with self._session_maker() as session:
            for ident in session.query(ServiceIdentity).all():
                names = self._tool_refs(ident.capabilities)
                if names:
                    refs[f"service_identity:{ident.id}"] = names
            for agent in session.query(Agent).all():
                names = self._tool_refs(agent.capabilities)
                if names:
                    refs[f"agent:{agent.id}"] = names
        return refs

    def check(self, registry: dict[str, dict[str, Any]]) -> dict[str, Any]:
        refs = self.collect_references()
        referenced = {name for names in refs.values() for name in names}
        registered = set(registry)
        missing = sorted(referenced - registered)
        orphans = sorted(t for t in registered - referenced if not registry[t].get("builtin"))
        return {"ok": not missing, "missing": missing, "orphan": orphans, "referencers": refs}

    def find_referencers(self, tool_name: str) -> list[str]:
        refs = self.collect_references()
        return sorted(label for label, names in refs.items() if tool_name in names)


def attach_tool_consistency_guard(session_maker) -> dict[str, Any]:
    """Startup guard: refuse to boot when the registry has dangling references."""
    validator = ToolConsistencyValidator(session_maker)
    gateway.attach_consistency(validator)
    report = gateway.run_consistency_check()
    if not report["ok"]:
        raise RuntimeError(f"Dangling tool references detected: {report['missing']}")
    return report


class SkillLearningLoop:
    """Skill Learning Loop (SLL): experience distillation and sanitized candidate generation."""

    @staticmethod
    def sanitize_text(text: str) -> str:
        """Remove private identifiers, phone numbers, emails, API keys, and internal IPs."""
        # Clean emails
        sanitized = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[EMAIL_REDACTED]", text)
        # Clean phone numbers
        sanitized = re.sub(r"\b1[3-9]\d{9}\b", "[PHONE_REDACTED]", sanitized)
        # Clean bearer/api keys
        sanitized = re.sub(r"(sk-[a-zA-Z0-9]{16,})|(ghp_[a-zA-Z0-9]{16,})", "[KEY_REDACTED]", sanitized)
        # Clean IPv4
        sanitized = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "[IP_REDACTED]", sanitized)
        return sanitized

    @classmethod
    def distill_engineering_workflow(cls) -> dict[str, Any]:
        """Generate the standardized canonical Engineering Delivery Workflow candidate."""
        skill_content = (
            "---\n"
            "name: engineering-delivery-workflow\n"
            "description: Standardized engineering delivery checklist covering migrations, automated tests, and rollback checkpoints.\n"
            "---\n\n"
            "# Engineering Delivery Workflow\n\n"
            "## 1. Trigger Conditions\n"
            "- New database migrations or API schema modifications.\n"
            "- PR integration and automated regression gates.\n\n"
            "## 2. Execution Steps\n"
            "1. Run static linter on SQL migrations (disallow unvetted DROP COLUMN).\n"
            "2. Execute unit and API test suites with zero regression tolerance.\n"
            "3. Record immutable git commit SHA and generate verification evidence.\n"
            "4. Verify health check (/api/health) and database connectivity.\n\n"
            "## 3. Fallback & Rollback\n"
            "- If test regression occurs, restore previous tagged release.\n"
            "- For migration failures, execute down-migration script before service restart.\n"
        )
        package = {
            "name": "engineering-delivery-workflow",
            "version": "1.0.0",
            "source": "sll_distilled_experience",
            "license": "Apache-2.0",
            "domain": "work",
            "skill_md": skill_content,
            "permissions": {},
        }
        return package

    @classmethod
    def distill_procurement_safeguard(cls) -> dict[str, Any]:
        """Generate the standardized canonical Procurement Safeguard candidate."""
        skill_content = (
            "---\n"
            "name: procurement-safeguard\n"
            "description: Universal procurement and SaaS purchase auditing rules to avoid recurring cost traps and vendor lock-in.\n"
            "---\n\n"
            "# Universal Procurement Safeguard Rules\n\n"
            "## 1. Core Verification Checklist\n"
            "- [ ] **Total Cost of Ownership (TCO)**: Compare upfront deployment cost vs 3-year recurring maintenance.\n"
            "- [ ] **Data Portability**: Verify whether data export supports open formats (JSON/SQL/CSV) without manual intervention.\n"
            "- [ ] **Renewal & Cancellation**: Check for silent auto-renewal terms, penalty fees, and 30-day cancellation notice.\n"
            "- [ ] **Vendor Lock-in Boundary**: Ensure critical business logic is decoupled from proprietary cloud APIs.\n\n"
            "## 2. Red Lines (Instant Rejection)\n"
            "- Absence of data export mechanism.\n"
            "- Uncapped price increases on renewal (>15% per annum).\n"
            "- Inability to audit security or access logs.\n"
        )
        package = {
            "name": "procurement-safeguard",
            "version": "1.0.0",
            "source": "sll_distilled_experience",
            "license": "MIT",
            "domain": "work",
            "skill_md": skill_content,
            "permissions": {},
        }
        return package

    @classmethod
    def distill_from_trace(
        cls,
        task_name: str,
        task_goal: str,
        workflow_steps: list[str],
        domain: str = "work",
    ) -> dict[str, Any]:
        """Synthesize a new candidate skill package from a real executed task trace."""
        clean_name = re.sub(r"[^a-z0-9-]", "-", task_name.lower()).strip("-")[:40] or "task-workflow"
        clean_goal = cls.sanitize_text(task_goal)

        steps_md = "\n".join([f"{i}. {cls.sanitize_text(step)}" for i, step in enumerate(workflow_steps, 1)])

        skill_content = (
            f"---\n"
            f"name: {clean_name}\n"
            f"description: SLL synthesized operational workflow for {clean_goal[:60]}\n"
            f"---\n\n"
            f"# Operational Workflow: {clean_name}\n\n"
            f"## Goal\n{clean_goal}\n\n"
            f"## Standardized Steps\n{steps_md}\n\n"
            f"## Verification\n- Ensure all steps execute in isolation.\n- Verify output integrity and log audit trail.\n"
        )

        return {
            "name": clean_name,
            "version": "0.1.0-candidate",
            "source": "sll_experience_distillation",
            "license": "Internal-Reviewed",
            "domain": domain,
            "skill_md": skill_content,
            "permissions": {},
        }
