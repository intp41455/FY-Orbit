"""Hermes Agent Adapter for Canvas collaboration (05 Multi-Agent Collaboration Canvas).

Integrates with locally installed NousResearch/hermes-agent CLI:
- Protocol probing (--version handshake)
- Subtask submission (-z oneshot mode)
- Receipt verification
- Artifact/output collection
- Trace logging
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from uuid import uuid4


class HermesAdapter:
    def __init__(self, binary_path: str | None = None):
        self.binary_path = binary_path or shutil.which("hermes")

    def probe(self) -> dict[str, Any]:
        """Probe Hermes binary and verify protocol handshake via --version."""
        if not self.binary_path:
            return {
                "name": "Hermes",
                "healthy": False,
                "stage": "仅设计",
                "version": None,
                "blocking_reason": "未在本机 PATH 检测到 hermes 可执行文件",
            }

        try:
            res = subprocess.run(
                [self.binary_path, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            if res.returncode == 0:
                first_line = (res.stdout.strip().splitlines() or [""])[0]
                return {
                    "name": "Hermes",
                    "healthy": True,
                    "stage": "本机握手通过",
                    "version": first_line,
                    "binary_path": self.binary_path,
                    "blocking_reason": None,
                }
            return {
                "name": "Hermes",
                "healthy": False,
                "stage": "发现接口",
                "version": None,
                "blocking_reason": f"Hermes --version exited with code {res.returncode}: {res.stderr.strip()}",
            }
        except Exception as exc:
            return {
                "name": "Hermes",
                "healthy": False,
                "stage": "发现接口",
                "version": None,
                "blocking_reason": f"Probe error: {str(exc)}",
            }

    def dispatch_and_run(
        self,
        subtask_id: str,
        goal: str,
        timeout_sec: int = 60,
    ) -> dict[str, Any]:
        """Execute subtask via Hermes oneshot mode and capture complete execution trace."""
        probe_info = self.probe()
        if not probe_info["healthy"]:
            return {
                "subtask_id": subtask_id,
                "state": "pending_adapter",
                "receipt": None,
                "error": probe_info.get("blocking_reason"),
            }

        receipt_id = f"rcpt-{uuid4().hex[:12]}"
        start_time = time.time()
        start_iso = datetime.now(timezone.utc).isoformat()

        trace_record = {
            "subtask_id": subtask_id,
            "receipt_id": receipt_id,
            "agent": "Hermes",
            "goal": goal,
            "stage_transitions": [
                {"stage": "submitted", "timestamp": start_iso},
                {"stage": "accepted", "timestamp": start_iso, "receipt_id": receipt_id},
            ],
            "state": "running",
        }

        try:
            res = subprocess.run(
                [self.binary_path, "-z", goal],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
            )
            duration_ms = int((time.time() - start_time) * 1000)
            end_iso = datetime.now(timezone.utc).isoformat()

            if res.returncode == 0:
                output_text = res.stdout.strip()
                trace_record["stage_transitions"].append({"stage": "completed", "timestamp": end_iso})
                trace_record["state"] = "completed"
                trace_record["output"] = output_text
                trace_record["duration_ms"] = duration_ms
                return trace_record
            else:
                err_text = res.stderr.strip() or res.stdout.strip()
                trace_record["stage_transitions"].append({"stage": "failed", "timestamp": end_iso, "error": err_text})
                trace_record["state"] = "failed"
                trace_record["error"] = err_text
                trace_record["duration_ms"] = duration_ms
                return trace_record
        except subprocess.TimeoutExpired:
            end_iso = datetime.now(timezone.utc).isoformat()
            trace_record["stage_transitions"].append({"stage": "cancelled", "timestamp": end_iso, "reason": "timeout"})
            trace_record["state"] = "cancelled"
            trace_record["error"] = f"Hermes execution timed out after {timeout_sec}s"
            return trace_record
        except Exception as exc:
            end_iso = datetime.now(timezone.utc).isoformat()
            trace_record["stage_transitions"].append({"stage": "failed", "timestamp": end_iso, "error": str(exc)})
            trace_record["state"] = "failed"
            trace_record["error"] = str(exc)
            return trace_record
