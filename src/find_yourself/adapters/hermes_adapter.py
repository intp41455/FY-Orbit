"""Hermes Agent Adapter for Canvas collaboration (05 Multi-Agent Collaboration Canvas).

Integrates with locally installed NousResearch/hermes-agent CLI:
- Protocol probing (--version handshake and capability declaration)
- Subtask submission (-z oneshot mode) with native --usage-file telemetry
- Local execution tracking (local_execution_id) and authentic external session capture (external_session_id)
- Distinct separation between execution completion and acceptance criteria validation
- Artifact/output collection and spend accounting
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
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
                "capabilities": {
                    "oneshot": False,
                    "usage_file": False,
                    "cancel": False,
                    "reconcile": False,
                },
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
                    "capabilities": {
                        "oneshot": True,
                        "usage_file": True,
                        "cancel": False,
                        "reconcile": False,
                    },
                    "blocking_reason": None,
                }
            return {
                "name": "Hermes",
                "healthy": False,
                "stage": "发现接口",
                "version": None,
                "capabilities": {
                    "oneshot": False,
                    "usage_file": False,
                    "cancel": False,
                    "reconcile": False,
                },
                "blocking_reason": f"Hermes --version exited with code {res.returncode}: {res.stderr.strip()}",
            }
        except Exception as exc:
            return {
                "name": "Hermes",
                "healthy": False,
                "stage": "发现接口",
                "version": None,
                "capabilities": {
                    "oneshot": False,
                    "usage_file": False,
                    "cancel": False,
                    "reconcile": False,
                },
                "blocking_reason": f"Probe error: {str(exc)}",
            }

    def dispatch_and_run(
        self,
        subtask_id: str,
        goal: str,
        timeout_sec: int = 120,
        acceptance_criteria: dict[str, Any] | None = None,
        local_execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute subtask via Hermes oneshot mode and capture complete execution trace.

        Strictly distinguishes:
        - local_execution_id: generated locally to track the subtask execution invocation
        - external_session_id: captured truthfully from Hermes execution telemetry
        - execution_succeeded vs accepted_by_validator: exit code 0 does not automatically mean acceptance
        """
        probe_info = self.probe()
        if not probe_info["healthy"]:
            return {
                "subtask_id": subtask_id,
                "state": "pending_adapter",
                "validation_passed": False,
                "local_execution_id": None,
                "external_session_id": None,
                "receipt": None,
                "error": probe_info.get("blocking_reason"),
            }

        local_execution_id = local_execution_id or f"exec-local-{uuid4().hex[:12]}"
        start_time = time.time()
        start_iso = datetime.now(timezone.utc).isoformat()

        trace_record: dict[str, Any] = {
            "subtask_id": subtask_id,
            "local_execution_id": local_execution_id,
            "external_session_id": None,
            "agent": "Hermes",
            "goal": goal,
            "stage_transitions": [
                {"stage": "submitted", "timestamp": start_iso, "local_execution_id": local_execution_id},
                {"stage": "running", "timestamp": start_iso},
            ],
            "state": "running",
            "validation_passed": False,
            "acceptance_criteria": acceptance_criteria,
        }

        # Prepare temporary usage file for spend and session telemetry
        runtime_dir = Path(".runtime")
        runtime_dir.mkdir(parents=True, exist_ok=True)
        usage_file = runtime_dir / f"hermes_usage_{local_execution_id}.json"

        try:
            cmd = [self.binary_path, "-z", goal, "--usage-file", str(usage_file)]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
            )
            duration_ms = int((time.time() - start_time) * 1000)
            end_iso = datetime.now(timezone.utc).isoformat()

            # Read usage telemetry if generated by Hermes
            usage_data: dict[str, Any] = {}
            if usage_file.exists():
                try:
                    usage_data = json.loads(usage_file.read_text(encoding="utf-8"))
                except Exception:
                    usage_data = {}
                finally:
                    try:
                        usage_file.unlink(missing_ok=True)
                    except Exception:
                        pass

            external_session_id = usage_data.get("session_id")
            tokens = usage_data.get("total_tokens")
            model = usage_data.get("model")
            estimated_cost_usd = usage_data.get("estimated_cost_usd", 0.0)
            cost_status = usage_data.get("cost_status", "unknown")

            trace_record["external_session_id"] = external_session_id
            trace_record["duration_ms"] = duration_ms
            trace_record["tokens"] = tokens
            trace_record["model"] = model
            trace_record["estimated_cost_usd"] = estimated_cost_usd
            trace_record["cost_status"] = cost_status

            if res.returncode == 0:
                output_text = res.stdout.strip()
                trace_record["output"] = output_text
                trace_record["stage_transitions"].append({
                    "stage": "execution_succeeded",
                    "timestamp": end_iso,
                    "external_session_id": external_session_id,
                    "tokens": tokens,
                    "model": model,
                })

                # Validate output against acceptance_criteria
                val_ok = True
                val_err = None
                if acceptance_criteria:
                    # 1. Contains check
                    expected_contains = acceptance_criteria.get("contains")
                    if expected_contains:
                        if isinstance(expected_contains, str):
                            expected_contains = [expected_contains]
                        for phrase in expected_contains:
                            if phrase.lower() not in output_text.lower():
                                val_ok = False
                                val_err = f"Acceptance criteria failed: missing required phrase '{phrase}' in output"
                                break

                    # 2. Minimum length check
                    min_len = acceptance_criteria.get("min_length")
                    if val_ok and min_len and len(output_text) < min_len:
                        val_ok = False
                        val_err = f"Acceptance criteria failed: output length {len(output_text)} < min_length {min_len}"

                    # 3. Must be valid JSON check
                    if val_ok and acceptance_criteria.get("must_be_json"):
                        try:
                            json.loads(output_text)
                        except Exception:
                            val_ok = False
                            val_err = "Acceptance criteria failed: output is not valid JSON"

                if val_ok:
                    trace_record["stage_transitions"].append({
                        "stage": "accepted_by_validator",
                        "timestamp": end_iso,
                    })
                    trace_record["state"] = "completed"
                    trace_record["validation_passed"] = True
                    return trace_record
                else:
                    trace_record["stage_transitions"].append({
                        "stage": "rejected_by_validator",
                        "timestamp": end_iso,
                        "error": val_err,
                    })
                    trace_record["state"] = "failed"
                    trace_record["validation_passed"] = False
                    trace_record["error"] = val_err
                    return trace_record
            else:
                err_text = res.stderr.strip() or res.stdout.strip()
                trace_record["stage_transitions"].append({
                    "stage": "failed",
                    "timestamp": end_iso,
                    "error": err_text,
                })
                trace_record["state"] = "failed"
                trace_record["validation_passed"] = False
                trace_record["error"] = err_text
                return trace_record

        except subprocess.TimeoutExpired:
            end_iso = datetime.now(timezone.utc).isoformat()
            duration_ms = int((time.time() - start_time) * 1000)
            trace_record["duration_ms"] = duration_ms
            trace_record["stage_transitions"].append({
                "stage": "cancelled",
                "timestamp": end_iso,
                "reason": "timeout",
            })
            trace_record["state"] = "cancelled"
            trace_record["validation_passed"] = False
            trace_record["error"] = f"Hermes execution timed out after {timeout_sec}s"
            if usage_file.exists():
                usage_file.unlink(missing_ok=True)
            return trace_record
        except Exception as exc:
            end_iso = datetime.now(timezone.utc).isoformat()
            duration_ms = int((time.time() - start_time) * 1000)
            trace_record["duration_ms"] = duration_ms
            trace_record["stage_transitions"].append({
                "stage": "failed",
                "timestamp": end_iso,
                "error": str(exc),
            })
            trace_record["state"] = "failed"
            trace_record["validation_passed"] = False
            trace_record["error"] = str(exc)
            if usage_file.exists():
                usage_file.unlink(missing_ok=True)
            return trace_record
