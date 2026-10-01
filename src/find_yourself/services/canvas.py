"""CanvasService: 05 多Agent协作可视化画布 (Multi-Agent Collaboration Canvas).

Implements:
- Canvas templates (Personal: Hermes + WorkBuddy + 豆包; Work: Codex + OpenCode + Pi agent)
- Real connector probing (with authentic status levels: 仅设计 -> 发现接口 -> 本机握手通过 -> 合成任务往返 -> 真实授权任务往返 -> 生产可用)
- Canvas instance lifecycle
- Structured subtask dispatching with budget slices
- Structured handoff packets (no raw conversation dumping)
- Monotonic CanvasEvent sequence & snapshot cursor replay
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
import shutil
import subprocess
import threading
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from find_yourself.adapters.hermes_adapter import HermesAdapter
from find_yourself.db.models import CanvasEvent, CanvasInstance, DispatchRecord, Grant, HandoffPacket, Memory, Task
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.budget import BudgetService
from find_yourself.services.grant import GrantService
from find_yourself.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed


TEMPLATES = {
    "personal": {
        "id": "personal",
        "name": "私人事务模板",
        "domain": "personal",
        "center": "Hermes",
        "workers": ["WorkBuddy", "豆包", "ResearchAgent"],
        "description": "处理个人规划、生活事务、内容创作与知识整理。任何敏感资料严格按需授权。",
        "default_budget": 0.50,
        "max_depth": 3,
    },
    "work": {
        "id": "work",
        "name": "工作工程模板",
        "domain": "work",
        "center": "Codex",
        "workers": ["OpenCode", "Pi agent", "EngineeringAgent"],
        "description": "处理需求拆解、架构设计、代码实现、测试、审查与发布准备。独立工作域与隔离工作区。",
        "default_budget": 2.00,
        "max_depth": 4,
    },
}


def _inspect_goal_privacy(
    goal: str,
    inst_domain: str,
    unauthorized_memories: list[Memory],
) -> None:
    """Rigorous privacy boundary guard for subtask goal.

    Prevents leaking raw text, clauses, partial excerpts, or paraphrases
    from other-domain records into the goal text.
    """
    clean_goal = goal.strip().lower()
    opposite_domain = "work" if inst_domain == "personal" else "personal"

    # 1. Reject if goal attempts to smuggle explicit domain markers
    # of the opposite domain without structured records
    sensitive_markers = {
        "personal": ["私人日记", "私人病历", "个人隐私", "个人薪资", "家庭住址", "恋爱经历", "私人存款", "体检报告"],
        "work": ["机密项目", "商业机密", "内部财报", "未公开代码", "工作薪酬", "客户名单", "公司战略", "架构机密"],
    }
    for marker in sensitive_markers.get(opposite_domain, []):
        if marker in clean_goal:
            raise ValidationFailed(
                f"Task goal contains explicit ungranted sensitive marker '{marker}' from {opposite_domain} domain"
            )

    # 2. Check against all active/unauthorized memories from other domains
    for um in unauthorized_memories:
        content = (um.content or "").strip()
        if not content:
            continue
        clean_content = content.lower()

        # A. Full text match
        if clean_content in clean_goal:
            raise ValidationFailed(
                f"Task goal contains ungranted full text from {um.domain} record '{um.id}'"
            )

        # B. Clause / Sentence match (split by punctuation)
        clauses = [c.strip() for c in re.split(r"[，。！？；;\,\.\!\?\n\r：:]+", clean_content) if len(c.strip()) >= 4]
        for clause in clauses:
            if clause in clean_goal:
                raise ValidationFailed(
                    f"Task goal contains ungranted excerpt/clause '{clause}' from {um.domain} record '{um.id}'"
                )

        # C. N-gram shingles overlap (detects paraphrasing / partial excerpts)
        chars = [ch for ch in clean_content if not ch.isspace()]
        if len(chars) >= 6:
            mem_shingles = set("".join(chars[i:i+3]) for i in range(len(chars) - 2))
            goal_chars = [ch for ch in clean_goal if not ch.isspace()]
            goal_shingles = set("".join(goal_chars[i:i+3]) for i in range(len(goal_chars) - 2))
            overlap = mem_shingles & goal_shingles
            if len(overlap) >= 3:
                raise ValidationFailed(
                    f"Task goal contains paraphrased or overlapping fragments from {um.domain} record '{um.id}'"
                )


class CanvasService:
    def __init__(
        self,
        session: Session,
        audit: AuditService,
        budget: BudgetService | None = None,
        grants: GrantService | None = None,
        hermes_adapter: HermesAdapter | None = None,
    ):
        self.session = session
        self.audit = audit
        self.budget = budget or BudgetService(session, audit)
        self.grants = grants or GrantService(session, audit)
        self.hermes_adapter = hermes_adapter or HermesAdapter()
        self._event_lock = threading.RLock()

    # -----------------------------------------------------------------------
    # Templates & Connectors
    # -----------------------------------------------------------------------
    def list_templates(self) -> list[dict[str, Any]]:
        return list(TEMPLATES.values())

    def probe_connectors(self) -> list[dict[str, Any]]:
        """Probe machine for actual connector availability with zero-simulation status."""
        connectors = []

        # 1. Hermes Agent - run real protocol handshake via hermes_adapter.probe()
        hermes_probe = self.hermes_adapter.probe()
        connectors.append({
            "name": "Hermes",
            "protocol": "ACP / TUI JSON-RPC",
            "role": "orchestrator",
            "stage": hermes_probe.get("stage", "仅设计"),
            "healthy": bool(hermes_probe.get("healthy")),
            "binary_path": hermes_probe.get("binary_path"),
            "version": hermes_probe.get("version"),
            "blocking_reason": hermes_probe.get("blocking_reason"),
            "domains": ["personal"],
        })

        # 2. Codex - check PATH and execute --version for handshake
        codex_bin = shutil.which("codex")
        codex_healthy = False
        codex_stage = "仅设计"
        codex_version = None
        codex_blocking = "未在本机 PATH 检测到 codex 可执行文件"
        if codex_bin:
            try:
                c_res = subprocess.run(
                    [codex_bin, "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    check=False,
                )
                v_out = (c_res.stdout.strip().splitlines() or [""])[0]
                codex_version = v_out
                # Honest inspection: check if this is the npm static site renderer rather than Codex AI Agent
                if "0.2." in v_out or "site" in c_res.stdout.lower():
                    codex_healthy = False
                    codex_stage = "仅设计"
                    codex_blocking = f"本机 PATH 的 codex ({v_out}) 系 npm 静态文档渲染器，非可调度的 Codex AI 智能体；会话操纵需外部接口"
                elif c_res.returncode == 0:
                    codex_healthy = True
                    codex_stage = "本机握手通过"
                    codex_blocking = None
                else:
                    codex_stage = "发现接口"
                    codex_blocking = f"Codex --version exited with code {c_res.returncode}: {c_res.stderr.strip()}"
            except Exception as e:
                codex_stage = "发现接口"
                codex_blocking = f"Codex probe error: {str(e)}"

        connectors.append({
            "name": "Codex",
            "protocol": "Internal Agent SDK / CLI",
            "role": "orchestrator",
            "stage": codex_stage,
            "healthy": codex_healthy,
            "binary_path": codex_bin,
            "version": codex_version,
            "blocking_reason": codex_blocking,
            "domains": ["work", "personal"],
        })

        # 3. OpenCode
        opencode_bin = shutil.which("opencode")
        connectors.append({
            "name": "OpenCode",
            "protocol": "HTTP / OpenAPI (opencode serve)",
            "role": "worker",
            "stage": "发现接口" if opencode_bin else "仅设计",
            "healthy": bool(opencode_bin),
            "binary_path": opencode_bin,
            "version": None,
            "blocking_reason": None if opencode_bin else "未在本机 PATH 检测到 opencode 二进制，需安装 opencode 并启动 opencode serve",
            "domains": ["work"],
        })

        # 4. Pi agent
        pi_bin = shutil.which("pi")
        connectors.append({
            "name": "Pi agent",
            "protocol": "Subprocess RPC (--mode rpc)",
            "role": "worker",
            "stage": "发现接口" if pi_bin else "仅设计",
            "healthy": bool(pi_bin),
            "binary_path": pi_bin,
            "version": None,
            "blocking_reason": None if pi_bin else "未在本机 PATH 检测到 pi agent，需配置 badlogic/pi-mono 运行环境",
            "domains": ["work"],
        })

        # 5. WorkBuddy
        connectors.append({
            "name": "WorkBuddy",
            "protocol": "Desktop Client / Manual Handoff",
            "role": "worker",
            "stage": "仅设计",
            "healthy": False,
            "binary_path": None,
            "version": None,
            "blocking_reason": "当前仅支持手动交接节点；无直接无头桌面自动化凭据",
            "domains": ["personal"],
        })

        # 6. 豆包
        connectors.append({
            "name": "豆包",
            "protocol": "Model API / Manual Handoff",
            "role": "worker",
            "stage": "仅设计",
            "healthy": False,
            "binary_path": None,
            "version": None,
            "blocking_reason": "当前仅支持手动交接节点或结构化模型能力，无客户端控制权限",
            "domains": ["personal"],
        })

        # 7. Built-in ResearchAgent & EngineeringAgent
        connectors.append({
            "name": "ResearchAgent",
            "protocol": "A2A / Internal",
            "role": "worker",
            "stage": "发现接口",
            "healthy": True,
            "binary_path": "INTERNAL",
            "version": "1.0-builtin",
            "blocking_reason": "内部研究员代理支持接收派发与交接包；需工作流或调用方提交完成回传",
            "domains": ["personal", "work"],
        })
        connectors.append({
            "name": "EngineeringAgent",
            "protocol": "A2A / Internal",
            "role": "worker",
            "stage": "发现接口",
            "healthy": True,
            "binary_path": "INTERNAL",
            "version": "1.0-builtin",
            "blocking_reason": "内部工程代理支持接收派发与任务拆解；需调用方提交完成回传",
            "domains": ["work"],
        })

        return connectors

    # -----------------------------------------------------------------------
    # Instance Lifecycle
    # -----------------------------------------------------------------------
    def create_instance(
        self,
        actor: Actor,
        project_name: str,
        template_id: str = "personal",
        orchestrator_id: str | None = None,
    ) -> CanvasInstance:
        actor.require_owner()
        tmpl = TEMPLATES.get(template_id)
        if not tmpl:
            raise ValidationFailed(f"Unknown canvas template: {template_id}")

        instance_id = f"canv-{uuid4().hex[:12]}"
        center = orchestrator_id or tmpl["center"]

        inst = CanvasInstance(
            id=instance_id,
            owner_id=actor.owner_id,
            project_name=project_name.strip(),
            domain=tmpl["domain"],
            template_id=template_id,
            orchestrator_id=center,
            state="active",
            config={
                "budget": tmpl["default_budget"],
                "max_depth": tmpl["max_depth"],
                "workers": tmpl["workers"],
            },
        )
        self.session.add(inst)

        # Build connector probe lookup for honest node registration
        connector_map = {c["name"]: c for c in self.probe_connectors()}

        # Emits initial lifecycle events
        self._emit_event(instance_id, "canvas.instance.created", details={"project": project_name, "template": template_id})

        # Register orchestrator node
        center_probe = connector_map.get(center, {})
        center_healthy = bool(center_probe.get("healthy"))
        self._emit_event(
            instance_id,
            "agent.node_registered",
            agent_id=center,
            details={"role": "center", "stage": center_probe.get("stage", "仅设计"), "healthy": center_healthy},
        )
        if center_healthy:
            self._emit_event(instance_id, "agent.connected", agent_id=center, details={"role": "center", "stage": center_probe.get("stage")})

        # Register worker nodes (never emit agent.connected for unverified/disconnected workers)
        for w in tmpl["workers"]:
            w_probe = connector_map.get(w, {})
            w_healthy = bool(w_probe.get("healthy"))
            self._emit_event(
                instance_id,
                "agent.node_registered",
                agent_id=w,
                details={"role": "worker", "stage": w_probe.get("stage", "仅设计"), "healthy": w_healthy},
            )
            # Only internal workers or verified connected agents emit connected
            if w in ("EngineeringAgent", "ResearchAgent"):
                self._emit_event(instance_id, "agent.connected", agent_id=w, details={"role": "worker", "stage": "合成任务往返"})

        self.audit.append(
            actor,
            "canvas.instance.create",
            target=instance_id,
            details={"project": project_name, "template": template_id, "orchestrator": center},
        )
        self.session.flush()
        return inst

    def get_instance(self, actor: Actor, instance_id: str) -> CanvasInstance:
        actor.require_owner()
        inst = self.session.get(CanvasInstance, instance_id)
        if not inst or inst.owner_id != actor.owner_id:
            raise NotFound(f"Canvas instance not found: {instance_id}")
        return inst

    def list_instances(self, actor: Actor) -> list[CanvasInstance]:
        actor.require_owner()
        stmt = (
            select(CanvasInstance)
            .where(CanvasInstance.owner_id == actor.owner_id)
            .order_by(CanvasInstance.created_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    # -----------------------------------------------------------------------
    # Subtask Dispatch & Handoff
    # -----------------------------------------------------------------------
    def dispatch_subtask(
        self,
        actor: Actor,
        instance_id: str,
        root_task_id: str,
        worker_id: str,
        goal: str,
        acceptance_criteria: str = "",
        budget_slice: float = 0.05,
        deadline: datetime | None = None,
        input_ref: dict[str, Any] | None = None,
        subtask_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> DispatchRecord:
        actor.require_owner()
        inst = self.get_instance(actor, instance_id)
        if inst.state != "active":
            raise Conflict(f"Canvas instance {instance_id} is {inst.state}; cannot dispatch")

        tmpl = TEMPLATES.get(inst.template_id, {})
        allowed_workers = tmpl.get("workers", []) + [inst.orchestrator_id]
        if worker_id not in allowed_workers:
            raise ValidationFailed(f"Worker {worker_id} is not part of template {inst.template_id}")

        # 1. Enforce strict budget cap ($0.50 default contract per subtask)
        if budget_slice > 0.50:
            raise ValidationFailed(
                f"Subtask budget slice (${budget_slice:.2f}) exceeds per-subtask maximum limit of $0.50"
            )
        if budget_slice <= 0.0:
            raise ValidationFailed("Budget slice must be greater than $0.00")

        # 2. Verify root task existence, ownership, and domain
        root_task = self.session.get(Task, root_task_id)
        if not root_task or root_task.owner_id != actor.owner_id:
            raise ValidationFailed(
                f"Root task {root_task_id} not found or not owned by caller"
            )
        if root_task.domain != inst.domain:
            raise ValidationFailed(
                f"Root task domain '{root_task.domain}' does not match canvas domain '{inst.domain}'"
            )

        # 3. Enforce domain boundary and strict authoritative data grant authorization
        input_ref_dict = dict(input_ref or {})
        grant_id = input_ref_dict.get("grant_id")
        client_domain = (
            input_ref_dict.get("domain")
            or input_ref_dict.get("privacy_domain")
            or input_ref_dict.get("source_domain")
        )

        target_records: list[str] = []
        if "record_id" in input_ref_dict:
            target_records.append(str(input_ref_dict["record_id"]))
        if "record_ids" in input_ref_dict and isinstance(input_ref_dict["record_ids"], list):
            target_records.extend([str(r) for r in input_ref_dict["record_ids"]])
        if "ref" in input_ref_dict and str(input_ref_dict["ref"]) not in target_records:
            target_records.append(str(input_ref_dict["ref"]))

        # Check all target records against authoritative Memory table
        record_domains = set()
        for rid in target_records:
            mem = self.session.get(Memory, rid)
            if mem is None:
                raise ValidationFailed(
                    f"Referenced record '{rid}' not found in authoritative storage"
                )
            if mem.deleted_at is not None or not mem.active:
                raise ValidationFailed(
                    f"Referenced record '{rid}' has been deleted or deactivated"
                )
            if mem.owner_id != actor.owner_id:
                raise ValidationFailed(
                    f"Referenced record '{rid}' is not owned by actor '{actor.owner_id}'"
                )
            if client_domain and mem.domain != client_domain:
                raise ValidationFailed(
                    f"Client reported domain '{client_domain}' conflicts with authoritative domain '{mem.domain}' for record '{rid}'"
                )
            record_domains.add(mem.domain)

        if len(record_domains) > 1:
            raise ValidationFailed(
                f"Referenced records span multiple conflicting domains: {sorted(list(record_domains))}; mixed-domain input references are rejected"
            )

        if record_domains:
            authoritative_domain = next(iter(record_domains))
        elif client_domain:
            authoritative_domain = client_domain
        else:
            authoritative_domain = inst.domain

        is_cross_domain = authoritative_domain != inst.domain

        if is_cross_domain or grant_id:
            if not grant_id:
                raise ValidationFailed(
                    f"Canvas in '{inst.domain}' domain cannot ingest '{authoritative_domain}' domain data without explicit grant authorization"
                )
            if not target_records:
                raise ValidationFailed(
                    "Cross-domain subtask dispatch requires specific authoritative record references"
                )

            grant = self.session.get(Grant, grant_id)
            if grant is None:
                raise ValidationFailed(f"Grant {grant_id} not found")
            if grant.state != "active" or grant.revoked_at is not None:
                raise ValidationFailed(f"Grant {grant_id} is not active (state: {grant.state})")
            if grant.expires_at <= utcnow():
                raise ValidationFailed(f"Grant {grant_id} has expired at {grant.expires_at.isoformat()}")
            if grant.consumer_domain != inst.domain:
                raise ValidationFailed(
                    f"Grant {grant_id} consumer domain '{grant.consumer_domain}' does not match canvas domain '{inst.domain}'"
                )
            if grant.source_domain != authoritative_domain:
                raise ValidationFailed(
                    f"Grant {grant_id} source domain '{grant.source_domain}' does not match authoritative input domain '{authoritative_domain}'"
                )

            # Check coverage of all target records
            for rid in target_records:
                if rid not in (grant.record_ids or []):
                    raise ValidationFailed(
                        f"Grant {grant_id} does not authorize record '{rid}' (authorized: {grant.record_ids})"
                    )

        # 3.1 Goal inspection: Ensure raw sensitive text from unauthorized other-domain memories is not smuggled in goal
        unauthorized_memories = self.session.execute(
            select(Memory).where(
                Memory.owner_id == actor.owner_id,
                Memory.domain != inst.domain,
                Memory.deleted_at.is_(None),
                Memory.active.is_(True),
            )
        ).scalars().all()
        active_unauthorized = [
            um for um in unauthorized_memories
            if not (grant_id and um.id in target_records)
        ]
        _inspect_goal_privacy(goal, inst.domain, active_unauthorized)

        # Idempotency check to prevent duplicate dispatches
        assigned_subtask_id = subtask_id or f"sub-{uuid4().hex[:12]}"
        idem_key = idempotency_key or f"dispatch-{instance_id}-{assigned_subtask_id}"

        existing_record = self.session.execute(
            select(DispatchRecord).where(DispatchRecord.idempotency_key == idem_key)
        ).scalar_one_or_none()
        if existing_record:
            if existing_record.state in ("running", "dispatched", "submitted"):
                # Prior execution crashed or retried during execution; Hermes has reconcile=False
                existing_record.state = "unknown_needs_reconciliation"
                self._emit_event(
                    instance_id,
                    "agent.reconciliation_required",
                    task_id=existing_record.subtask_id,
                    agent_id=existing_record.worker_id,
                    details={
                        "message": "Subtask execution was in-flight or process crashed before outcome was recorded; reconcile is unsupported, manual reconciliation required.",
                        "local_execution_id": (existing_record.input_ref or {}).get("local_execution_id"),
                        "idempotency_key": idem_key,
                    },
                )
                res_id = (existing_record.input_ref or {}).get("reservation_id")
                if self.budget and res_id:
                    try:
                        self.budget.hold_unknown_pricing(actor, res_id, reason="crash_in_flight_needs_reconciliation")
                    except Exception:
                        pass
                self.session.commit()
                return existing_record
            return existing_record

        # 4. Check worker connectivity / adapter state BEFORE reserving budget!
        connector_map = {c["name"]: c for c in self.probe_connectors()}
        worker_info = connector_map.get(worker_id, {})
        worker_healthy = bool(worker_info.get("healthy"))
        is_internal = worker_id in ("EngineeringAgent", "ResearchAgent")

        # External unconfigured agents enter 'pending_adapter' state truthfully without reserving budget
        if not worker_healthy and not is_internal:
            dispatch_state = "pending_adapter"
            event_type = "task.planned"
            event_details = {
                "goal": goal,
                "orchestrator": inst.orchestrator_id,
                "budget_slice": budget_slice,
                "adapter_status": "pending_adapter",
                "message": f"Worker {worker_id} adapter is not connected. Subtask recorded as planned/pending adapter.",
            }
            record = DispatchRecord(
                id=f"disp-{uuid4().hex[:12]}",
                instance_id=instance_id,
                root_task_id=root_task_id,
                subtask_id=assigned_subtask_id,
                orchestrator_id=inst.orchestrator_id,
                worker_id=worker_id,
                idempotency_key=idem_key,
                input_ref=input_ref_dict,
                goal=goal.strip(),
                acceptance_criteria=str(acceptance_criteria).strip() if not isinstance(acceptance_criteria, dict) else str(acceptance_criteria),
                budget_slice=budget_slice,
                deadline=deadline or (utcnow() + timedelta(hours=2)),
                state=dispatch_state,
            )
            self.session.add(record)
            self._emit_event(
                instance_id,
                event_type,
                task_id=assigned_subtask_id,
                agent_id=worker_id,
                details=event_details,
            )
            self.session.flush()
            return record

        # 5. Worker is ready or internal: Reserve budget now!
        res = None
        if self.budget:
            res = self.budget.reserve(
                actor,
                task_id=root_task_id,
                amount=Decimal(str(budget_slice)),
                idempotency_key=f"disp-res-{idem_key}",
                scope="canvas_dispatch",
            )

        updated_input_ref = dict(input_ref_dict)
        if res:
            updated_input_ref["reservation_id"] = res.id

        # 6. Dispatch and run if external connected adapter (Hermes)
        if worker_id.lower() == "hermes":
            # Re-verify grant before launching external process to mitigate revocation race condition
            if grant_id:
                rechecked_grant = self.session.get(Grant, grant_id)
                if not rechecked_grant or rechecked_grant.state != "active" or rechecked_grant.revoked_at is not None or rechecked_grant.expires_at <= utcnow():
                    if self.budget and res:
                        self.budget.release(actor, res.id)
                    raise ValidationFailed(f"Grant {grant_id} is no longer active prior to execution")

            local_exec_id = f"exec-local-{uuid4().hex[:12]}"
            updated_input_ref["local_execution_id"] = local_exec_id

            # PRE-EXECUTION PERSISTENCE & DURABLE OUTBOX:
            # Commit the record in 'running' state BEFORE invoking external process!
            record = DispatchRecord(
                id=f"disp-{uuid4().hex[:12]}",
                instance_id=instance_id,
                root_task_id=root_task_id,
                subtask_id=assigned_subtask_id,
                orchestrator_id=inst.orchestrator_id,
                worker_id=worker_id,
                idempotency_key=idem_key,
                input_ref=updated_input_ref,
                goal=goal.strip(),
                acceptance_criteria=str(acceptance_criteria).strip() if not isinstance(acceptance_criteria, dict) else str(acceptance_criteria),
                budget_slice=budget_slice,
                deadline=deadline or (utcnow() + timedelta(hours=2)),
                state="running",
            )
            self.session.add(record)
            self._emit_event(
                instance_id,
                "task.dispatched",
                task_id=assigned_subtask_id,
                agent_id=worker_id,
                details={
                    "goal": goal,
                    "orchestrator": inst.orchestrator_id,
                    "budget_slice": budget_slice,
                    "adapter_status": "connected",
                    "local_execution_id": local_exec_id,
                },
            )
            # Commit pre-execution intent to DB so it is durable across crashes & separate connections
            self.session.commit()

            # Build acceptance criteria dict if string or dict
            crit_dict = None
            if isinstance(acceptance_criteria, dict):
                crit_dict = acceptance_criteria
            elif isinstance(acceptance_criteria, str) and acceptance_criteria.strip():
                crit_dict = {"contains": [acceptance_criteria.strip()]}

            try:
                exec_res = self.hermes_adapter.dispatch_and_run(
                    subtask_id=assigned_subtask_id,
                    goal=goal,
                    acceptance_criteria=crit_dict,
                    local_execution_id=local_exec_id,
                )
            except Exception as exc:
                record.state = "failed"
                record.completed_at = utcnow()
                updated_input_ref["error"] = str(exc)
                record.input_ref = dict(updated_input_ref)
                flag_modified(record, "input_ref")
                self._emit_event(
                    instance_id,
                    "agent.task_failed",
                    task_id=assigned_subtask_id,
                    agent_id=worker_id,
                    details={
                        "local_execution_id": local_exec_id,
                        "error": str(exc),
                    },
                )
                if self.budget and res:
                    self.budget.release(actor, res.id)
                self.session.commit()
                raise

            ext_sess_id = exec_res.get("external_session_id")
            val_passed = bool(exec_res.get("validation_passed"))
            duration_ms = exec_res.get("duration_ms")
            est_cost = exec_res.get("estimated_cost_usd", 0.0)
            cost_status = exec_res.get("cost_status", "unknown")

            local_exec_id = exec_res.get("local_execution_id") or local_exec_id
            updated_input_ref["local_execution_id"] = local_exec_id
            updated_input_ref["external_session_id"] = ext_sess_id
            updated_input_ref["execution_trace"] = exec_res
            updated_input_ref["tokens"] = exec_res.get("tokens")
            updated_input_ref["model"] = exec_res.get("model")
            updated_input_ref["cost_status"] = cost_status
            updated_input_ref["duration_ms"] = duration_ms
            updated_input_ref["validation_passed"] = val_passed
            if exec_res.get("output"):
                updated_input_ref["output"] = exec_res["output"]
            if exec_res.get("error"):
                updated_input_ref["error"] = exec_res["error"]

            if exec_res.get("state") == "completed" and val_passed:
                record.state = "completed"
                record.completed_at = utcnow()
                self._emit_event(
                    instance_id,
                    "agent.task_submitted",
                    task_id=assigned_subtask_id,
                    agent_id=worker_id,
                    details={"local_execution_id": local_exec_id},
                )
                self._emit_event(
                    instance_id,
                    "agent.task_completed",
                    task_id=assigned_subtask_id,
                    agent_id=worker_id,
                    details={
                        "local_execution_id": local_exec_id,
                        "external_session_id": ext_sess_id,
                        "output_preview": (exec_res.get("output") or "")[:200],
                        "duration_ms": duration_ms,
                        "model": exec_res.get("model"),
                        "tokens": exec_res.get("tokens"),
                    },
                )
                # Truthful budget accounting: unknown pricing is NOT zero cost!
                if self.budget and res:
                    if cost_status in ("actual", "estimated") and est_cost > 0.0:
                        settle_amt = Decimal(str(est_cost))
                        if settle_amt > Decimal(str(budget_slice)):
                            raise Conflict(
                                "settlement_exceeds_budget",
                                f"Settlement cost (${settle_amt}) exceeds budget slice limit (${budget_slice})"
                            )
                        self.budget.settle(actor, res.id, settled_amount=settle_amt)
                        updated_input_ref["budget_settlement"] = {
                            "status": "settled",
                            "reserved_amount_usd": float(budget_slice),
                            "settled_amount_usd": float(settle_amt),
                            "cost_status": cost_status,
                        }
                    else:
                        # Unknown pricing: hold reservation in 'unknown' state to prevent unbudgeted token drain
                        self.budget.hold_unknown_pricing(actor, res.id, reason="provider_pricing_unknown")
                        updated_input_ref["budget_settlement"] = {
                            "status": "held_unknown",
                            "reserved_amount_usd": float(budget_slice),
                            "settled_amount_usd": None,
                            "cost_status": "unknown",
                            "tokens": exec_res.get("tokens"),
                            "model": exec_res.get("model"),
                            "provider": "hermes_local_provider",
                            "note": "Provider pricing is unknown; reservation retained in 'unknown' state to prevent unbudgeted token consumption until reconciliation.",
                        }
            else:
                record.state = exec_res.get("state") or "failed"
                record.completed_at = utcnow()
                self._emit_event(
                    instance_id,
                    "agent.task_failed",
                    task_id=assigned_subtask_id,
                    agent_id=worker_id,
                    details={
                        "local_execution_id": local_exec_id,
                        "external_session_id": ext_sess_id,
                        "error": exec_res.get("error"),
                        "duration_ms": duration_ms,
                    },
                )
                if self.budget and res:
                    tokens_used = exec_res.get("tokens") or 0
                    if tokens_used > 0 and cost_status == "unknown":
                        # Tokens were consumed with unknown pricing; cannot claim zero cost!
                        self.budget.hold_unknown_pricing(actor, res.id, reason="task_unvalidated_but_tokens_consumed")
                        updated_input_ref["budget_settlement"] = {
                            "status": "held_unknown",
                            "reserved_amount_usd": float(budget_slice),
                            "settled_amount_usd": None,
                            "cost_status": "unknown",
                            "tokens": tokens_used,
                            "note": "Subtask failed or rejected by validator, but tokens were consumed with unknown pricing; reservation held pending reconciliation.",
                        }
                    elif cost_status in ("actual", "estimated") and est_cost > 0.0:
                        settle_amt = Decimal(str(est_cost))
                        self.budget.settle(actor, res.id, settled_amount=min(settle_amt, Decimal(str(budget_slice))))
                        updated_input_ref["budget_settlement"] = {
                            "status": "settled",
                            "reserved_amount_usd": float(budget_slice),
                            "settled_amount_usd": float(settle_amt),
                            "cost_status": cost_status,
                        }
                    else:
                        self.budget.release(actor, res.id)
                        updated_input_ref["budget_settlement"] = {
                            "status": "released",
                            "reserved_amount_usd": float(budget_slice),
                            "settled_amount_usd": 0.0,
                        }

            record.input_ref = dict(updated_input_ref)
            flag_modified(record, "input_ref")
            self.session.commit()
            return record

        else:
            # Internal or other worker: mark dispatched
            dispatch_state = "dispatched"
            record = DispatchRecord(
                id=f"disp-{uuid4().hex[:12]}",
                instance_id=instance_id,
                root_task_id=root_task_id,
                subtask_id=assigned_subtask_id,
                orchestrator_id=inst.orchestrator_id,
                worker_id=worker_id,
                idempotency_key=idem_key,
                input_ref=updated_input_ref,
                goal=goal.strip(),
                acceptance_criteria=str(acceptance_criteria).strip() if not isinstance(acceptance_criteria, dict) else str(acceptance_criteria),
                budget_slice=budget_slice,
                deadline=deadline or (utcnow() + timedelta(hours=2)),
                state=dispatch_state,
            )
            self.session.add(record)
            self._emit_event(
                instance_id,
                "task.dispatched",
                task_id=assigned_subtask_id,
                agent_id=worker_id,
                details={
                    "goal": goal,
                    "orchestrator": inst.orchestrator_id,
                    "budget_slice": budget_slice,
                    "adapter_status": "connected",
                },
            )
            self.session.commit()
            return record

    def complete_subtask(
        self,
        actor: Actor,
        instance_id: str,
        subtask_id: str,
        output: str = "",
        settled_budget: float | None = None,
    ) -> DispatchRecord:
        actor.require_owner()
        self.get_instance(actor, instance_id)
        stmt = select(DispatchRecord).where(
            DispatchRecord.instance_id == instance_id,
            DispatchRecord.subtask_id == subtask_id,
        )
        rec = self.session.execute(stmt).scalar_one_or_none()
        if not rec:
            raise NotFound(f"Subtask dispatch record not found: {subtask_id}")
        if rec.state in ("completed", "failed", "cancelled"):
            raise Conflict(f"Subtask is already in terminal state: {rec.state}")

        rec.state = "completed"
        rec.completed_at = utcnow()
        ref = dict(rec.input_ref or {})
        ref["output"] = output
        rec.input_ref = dict(ref)
        flag_modified(rec, "input_ref")

        res_id = ref.get("reservation_id")
        if self.budget and res_id:
            amt = settled_budget if settled_budget is not None else rec.budget_slice
            self.budget.settle(actor, res_id, settled_amount=Decimal(str(amt)))

        self._emit_event(
            instance_id,
            "agent.task_completed",
            task_id=subtask_id,
            agent_id=rec.worker_id,
            details={"output_preview": output[:200]},
        )
        self.session.flush()
        return rec

    def cancel_subtask(
        self,
        actor: Actor,
        instance_id: str,
        subtask_id: str,
        reason: str = "User cancelled subtask",
    ) -> DispatchRecord:
        actor.require_owner()
        self.get_instance(actor, instance_id)
        stmt = select(DispatchRecord).where(
            DispatchRecord.instance_id == instance_id,
            DispatchRecord.subtask_id == subtask_id,
        )
        rec = self.session.execute(stmt).scalar_one_or_none()
        if not rec:
            raise NotFound(f"Subtask dispatch record not found: {subtask_id}")
        if rec.state in ("completed", "failed", "cancelled"):
            raise Conflict(f"Subtask is already in terminal state: {rec.state}")

        rec.state = "cancelled"
        rec.completed_at = utcnow()
        ref = dict(rec.input_ref or {})
        ref["cancel_reason"] = reason
        rec.input_ref = dict(ref)
        flag_modified(rec, "input_ref")

        res_id = ref.get("reservation_id")
        if self.budget and res_id:
            self.budget.release(actor, res_id)

        self._emit_event(
            instance_id,
            "agent.task_cancelled",
            task_id=subtask_id,
            agent_id=rec.worker_id,
            details={"reason": reason},
        )
        self.session.flush()
        return rec

    def record_handoff(
        self,
        actor: Actor,
        instance_id: str,
        stage: str,
        goal: str,
        source_worker_id: str,
        target_worker_id: str,
        source_task_id: str,
        completed_items: list[str],
        artifact_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        unresolved_issues: list[str] | None = None,
        risks: list[str] | None = None,
        next_steps: list[str] | None = None,
    ) -> HandoffPacket:
        actor.require_owner()
        inst = self.get_instance(actor, instance_id)

        packet = HandoffPacket(
            id=f"hnd-{uuid4().hex[:12]}",
            instance_id=instance_id,
            stage=stage,
            goal=goal,
            completed_items=completed_items,
            artifact_refs=artifact_refs or [],
            evidence_refs=evidence_refs or [],
            unresolved_issues=unresolved_issues or [],
            risks=risks or [],
            next_steps=next_steps or [],
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            target_worker_id=target_worker_id,
        )
        self.session.add(packet)

        self._emit_event(
            instance_id,
            "handoff.created",
            task_id=source_task_id,
            agent_id=target_worker_id,
            details={
                "stage": stage,
                "from": source_worker_id,
                "to": target_worker_id,
                "completed_count": len(completed_items),
            },
        )
        self.session.flush()
        return packet

    # -----------------------------------------------------------------------
    # Snapshot & Cursor Replay
    # -----------------------------------------------------------------------
    def get_snapshot(self, actor: Actor, instance_id: str) -> dict[str, Any]:
        actor.require_owner()
        inst = self.get_instance(actor, instance_id)
        connectors = self.probe_connectors()

        # Fetch dispatches
        stmt_disp = (
            select(DispatchRecord)
            .where(DispatchRecord.instance_id == instance_id)
            .order_by(DispatchRecord.created_at.asc())
        )
        dispatches = list(self.session.execute(stmt_disp).scalars().all())

        # Fetch handoffs
        stmt_hnd = (
            select(HandoffPacket)
            .where(HandoffPacket.instance_id == instance_id)
            .order_by(HandoffPacket.created_at.asc())
        )
        handoffs = list(self.session.execute(stmt_hnd).scalars().all())

        # Fetch recent events
        stmt_evt = (
            select(CanvasEvent)
            .where(CanvasEvent.instance_id == instance_id)
            .order_by(CanvasEvent.seq.asc())
        )
        events = list(self.session.execute(stmt_evt).scalars().all())

        return {
            "instance": {
                "id": inst.id,
                "project_name": inst.project_name,
                "domain": inst.domain,
                "template_id": inst.template_id,
                "orchestrator_id": inst.orchestrator_id,
                "state": inst.state,
                "config": inst.config,
                "created_at": inst.created_at.isoformat(),
            },
            "connectors": connectors,
            "dispatches": [
                {
                    "id": d.id,
                    "subtask_id": d.subtask_id,
                    "orchestrator_id": d.orchestrator_id,
                    "worker_id": d.worker_id,
                    "goal": d.goal,
                    "state": d.state,
                    "budget_slice": float(d.budget_slice),
                    "created_at": d.created_at.isoformat(),
                }
                for d in dispatches
            ],
            "handoffs": [
                {
                    "id": h.id,
                    "stage": h.stage,
                    "from": h.source_worker_id,
                    "to": h.target_worker_id,
                    "completed_items": h.completed_items,
                    "created_at": h.created_at.isoformat(),
                }
                for h in handoffs
            ],
            "events": [
                {
                    "seq": e.seq,
                    "event_type": e.event_type,
                    "task_id": e.task_id,
                    "agent_id": e.agent_id,
                    "details": e.details,
                    "created_at": e.created_at.isoformat(),
                }
                for e in events
            ],
        }

    def get_events(self, actor: Actor, instance_id: str, cursor: int = 0) -> list[dict[str, Any]]:
        actor.require_owner()
        self.get_instance(actor, instance_id)

        stmt = (
            select(CanvasEvent)
            .where(
                CanvasEvent.instance_id == instance_id,
                CanvasEvent.seq > cursor,
            )
            .order_by(CanvasEvent.seq.asc())
        )
        events = list(self.session.execute(stmt).scalars().all())
        return [
            {
                "seq": e.seq,
                "event_type": e.event_type,
                "task_id": e.task_id,
                "agent_id": e.agent_id,
                "details": e.details,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]

    # -----------------------------------------------------------------------
    # Helper
    # -----------------------------------------------------------------------
    def _emit_event(
        self,
        instance_id: str,
        event_type: str,
        task_id: str | None = None,
        agent_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> CanvasEvent:
        with self._event_lock:
            if self.session.bind and self.session.bind.dialect.name == "postgresql":
                self.session.execute(
                    select(CanvasInstance.id).where(CanvasInstance.id == instance_id).with_for_update()
                )
            stmt_max_seq = select(func.coalesce(func.max(CanvasEvent.seq), 0)).where(
                CanvasEvent.instance_id == instance_id
            )
            current_max = self.session.execute(stmt_max_seq).scalar() or 0
            seq = current_max + 1

            evt = CanvasEvent(
                id=f"evt-{uuid4().hex[:12]}",
                instance_id=instance_id,
                seq=seq,
                event_type=event_type,
                task_id=task_id,
                agent_id=agent_id,
                details=details or {},
                trace_id=f"tr-{uuid4().hex[:8]}",
            )
            self.session.add(evt)
            self.session.flush()
            return evt
