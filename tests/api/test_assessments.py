"""U05/U06: server-side assessment scoring — incomplete no default result, reverse items, version binding."""

from __future__ import annotations

from fastapi.testclient import TestClient

from helpers import login_owner


def _all_answers(q: str, value: int) -> dict:
    from find_yourself.adapters.assessments import CATALOG
    return {i.id: value for i in CATALOG[q].items}


def test_assessment_catalog_lists_synthetic_only(client: TestClient):
    headers = login_owner(client)
    r = client.get("/api/assessments/catalog", headers=headers)
    assert r.status_code == 200
    for q in r.json()["questionnaires"]:
        assert q["synthetic"] is True
        assert q["license"] != "official-ipip"


def test_u05_incomplete_submit_produces_no_default_result(client: TestClient):
    headers = login_owner(client)
    s = client.post("/api/assessments/bigfive-synthetic/sessions", headers=headers).json()
    # Record only one answer, then submit: must stay incomplete, no result.
    client.post(f"/api/assessments/sessions/{s['session_id']}/answers",
                json={"answers": {"ope1": 4}}, headers=headers)
    r = client.post(f"/api/assessments/sessions/{s['session_id']}/submit", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "incomplete"
    assert body["result"] is None
    assert len(body["missing"]) > 0


def test_u06_complete_submit_scores_and_binds_version(client: TestClient):
    headers = login_owner(client)
    s = client.post("/api/assessments/bigfive-synthetic/sessions", headers=headers).json()
    answers = _all_answers("bigfive-synthetic", 3)
    client.post(f"/api/assessments/sessions/{s['session_id']}/answers",
                json={"answers": answers}, headers=headers)
    r = client.post(f"/api/assessments/sessions/{s['session_id']}/submit", headers=headers)
    body = r.json()
    assert body["status"] == "scored"
    result = body["result"]
    assert result["questionnaire_version"] == "0.1.0-synthetic"
    assert result["item_set_hash"]
    assert result["norms"] is None
    assert result["clinical"] is False
    assert result["synthetic"] is True


def test_u06_reverse_item_flips_score(client: TestClient):
    """All 5s on normal items and all 1s on reverse items must both average 5."""
    headers = login_owner(client)
    s = client.post("/api/assessments/bigfive-synthetic/sessions", headers=headers).json()
    from find_yourself.adapters.assessments import CATALOG
    answers = {}
    for i in CATALOG["bigfive-synthetic"].items:
        answers[i.id] = 1 if i.direction < 0 else 5  # reverse=1 -> flipped to 5
    client.post(f"/api/assessments/sessions/{s['session_id']}/answers",
                json={"answers": answers}, headers=headers)
    r = client.post(f"/api/assessments/sessions/{s['session_id']}/submit", headers=headers)
    profile = r.json()["result"]["profile"]
    for dim, avg in profile.items():
        assert avg == 5.0, f"{dim}={avg} expected 5.0 after reverse flip"
