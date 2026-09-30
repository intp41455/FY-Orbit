"""Unit test for HermesAdapter (Threshold 3: authentic agent roundtrip & trace)."""

from find_yourself.adapters.hermes_adapter import HermesAdapter


def test_hermes_adapter_probe():
    adapter = HermesAdapter()
    info = adapter.probe()
    assert info["name"] == "Hermes"
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
