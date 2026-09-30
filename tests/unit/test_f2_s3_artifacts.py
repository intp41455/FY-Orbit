"""Unit tests for F2: S3 Artifact Store, SigV4 private storage, and tombstone replay (O04)."""

import os
import socket
import threading
import time
import urllib.error
import urllib.request
import pytest
import uvicorn

from find_yourself.adapters.artifacts import LocalArtifactStore, S3ArtifactStore, build_artifact_store
from find_yourself.adapters.s3_service import create_s3_app
from find_yourself.config import Settings
from find_yourself.services.errors import NotFound, ValidationFailed


def get_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def local_s3_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("s3_data")
    port = get_free_port()
    app = create_s3_app(
        storage_dir=str(data_dir),
        access_key="fy-test-key",
        secret_key="fy-test-secret-at-least-32-chars-long",
        region="us-east-1",
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    endpoint = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{endpoint}/health/live", timeout=0.5) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.05)
    else:
        raise RuntimeError("Failed to start local S3 server")

    yield {
        "endpoint": endpoint,
        "access_key": "fy-test-key",
        "secret_key": "fy-test-secret-at-least-32-chars-long",
        "region": "us-east-1",
        "data_dir": str(data_dir),
    }
    server.should_exit = True
    t.join(timeout=2)


@pytest.fixture()
def s3_settings(local_s3_server):
    return Settings(
        session_secret="this-is-a-test-session-secret-that-is-long-enough-32",
        s3_endpoint=local_s3_server["endpoint"],
        s3_bucket="private-artifacts",
        s3_region=local_s3_server["region"],
        s3_access_key=local_s3_server["access_key"],
        s3_secret_key=local_s3_server["secret_key"],
    )


def test_s3_anonymous_public_access_is_forbidden(local_s3_server):
    """F2.1: Buckets are private; unauthenticated anonymous requests are rejected with 403."""
    url = f"{local_s3_server['endpoint']}/private-artifacts/test-artifact"
    req = urllib.request.Request(url, method="GET")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 403
    body = exc_info.value.read().decode("utf-8")
    assert "AccessDenied" in body


def test_s3_artifact_store_crud_lifecycle(s3_settings):
    """F2.2: Full S3 CRUD lifecycle with SHA-256 and size verification."""
    store = S3ArtifactStore(s3_settings)

    # 1. Put
    payload = b"Sample artifact content for unit test #12345"
    stored = store.put("art-001", payload, media_type="text/plain")
    assert stored.artifact_id == "art-001"
    assert stored.size == len(payload)
    assert stored.media_type == "text/plain"

    # 2. Exists
    assert store.exists("art-001") is True
    assert store.exists("art-999") is False

    # 3. Get
    retrieved = store.get("art-001")
    assert retrieved == payload

    # 4. List
    objs = store.list_objects()
    assert any(o["key"] == "art-001" and o["size"] == len(payload) for o in objs)

    # 5. Delete
    deleted = store.delete("art-001")
    assert deleted is True
    assert store.exists("art-001") is False

    with pytest.raises(NotFound):
        store.get("art-001")


def test_s3_presigned_url_expiration_and_security(s3_settings):
    """F2.3: Pre-signed URLs are time-bounded; TTL > 900s is rejected; expired URLs fail."""
    store = S3ArtifactStore(s3_settings)
    store.put("art-timed", b"temporary secret data")

    # 1. Reject TTL > 15 minutes (900 seconds)
    with pytest.raises(ValidationFailed) as exc_info:
        store.presigned_get("art-timed", expires_in_seconds=901)
    assert exc_info.value.code == "bad_ttl"

    # 2. Reject nonexistent artifact
    with pytest.raises(NotFound):
        store.presigned_get("art-nonexistent", expires_in_seconds=300)

    # 3. Valid presigned GET URL can be accessed anonymously without headers
    url = store.presigned_get("art-timed", expires_in_seconds=300)
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        assert resp.read() == b"temporary secret data"

    # 4. Short-lived URL expires and is rejected with 403
    short_url = store.presigned_get("art-timed", expires_in_seconds=1)
    time.sleep(2)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(short_url)
    assert exc_info.value.code == 403


def test_s3_backup_restore_and_tombstone_replay(s3_settings, tmp_path):
    """F2.4 & O04: Snapshot backup and restore with tombstone replay prevents reviving deleted items."""
    store = S3ArtifactStore(s3_settings)

    # Setup 3 artifacts
    store.put("art-alpha", b"Alpha content")
    store.put("art-beta", b"Beta content")
    store.put("art-gamma-deleted", b"Gamma content to be tombstoned")

    backup_dir = tmp_path / "backup_dest"
    snap = store.backup_snapshot(str(backup_dir))
    assert snap["count"] >= 3

    # Now mark art-gamma-deleted as deleted/tombstoned in our source
    store.delete("art-gamma-deleted")

    # Clean bucket
    store.delete("art-alpha")
    store.delete("art-beta")
    assert store.exists("art-alpha") is False
    assert store.exists("art-beta") is False
    assert store.exists("art-gamma-deleted") is False

    # Restore snapshot with tombstone replay: art-gamma-deleted MUST NOT be revived!
    tombstones = {"art-gamma-deleted"}
    res = store.restore_snapshot(str(backup_dir), tombstones=tombstones)
    assert res["skipped_tombstones"] == 1
    assert res["restored"] >= 2

    # Verify active artifacts are restored
    assert store.get("art-alpha") == b"Alpha content"
    assert store.get("art-beta") == b"Beta content"

    # Verify tombstoned artifact is NOT revived
    assert store.exists("art-gamma-deleted") is False
    with pytest.raises(NotFound):
        store.get("art-gamma-deleted")


def test_artifact_path_traversal_rejected(s3_settings):
    """F2.5: Path traversal keys are rejected by both stores."""
    s3_store = S3ArtifactStore(s3_settings)
    with pytest.raises(ValidationFailed) as exc_info:
        s3_store.put("../sneaky", b"bad")
    assert exc_info.value.code == "bad_artifact_id"

    with pytest.raises(ValidationFailed):
        s3_store.get("sub/dir/key")

    local_store = LocalArtifactStore(".runtime/test_art")
    with pytest.raises(ValidationFailed):
        local_store.put("..\\sneaky", b"bad")
