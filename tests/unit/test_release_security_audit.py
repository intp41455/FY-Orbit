"""真实校验器红绿测试与更新路径限制。"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from find_yourself.services import release_update as ru
from find_yourself.services.errors import ValidationFailed

ROOT = Path(__file__).resolve().parents[2]


def builder():
    spec = importlib.util.spec_from_file_location("audit_builder", ROOT / "deploy/build_release_package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_covers_binary_and_frontend_and_detects_tampering(tmp_path):
    exe = tmp_path / "find-yourself-backend/app.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"binary")
    js = tmp_path / "web/dist/file with spaces.js"
    js.parent.mkdir(parents=True)
    js.write_text("test", encoding="utf-8")
    shutil.copy2(ROOT / "deploy/verify_release.ps1", tmp_path / "verify_release.ps1")
    manifest = tmp_path / "SHA256SUMS.txt"
    manifest.write_text(builder()._package_filesums(tmp_path), encoding="utf-8")
    assert exe.relative_to(tmp_path).as_posix() in manifest.read_text(encoding="utf-8")
    assert js.relative_to(tmp_path).as_posix() in manifest.read_text(encoding="utf-8")
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell unavailable")
    cmd = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
           str(tmp_path / "verify_release.ps1")]
    result = subprocess.run(cmd, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    exe.write_bytes(b"tampered")
    assert subprocess.run(cmd, capture_output=True).returncode != 0
    exe.write_bytes(b"binary")
    manifest.write_text("# empty\n", encoding="utf-8")
    assert subprocess.run(cmd, capture_output=True).returncode != 0
    manifest.unlink()
    assert subprocess.run(cmd, capture_output=True).returncode != 0


@pytest.mark.parametrize("name", ["../x.zip", "C:x.zip", "x\\y.zip", "/x.zip", "x.exe"])
def test_update_asset_path_is_rejected(name):
    with pytest.raises(ValidationFailed):
        ru._safe_asset_name(name)


def test_update_missing_digest_refuses_before_network(tmp_path, monkeypatch):
    def network(*args, **kwargs):
        pytest.fail("network called before digest validation")
    monkeypatch.setattr(ru.urllib.request, "urlopen", network)
    with pytest.raises(ValidationFailed):
        ru.download_release_zip(ru.ReleaseAsset(name="x.zip", download_url="https://example.invalid"),
                                tmp_path / "x.zip")


def test_pending_path_cannot_copy_arbitrary_file(tmp_path):
    pending = tmp_path / ".update/pending"
    pending.mkdir(parents=True)
    external = tmp_path / "outside.zip"
    external.write_bytes(b"not-secret-test")
    (pending / "pending.json").write_text(json.dumps({
        "zip_path": str(external), "zip_name": "../escape.zip",
        "expected_sha256": hashlib.sha256(external.read_bytes()).hexdigest()}), encoding="utf-8")
    with pytest.raises(ValidationFailed):
        ru.apply_staged_update(tmp_path)
    assert not (tmp_path / ".update/incoming").exists()
