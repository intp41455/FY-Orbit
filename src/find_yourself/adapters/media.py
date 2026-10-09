"""Local media async job adapter (G7/A11).

A *local fake* job runner used to prove the async lifecycle: status, cancel,
cost reservation and artifact path. This is NOT a MinIO / remote media
integration — outputs go to the authenticated local artifact store. The real
provider call is BLOCKED_EXTERNAL until credentials and approval exist.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from decimal import Decimal

from ..db.models import Artifact
from ..services.actor import Actor
from ..services.audit import AuditService
from ..services.budget import BudgetService
from ..services.errors import Conflict, NotFound


@dataclass
class MediaJob:
    id: str
    owner_id: str
    kind: str
    status: str = "queued"          # queued -> processing -> completed/failed/cancelled
    cost_reserved: Decimal = Decimal("0")
    artifact_id: str | None = None
    failure: dict | None = None


class LocalMediaRunner:
    """In-process media job registry; deterministic local fake."""

    def __init__(self):
        self._jobs: dict[str, MediaJob] = {}
        self._lock = threading.Lock()

    def submit(self, actor: Actor, *, kind: str, budget: BudgetService, audit: AuditService,
               session, task_id: str, idempotency_key: str,
               request_cost: Decimal) -> MediaJob:
        actor.require_owner()
        job = MediaJob(id=uuid.uuid4().hex, owner_id=actor.owner_id, kind=kind)
        # Reserve cost atomically before doing work (no unknown-price charge).
        _ = budget.reserve(actor, task_id=task_id, amount=request_cost,
                                     idempotency_key=idempotency_key)
        job.cost_reserved = request_cost
        job.status = "processing"
        self._jobs[job.id] = job
        # Produce a local artifact (fake deterministic output).
        art = Artifact(id=uuid.uuid4().hex, task_id=task_id, domain="personal",
                       sha256="0" * 64, size=128,
                       media_type="text/plain", verified=True, verifier="local-fake")
        session.add(art)
        job.artifact_id = art.id
        job.status = "completed"
        audit.append(actor, "media.completed", job.id, {"kind": kind})
        return job

    def get(self, actor: Actor, job_id: str) -> MediaJob:
        j = self._jobs.get(job_id)
        if j is None or (actor.subject_type == "owner" and j.owner_id != actor.owner_id):
            raise NotFound("media_job_not_found", "Media job not found")
        return j

    def cancel(self, actor: Actor, job_id: str) -> MediaJob:
        j = self.get(actor, job_id)
        if j.status in ("completed", "cancelled", "failed"):
            raise Conflict("media_job_terminal", f"Job in terminal state '{j.status}'")
        j.status = "cancelled"
        return j


# Process-local registry (real provider would be a worker queue; BLOCKED_EXTERNAL).
runner = LocalMediaRunner()
