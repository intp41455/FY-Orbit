"""发行更新服务的单元护栏。

覆盖文档点名的自动更新最小闭环里，最容易出假数据的几个分叉：

* 离线模式 ⇒ 不联网（真实行为），而不是「假装检查了但没结果」；
* 联网失败 ⇒ 如实 reporting，而不是把网络错误包装成「已是最新」；
* SHA-256 不匹配 ⇒ ValidationFailed 且删除坏包，而不是静默通过；
* apply 不替换运行中的 exe，只标记 incoming/（下次启动替换）。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from find_yourself.services import release_update as ru
from find_yourself.services.errors import Conflict, ValidationFailed

# ---------------------------------------------------------------------------
# 版本号比较
# ---------------------------------------------------------------------------

def test_version_newer_compares_major_minor_patch():
    assert ru.version_newer("v1.1.0", "1.0.0") is True
    assert ru.version_newer("v1.0.1", "1.0.0") is True
    assert ru.version_newer("v1.0.0", "1.0.0") is False
    assert ru.version_newer("v0.9.9", "1.0.0") is False
    # 前缀 v 与无 v 等价
    assert ru.version_newer("1.2.0", "v1.1.9") is True


def test_parse_version_handles_prefixes_and_prerelease():
    assert ru.parse_version("v1.2.3") == (1, 2, 3, "")
    assert ru.parse_version("1.2.3-beta.1") == (1, 2, 3, "beta.1")
    assert ru.parse_version("not.a.version") is None


# ---------------------------------------------------------------------------
# 离线模式
# ---------------------------------------------------------------------------

def test_check_update_honors_offline_mode(monkeypatch):
    monkeypatch.setenv("FY_OFFLINE_MODE", "1")
    verdict = ru.check_update()
    assert verdict.offline_blocked is True
    assert verdict.update_available is False
    assert "离线模式" in verdict.reason


def test_stage_update_refuses_when_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("FY_OFFLINE_MODE", "1")
    with pytest.raises(Conflict):
        ru.stage_update(tmp_path)


# ---------------------------------------------------------------------------
# 联网失败要如实报告
# ---------------------------------------------------------------------------

def test_check_update_network_failure_is_reported_not_faked(monkeypatch):
    monkeypatch.setenv("FY_OFFLINE_MODE", "0")

    def boom(url):
        raise OSError("dns failed")

    verdict = ru.check_update(fetcher=boom)
    assert verdict.offline_blocked is False
    assert verdict.update_available is False
    assert "无法连接更新源" in verdict.reason


def test_check_update_reports_new_version_when_available(monkeypatch):
    monkeypatch.setenv("FY_OFFLINE_MODE", "0")

    def fake_fetch(url):
        return {
            "tag_name": "v9.9.9",
            "html_url": "https://example.invalid/rel",
            "assets": [
                {"name": "FY-Orbit-Windows-v9.9.9.zip", "size": 1,
                 "browser_download_url": "https://example.invalid/app.zip"},
            ],
        }

    verdict = ru.check_update(fetcher=fake_fetch)
    assert verdict.latest_version == "9.9.9"
    assert verdict.update_available is (ru.parse_version("9.9.9") is not None
                                        and ru.version_newer("9.9.9", ru.current_version()))
    # 签名状态必须如实是 unsigned，不允许写成已签名
    assert verdict.signature_status == "unsigned"


def test_check_update_no_tag_in_payload_is_not_a_new_version(monkeypatch):
    monkeypatch.setenv("FY_OFFLINE_MODE", "0")
    verdict = ru.check_update(fetcher=lambda url: {"html_url": "x"})
    assert verdict.update_available is False
    assert "不含 tag_name" in verdict.reason


# ---------------------------------------------------------------------------
# SHA-256 校验
# ---------------------------------------------------------------------------

def test_download_verifies_sha256_and_deletes_bad_package(monkeypatch, tmp_path):
    monkeypatch.setenv("FY_OFFLINE_MODE", "0")

    class _Resp:
        def __init__(self, data: bytes):
            self._buf = io.BytesIO(data)

        def read(self, n=-1):
            return self._buf.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = b"not a real update zip"

    def fake_urlopen(req, timeout=0, **kw):
        return _Resp(payload)

    monkeypatch.setattr(ru.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "app.zip"
    with pytest.raises(ValidationFailed):
        ru.download_release_zip(
            ru.ReleaseAsset(name="app.zip", download_url="https://example.invalid"),
            dest,
            expected_sha256="0" * 64,
        )
    assert not dest.exists(), "校验失败后的坏包必须被删除"


def test_parse_sha256sums_accepts_common_formats():
    table = ru.parse_sha256sums(
        "# comment\n"
        + "a" * 64 + "  FY-Orbit-Windows-v1.0.0.zip\n"
        + "b" * 64 + " *app.exe\n"
        + "not-a-hash  x.bin\n"
    )
    assert table["FY-Orbit-Windows-v1.0.0.zip"] == "a" * 64
    assert table["app.exe"] == "b" * 64
    assert "x.bin" not in table


# ---------------------------------------------------------------------------
# apply / rollback
# ---------------------------------------------------------------------------

def _write_pending(root: Path, zip_path: Path, expected: str):
    pending = root / ".update" / "pending"
    pending.mkdir(parents=True)
    meta = {
        "staged_at": "2026-10-08T00:00:00Z",
        "tag": "v9.9.9",
        "zip_name": zip_path.name,
        "zip_path": str(zip_path),
        "zip_sha256": ru.sha256_file(zip_path),
        "expected_sha256": expected,
        "from_version": "1.0.0",
        "signature_status": "unsigned",
    }
    (pending / "pending.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def test_apply_refuses_when_no_pending(tmp_path):
    with pytest.raises(ru.UpdatePendingMissing):
        ru.apply_staged_update(tmp_path)


def test_apply_copies_zip_to_incoming_and_records_metadata(tmp_path):
    fake_zip = tmp_path / "fake.zip"
    payload = b"zip-bytes"
    fake_zip.write_bytes(payload)
    expected = ru.sha256_file(fake_zip)
    _write_pending(tmp_path, fake_zip, expected)

    result = ru.apply_staged_update(tmp_path)
    assert result["status"] == "staged_for_next_launch"
    assert result["sha256"] == expected
    assert (tmp_path / ".update" / "incoming" / "incoming.json").is_file()
    assert (tmp_path / ".update" / "incoming" / "fake.zip").read_bytes() == payload


def test_apply_refuses_mismatched_hash(tmp_path):
    fake_zip = tmp_path / "fake.zip"
    fake_zip.write_bytes(b"actual")
    _write_pending(tmp_path, fake_zip, "0" * 64)
    with pytest.raises(ValidationFailed):
        ru.apply_staged_update(tmp_path)


def test_rollback_clears_pending_and_incoming(tmp_path):
    pending = tmp_path / ".update" / "pending"
    incoming = tmp_path / ".update" / "incoming"
    pending.mkdir(parents=True)
    incoming.mkdir(parents=True)
    (pending / "pending.json").write_text("{}", encoding="utf-8")
    (incoming / "incoming.json").write_text("{}", encoding="utf-8")
    result = ru.rollback_update(tmp_path)
    assert result["cleared_files"] == 2
    assert not (pending / "pending.json").exists()
    assert not (incoming / "incoming.json").exists()
