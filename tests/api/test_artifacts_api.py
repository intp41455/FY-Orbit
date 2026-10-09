"""API tests for catalog artifacts: get, presigned, and deleted state."""

from fastapi.testclient import TestClient
from helpers import login_owner

from find_yourself.adapters.artifacts import build_artifact_store
from find_yourself.contracts import uid
from find_yourself.db.models import Artifact, utcnow


def test_artifact_api_get_and_presigned(client: TestClient, session_maker, settings):
    headers = login_owner(client)
    art_id = uid()

    # Store content in artifact store
    store = build_artifact_store(settings)
    store.put(art_id, b"Content for artifact api test", media_type="text/plain")

    # Record in database
    s = session_maker()
    art = Artifact(
        id=art_id,
        domain="personal",
        sha256="fake-hash",
        size=len(b"Content for artifact api test"),
        media_type="text/plain",
        verified=True,
    )
    s.add(art)
    s.commit()
    s.close()

    # 1. GET /api/artifacts/{id}
    r = client.get(f"/api/artifacts/{art_id}", headers=headers)
    assert r.status_code == 200
    assert r.content == b"Content for artifact api test"

    # 2. GET /api/artifacts/{id}/presigned
    r_pre = client.get(f"/api/artifacts/{art_id}/presigned?expires_in=300", headers=headers)
    assert r_pre.status_code == 200
    body = r_pre.json()
    assert body["artifact_id"] == art_id
    assert "url" in body

    # 3. Soft-delete artifact
    s = session_maker()
    art = s.get(Artifact, art_id)
    art.deleted_at = utcnow()
    s.commit()
    s.close()

    # Both endpoints now return 404
    r_del = client.get(f"/api/artifacts/{art_id}", headers=headers)
    assert r_del.status_code == 404

    r_pre_del = client.get(f"/api/artifacts/{art_id}/presigned", headers=headers)
    assert r_pre_del.status_code == 404
