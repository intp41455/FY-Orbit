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
import shutil
import subprocess
import threading
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
                if c_res.returncode == 0:
                    codex_healthy = True
                    codex_stage = "本机握手通过"
                    codex_version = (c_res.stdout.strip().splitlines() or [""])[0]
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
            "stage": "合成任务往返",
            "healthy": True,
            "binary_path": "INTERNAL",
            "version": "1.0-builtin",
            "blocking_reason": None,
            "domains": ["personal", "work"],
        })
        connectors.append({
            "name": "EngineeringAgent",
            "protocol": "A2A / Internal",
            "role": "worker",
            "stage": "合成任务往返",
            "healthy": True,
            "binary_path": "INTERNAL",
            "version": "1.0-builtin",
            "blocking_reason": None,
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

        # 2. Verify root task existence and ownership
        root_task = self.session.get(Task, root_task_id)
        if not root_task or root_task.owner_id != actor.owner_id:
            raise ValidationFailed(
                f"Root task {root_task_id} not found or not owned by caller"
            )

        # 3. Enforce domain boundary and strict data grant authorization
        input_ref_dict = dict(input_ref or {})
        grant_id = input_ref_dict.get("grant_id")
        input_domain = (
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

        # Cross-reference with underlying Memory table if record exists to prevent client spoofing
        for rid in target_records:
            mem = self.session.get(Memory, rid)
            if mem is not None and mem.domain != inst.domain:
                input_domain = mem.domain

        is_cross_domain = bool(input_domain and input_domain != inst.domain)

        if is_cross_domain or grant_id:
            if not grant_id:
                raise ValidationFailed(
                    f"Canvas in '{inst.domain}' domain cannot ingest '{input_domain}' domain data without explicit grant authorization"
                )

            grant = self.session.get(Grant, grant_id)
            if grant is None:
                raise ValidationFailed(f"Grant {grant_id} not found")
            if grant.state != "active":
                raise ValidationFailed(f"Grant {grant_id} is not active (state: {grant.state})")
            if grant.expires_at <= utcnow():
                raise ValidationFailed(f"Grant {grant_id} has expired at {grant.expires_at.isoformat()}")
            if grant.consumer_domain != inst.domain:
                raise ValidationFailed(
                    f"Grant {grant_id} consumer domain '{grant.consumer_domain}' does not match canvas domain '{inst.domain}'"
                )
            if input_domain and grant.source_domain != input_domain:
                raise ValidationFailed(
                    f"Grant {grant_id} source domain '{grant.source_domain}' does not match input domain '{input_domain}'"
                )

            # Check coverage of all target records
            if target_records:
                for rid in target_records:
                    if rid not in (grant.record_ids or []):
                        raise ValidationFailed(
                            f"Grant {grant_id} does not authorize record '{rid}' (authorized: {grant.record_ids})"
                        )

        # Idempotency check to prevent duplicate dispatches
        assigned_subtask_id = subtask_id or f"sub-{uuid4().hex[:12]}"
        idem_key = idempotency_key or f"dispatch-{instance_id}-{assigned_subtask_id}"

        existing_record = self.session.execute(
            select(DispatchRecord).where(DispatchRecord.idempotency_key == idem_key)
        ).scalar_one_or_none()
        if existing_record:
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
                acceptance_criteria=acceptance_criteria.strip(),
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

            exec_res = self.hermes_adapter.dispatch_and_run(subtask_id=assigned_subtask_id, goal=goal)
            receipt_id = exec_res.get("receipt_id")
            updated_input_ref["receipt_id"] = receipt_id
            updated_input_ref["execution_trace"] = exec_res
            if exec_res.get("output"):
                updated_input_ref["output"] = exec_res["output"]
            if exec_res.get("error"):
                updated_input_ref["error"] = exec_res["error"]
            if exec_res.get("duration_ms") is not None:
                updated_input_ref["duration_ms"] = exec_res["duration_ms"]

            if exec_res.get("state") == "completed":
                dispatch_state = "completed"
                completed_at = utcnow()
                self._emit_event(
                    instance_id,
                    "agent.task_accepted",
                    task_id=assigned_subtask_id,
                    agent_id=worker_id,
                    details={"receipt_id": receipt_id},
                )
                self._emit_event(
                    instance_id,
                    "agent.task_completed",
                    task_id=assigned_subtask_id,
                    agent_id=worker_id,
                    details={
                        "receipt_id": receipt_id,
                        "output_preview": (exec_res.get("output") or "")[:200],
                        "duration_ms": exec_res.get("duration_ms"),
                    },
                )
                if self.budget and res:
                    self.budget.settle(actor, res.id, settled_amount=Decimal(str(budget_slice)))
            else:
                dispatch_state = exec_res.get("state") or "failed"
                completed_at = utcnow()
                self._emit_event(
                    instance_id,
                    f"agent.task_{dispatch_state}",
                    task_id=assigned_subtask_id,
                    agent_id=worker_id,
                    details={
                        "error": exec_res.get("error"),
                        "duration_ms": exec_res.get("duration_ms"),
                    },
                )
                if self.budget and res:
                    self.budget.release(actor, res.id)

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
                acceptance_criteria=acceptance_criteria.strip(),
                budget_slice=budget_slice,
                deadline=deadline or (utcnow() + timedelta(hours=2)),
                state=dispatch_state,
                completed_at=completed_at,
            )
            self.session.add(record)
            self.session.flush()
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
                acceptance_criteria=acceptance_criteria.strip(),
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
            self.session.flush()
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
        rec.input_ref = ref

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
        rec.input_ref = ref

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
