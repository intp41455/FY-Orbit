"""Anchor store: FileAnchorStore / S3AnchorStore / NullAnchorStore behaviour.

Covers the append-only guarantee, the FY_ANCHOR_DIR override, and the S3 store
against the project's own S3-compatible service (so S3AnchorStore is exercised
for real rather than shipped untested).
"""

import json
import socket
import threading
import time
import urllib.request

import pytest
import uvicorn

from find_yourself.adapters.s3_service import create_s3_app
from find_yourself.db.types import utcnow
from find_yourself.services.anchor_store import (
    AnchorRecord,
    FileAnchorStore,
    NullAnchorStore,
    S3AnchorStore,
    anchor_key,
)


def _record(seq: int, head: str | None = None) -> AnchorRecord:
    return AnchorRecord(
        seq=seq,
        head_hash=head or (str(seq) * 64),
        storage="file:/tmp/anchors",
        evidence={"note": "test"},
        created_at=utcnow(),
    )


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------------
# AnchorRecord
# --------------------------------------------------------------------------

def test_anchor_record_roundtrips_through_json():
    rec = _record(7)
    back = AnchorRecord.from_json(rec.to_json())
    assert back.seq == rec.seq
    assert back.head_hash == rec.head_hash
    assert back.storage == rec.storage
    assert back.evidence == rec.evidence
    assert back.created_at == rec.created_at


def test_anchor_key_is_zero_padded_and_sortable():
    assert anchor_key(1) < anchor_key(2) < anchor_key(123)
    assert anchor_key(123) == "anchor-000000000123.json"


# --------------------------------------------------------------------------
# FileAnchorStore
# --------------------------------------------------------------------------

def test_file_store_put_and_latest(tmp_path):
    store = FileAnchorStore(tmp_path / "anchors")
    assert store.latest() is None
    store.put(_record(1))
    store.put(_record(2))
    latest = store.latest()
    assert latest is not None
    assert latest.seq == 2


def test_file_store_is_append_only(tmp_path):
    """Re-anchoring an existing seq must fail loudly, never overwrite history."""
    store = FileAnchorStore(tmp_path / "anchors")
    store.put(_record(1, head="a" * 64))
    with pytest.raises(FileExistsError):
        store.put(_record(1, head="b" * 64))
    # Original content intact.
    assert json.loads((store.directory / anchor_key(1)).read_text(encoding="utf-8"))["head_hash"] == "a" * 64


def test_file_store_skips_corrupt_files(tmp_path):
    store = FileAnchorStore(tmp_path / "anchors")
    store.put(_record(5))
    (store.directory / anchor_key(9)).write_text("{not json", encoding="utf-8")
    latest = store.latest()
    assert latest is not None and latest.seq == 5


def test_file_store_honours_fy_anchor_dir_env(tmp_path, monkeypatch):
    target = tmp_path / "from-env"
    monkeypatch.setenv("FY_ANCHOR_DIR", str(target))
    store = FileAnchorStore()
    assert store.directory == target.resolve()
    store.put(_record(1))
    assert (target / anchor_key(1)).exists()


def test_file_store_location_points_outside_primary_db(tmp_path):
    store = FileAnchorStore(tmp_path / "anchors")
    assert store.location().startswith("file:")
    assert ".runtime" not in store.location()


# --------------------------------------------------------------------------
# NullAnchorStore
# --------------------------------------------------------------------------

def test_null_store_discards(tmp_path):
    store = NullAnchorStore()
    store.put(_record(1))
    assert store.latest() is None
    assert store.location() == "null:disabled"


# --------------------------------------------------------------------------
# S3AnchorStore (against the project's own S3 service)
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def local_s3_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("anchor_s3_data")
    port = _free_port()
    app = create_s3_app(
        storage_dir=str(data_dir),
        access_key="fy-anchor-key",
        secret_key="fy-anchor-secret-at-least-32-chars-long",
        region="us-east-1",
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
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
        "endpoint": endpoint, "bucket": "audit-anchors",
        "access_key": "fy-anchor-key", "secret_key": "fy-anchor-secret-at-least-32-chars-long",
    }
    server.should_exit = True
    t.join(timeout=2)


@pytest.fixture()
def s3_store(local_s3_server):
    return S3AnchorStore(
        local_s3_server["bucket"],
        endpoint_url=local_s3_server["endpoint"],
        access_key=local_s3_server["access_key"],
        secret_key=local_s3_server["secret_key"],
    )


def test_s3_store_put_and_latest_roundtrip(s3_store):
    assert s3_store.latest() is None
    rec = _record(3)
    s3_store.put(rec)
    back = s3_store.latest()
    assert back is not None
    assert back.seq == 3
    assert back.head_hash == rec.head_hash


def test_s3_store_location_format(s3_store):
    assert s3_store.location() == "s3:audit-anchors/anchors"


def test_s3_store_is_append_only(s3_store):
    s3_store.put(_record(4, head="a" * 64))
    with pytest.raises(FileExistsError):
        s3_store.put(_record(4, head="b" * 64))
    assert s3_store.latest().head_hash == "a" * 64
