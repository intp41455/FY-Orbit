"""API integration tests for Profiles (04 个人与对象多维画像)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner


def test_profiles_api_lifecycle(client: TestClient) -> None:
    headers = login_owner(client)

    # 1. Create a Self Subject
    r_subj = client.post(
        "/api/profiles/subjects",
        json={"label": "本人档案", "kind": "self", "description": "核心本人画像"},
        headers=headers,
    )
    assert r_subj.status_code == 201
    subj = r_subj.json()
    assert subj["kind"] == "self"
    assert subj["confirmed"] is True
    subj_id = subj["id"]

    # 2. List subjects
    r_list = client.get("/api/profiles/subjects", headers=headers)
    assert r_list.status_code == 200
    assert any(s["id"] == subj_id for s in r_list.json()["items"])

    # 3. Import document with conversation
    chat_text = """
    Alice: 我主张采用严格的不可变审计日志与防御性架构。
    Bob: 这能有效防止数据泄露。
    Alice: 无论外部压力多大，绝不能关闭安全栅栏。
    """
    r_imp = client.post(
        "/api/profiles/imports",
        json={
            "content": chat_text,
            "filename": "discussion.txt",
            "subject_id": subj_id,
            "privacy_domain": "personal",
        },
        headers=headers,
    )
    assert r_imp.status_code == 201
    imp = r_imp.json()
    assert imp["status"] == "parsed"
    assert imp["subject_id"] == subj_id
    import_id = imp["id"]

    # 3b. Confirm Alice as self
    r_conf = client.post(
        f"/api/profiles/imports/{import_id}/confirm_speakers",
        json={"mappings": {"Alice": "self"}},
        headers=headers,
    )
    assert r_conf.status_code == 200

    # 4. Run profiling synthesis
    r_run = client.post(
        f"/api/profiles/{subj_id}/runs",
        json={"rule_version": "v1.0"},
        headers=headers,
    )
    assert r_run.status_code == 201
    rev = r_run.json()
    assert rev["subject_id"] == subj_id
    assert rev["revision"] >= 1
    assert "clusters" in rev
    assert len(rev["clusters"]) >= 1
    assert "metrics" in rev
    assert "limitations" in rev

    # 5. List and get revisions
    r_revs = client.get(f"/api/profiles/{subj_id}/revisions", headers=headers)
    assert r_revs.status_code == 200
    assert len(r_revs.json()["items"]) >= 1

    r_rev_detail = client.get(f"/api/profiles/revisions/{rev['id']}", headers=headers)
    assert r_rev_detail.status_code == 200
    assert r_rev_detail.json()["id"] == rev["id"]

    # 6. Submit evidence feedback
    all_nodes = [node for cl in rev["clusters"] for node in cl["nodes"]]
    evidence_id = all_nodes[0]["evidence_refs"][0]
    r_fb = client.post(
        f"/api/profiles/evidence/{evidence_id}/feedback",
        json={"action": "accept", "feedback_text": "确为本人的核心价值观表达"},
        headers=headers,
    )
    assert r_fb.status_code == 200
    assert r_fb.json()["action"] == "accept"

    # 7. Delete import and verify cascading tombstone & redaction
    r_del = client.delete(f"/api/profiles/imports/{import_id}", headers=headers)
    assert r_del.status_code == 204

    # 8. Revisions list defaults to hiding invalidated revisions
    r_revs_after = client.get(f"/api/profiles/{subj_id}/revisions", headers=headers)
    assert r_revs_after.status_code == 200
    assert len(r_revs_after.json()["items"]) == 0

    # Revisions detail defaults to 404 for invalidated
    r_detail_after = client.get(f"/api/profiles/revisions/{rev['id']}", headers=headers)
    assert r_detail_after.status_code == 404

    # Revisions detail with allow_invalidated=true returns scrubbed text
    r_detail_allow = client.get(f"/api/profiles/revisions/{rev['id']}?allow_invalidated=true", headers=headers)
    assert r_detail_allow.status_code == 200
    detail_data = r_detail_allow.json()
    assert "REDACTED" in detail_data["core_summary"]["summary"]
    for cl in detail_data["clusters"]:
        for n in cl["nodes"]:
            assert "REDACTED" in n["description"]


def test_profiles_api_csrf_enforcement(client: TestClient) -> None:
    # Attempting to post without CSRF header should fail with 403
    login_owner(client)
    r = client.post(
        "/api/profiles/subjects",
        json={"label": "未授权请求", "kind": "project"},
    )
    assert r.status_code == 403
