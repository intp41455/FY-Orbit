"""Unit test for HermesAdapter (Threshold 3: authentic agent roundtrip & trace)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from find_yourself.adapters.hermes_adapter import HermesAdapter


def test_hermes_adapter_probe():
    adapter = HermesAdapter()
    info = adapter.probe()
    assert info["name"] == "Hermes"
    assert "capabilities" in info
    assert info["capabilities"]["oneshot"] is (True if info["healthy"] else False)
    if info["healthy"]:
        assert info["stage"] == "本机握手通过"
        assert "Hermes Agent" in info["version"]
    else:
        assert info["stage"] in ("仅设计", "发现接口")


def test_hermes_adapter_mock_fallback():
    # Test non-existent path properly reports blocking reason
    fake_adapter = HermesAdapter(binary_path="nonexistent_binary_xyz_123")
    info = fake_adapter.probe()
    assert info["healthy"] is False
    assert info["stage"] == "发现接口"

    res = fake_adapter.dispatch_and_run("sub-test-fake", "Test goal")
    assert res["state"] == "pending_adapter"
    assert res["validation_passed"] is False


def test_hermes_adapter_acceptance_criteria_and_telemetry(monkeypatch, tmp_path):
    adapter = HermesAdapter(binary_path="mock_hermes")
    monkeypatch.setattr(adapter, "probe", lambda: {"name": "Hermes", "healthy": True, "stage": "本机握手通过", "version": "v1.0"})

    def mock_subprocess_run(cmd, **kwargs):
        # find --usage-file path from cmd
        usage_idx = cmd.index("--usage-file")
        usage_file = Path(cmd[usage_idx + 1])
        usage_data = {
            "session_id": "sess-20261001-real",
            "total_tokens": 1280,
            "model": "agnes-2.5-flash",
            "estimated_cost_usd": 0.04,
            "cost_status": "estimated",
        }
        usage_file.write_text(json.dumps(usage_data), encoding="utf-8")
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "Task output summary with MANDATORY_FLAG and details"
        mock_res.stderr = ""
        return mock_res

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)

    # 1. Test passing acceptance criteria
    res_pass = adapter.dispatch_and_run(
        "sub-test-01",
        "Perform task",
        acceptance_criteria={"contains": ["MANDATORY_FLAG"], "min_length": 10},
    )
    assert res_pass["state"] == "completed"
    assert res_pass["validation_passed"] is True
    assert res_pass["external_session_id"] == "sess-20261001-real"
    assert res_pass["tokens"] == 1280
    assert res_pass["model"] == "agnes-2.5-flash"
    assert res_pass["estimated_cost_usd"] == 0.04

    # 2. Test failing acceptance criteria
    res_fail = adapter.dispatch_and_run(
        "sub-test-02",
        "Perform task",
        acceptance_criteria={"contains": ["MISSING_PHRASE"]},
    )
    assert res_fail["state"] == "failed"
    assert res_fail["validation_passed"] is False
    assert "Acceptance criteria failed" in res_fail["error"]
