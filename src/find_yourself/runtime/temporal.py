"""Temporal client wiring for the API process (FROZEN_CONTRACT §10).

This is a *client* (starter/signal/query) only — the durable workflow and its
activities live in the worker shard. The API process must never run activities.

Behaviour:
* When ``FY_TEMPORAL_ADDRESS`` is configured the app connects on startup and
  stores the client on ``app.state.temporal``. Shutdown closes it.
* When it is NOT configured, ``app.state.temporal`` is a disabled handle. The
  app still boots; ``/health/ready`` reports ``temporal: disabled`` and never
  claims Temporal readiness. Production settings already force an address.
* Workflow ID convention (frozen contract §10): ``fy-task:<task_id>``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Settings

TASK_WORKFLOW_NAME = "fy-task-workflow"
TASK_QUEUE = "find-yourself"


def workflow_id_for(task_id: str) -> str:
    return f"fy-task:{task_id}"


@dataclass
class TemporalRuntime:
    enabled: bool
    address: str = ""
    namespace: str = "default"
    queue: str = TASK_QUEUE
    _client: Any = None  # temporalio.client.Client | None

    @classmethod
    def disabled(cls) -> "TemporalRuntime":
        return cls(enabled=False)

    async def connect(self, settings: Settings) -> None:
        if not settings.temporal_address:
            self.enabled = False
            return
        # Imported lazily: a local/test app without temporalio installed must
        # still boot (disabled path).
        from temporalio.client import Client

        self.address = settings.temporal_address
        self.namespace = settings.temporal_namespace
        self.queue = settings.temporal_queue or TASK_QUEUE
        self._client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace)
        self.enabled = True

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        self.enabled = False

    def is_enabled(self) -> bool:
        return self.enabled and self._client is not None

    async def start_task_workflow(self, input_dict: dict[str, Any]) -> str:
        """Start the task workflow; returns the run-id. Caller must guard enabled."""
        wid = workflow_id_for(input_dict["task_id"])
        handle = await self._client.start_workflow(
            TASK_WORKFLOW_NAME,
            input_dict,
            id=wid,
            task_queue=self.queue,
        )
        return handle.result_run_id or ""

    async def send_cancel(self, task_id: str, reason: str = "cancelled_via_api") -> None:
        """Send the cooperative ``cancel`` signal. The DB remains the source of truth."""
        handle = self._client.get_workflow_handle(workflow_id_for(task_id))
        await handle.signal("cancel", reason)

    async def query_status(self, task_id: str) -> dict[str, Any] | None:
        try:
            handle = self._client.get_workflow_handle(workflow_id_for(task_id))
            return await handle.query("status")
        except Exception:
            return None
