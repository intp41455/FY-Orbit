"""P1 基座质保 HTTP 契约测试（A-基座质保-10/11/13）。

服务层行为已在 ``tests/unit/test_quality_*.py`` 覆盖；本文件只测 HTTP 层：
认证 / CSRF / body 不能提权 / 错误信封 / 真实审计链的筛选与导出 / 门禁判定与豁免流程 /
openapi 约定式自动发现。前端 ``useAutosave`` / ``SaveStatusIndicator``（-12/-14）
由 vitest 覆盖，不在本文件重复。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner

from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService

OWNER_ID = "owner"          # dev-token 的 owner_id 就是 "owner"

DECLARED_PAGE = """
import { useBase } from '../../hooks/useAutosave';
export function DeclaredPage() {
  const base = useBase({ surface: 'workbench' });
  return <section>{base.surface}</section>;
}
"""

EDITABLE_PAGE = """
export function EditablePage() {
  return <textarea onChange={(e) => console.log(e.target.value)} />;
}
"""

DISPLAY_PAGE = """
export function DisplayPage() {
  return <article>只读简报</article>;
}
"""

DISPLAY_COMP = """
export function DisplayComp() {
  return <span>badge</span>;
}
"""


@pytest.fixture()
def auth(client: TestClient) -> dict[str, str]:
    return login_owner(client)


@pytest.fixture()
def isolated(tmp_path, monkeypatch) -> Path:
    """导出包 / 豁免清单 / 扫描根都指到 tmp：不脏仓库 ``.runtime``，也不依赖真实页面树。"""
    monkeypatch.setenv("FY_QUALITY_DIR", str(tmp_path / "quality"))
    monkeypatch.setenv("FY_BASE_EXEMPTIONS", str(tmp_path / "exemptions.json"))
    monkeypatch.setenv("FY_BASE_SCAN_ROOT", str(tmp_path))

    pages = tmp_path / "web" / "src" / "pages"
    comps = tmp_path / "web" / "src" / "components"
    pages.mkdir(parents=True)
    comps.mkdir(parents=True)
    (pages / "DeclaredPage.tsx").write_text(DECLARED_PAGE, encoding="utf-8")
    (pages / "EditablePage.tsx").write_text(EDITABLE_PAGE, encoding="utf-8")
    (pages / "DisplayPage.tsx").write_text(DISPLAY_PAGE, encoding="utf-8")
    (comps / "DisplayComp.tsx").write_text(DISPLAY_COMP, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def seeded(session_maker):
    """在真实审计链上落五级样本（时间维是 seq，所以这里只关心定序）。"""
    session = session_maker()
    try:
        audit = AuditService(session)
        actor = Actor.owner(OWNER_ID)
        audit.append(actor, "task.updated", "proj-alpha:t1",
                     {"surface": "workbench", "project": "proj-alpha"})
        audit.append(actor, "task.save_failed", "proj-alpha:t1",
                     {"surface": "workbench", "project": "proj-alpha",
                      "api_key": "sk-should-never-leak"})
        audit.append(actor, "sync.retry_scheduled", "proj-beta:t2",
                     {"surface": "chat", "project": "proj-beta"})
        audit.append(actor, "debug.probe", "proj-beta:t2",
                     {"surface": "chat", "project": "proj-beta"})
        audit.append(actor, "list.viewed", "proj-beta:t2",
                     {"surface": "chat", "project": "proj-beta"})
        session.commit()
    finally:
        session.close()
    return 5


# --------------------------------------------------------------------------- #
# 认证 / CSRF
# --------------------------------------------------------------------------- #
def test_quality_reads_require_authentication(client: TestClient):
    assert client.get("/api/quality/logs/levels").status_code == 401
    assert client.get("/api/quality/logs").status_code == 401
    assert client.get("/api/quality/performance-budget").status_code == 401
    assert client.get("/api/quality/base-contract").status_code == 401
    assert client.get("/api/quality/storage").status_code == 401


def test_export_and_exemption_mutations_require_csrf(client: TestClient, isolated):
    login_owner(client)
    assert client.post("/api/quality/logs/export", json={}).status_code == 403
    assert client.post("/api/quality/base-contract/exemptions", json={
        "path": "web/src/pages/DisplayPage.tsx", "reason": "纯展示页：只渲染简报，无可编辑控件",
    }).status_code == 403
    assert client.post("/api/quality/base-contract/exemptions/approve",
                       json={"path": "x"}).status_code == 403


# --------------------------------------------------------------------------- #
# A-基座质保-10 · 日志分级与可导出
# --------------------------------------------------------------------------- #
def test_levels_endpoint_lists_the_five_levels(client: TestClient, auth):
    body = client.get("/api/quality/logs/levels", headers=auth).json()
    assert [lv["id"] for lv in body["levels"]] == ["error", "warning", "info", "debug", "change"]
    assert body["configurable"] is True
    assert body["time_axis"] == "seq"


def test_logs_query_supports_the_five_filters_over_http(client: TestClient, auth, seeded):
    # 登录本身也会往审计链写帧，所以总数用「>= 本次种下的 5 条」断言，
    # 其余维度一律用 project / surface 把本次样本圈出来（不依赖绝对 seq）。
    base = client.get("/api/quality/logs", headers=auth).json()
    assert base["total"] >= seeded

    assert client.get("/api/quality/logs", headers=auth,
                      params={"surface": "workbench"}).json()["total"] == 2
    assert client.get("/api/quality/logs", headers=auth,
                      params={"project": "proj-alpha"}).json()["total"] == 2
    assert client.get("/api/quality/logs", headers=auth,
                      params={"project": "proj-beta"}).json()["total"] == 3
    assert client.get("/api/quality/logs", headers=auth,
                      params={"actor": "someone-else"}).json()["total"] == 0

    seqs = [i["seq"] for i in base["items"]]
    window = client.get("/api/quality/logs", headers=auth,
                        params={"since_seq": seqs[1], "until_seq": seqs[2],
                                "limit": 100}).json()
    assert [i["seq"] for i in window["items"]] == seqs[1:3]

    err = client.get("/api/quality/logs", headers=auth,
                     params={"level": "error", "project": "proj-alpha"}).json()
    assert err["total"] == 1
    assert err["items"][0]["details"]["api_key"] == "[已脱敏]"
    assert err["items"][0]["at"] is None      # 不编造墙钟时间
    assert err["time_axis"] == "seq"


def test_logs_query_bounds_are_enforced_over_http(client: TestClient, auth):
    assert client.get("/api/quality/logs", headers=auth,
                      params={"limit": 0}).status_code == 422
    assert client.get("/api/quality/logs", headers=auth,
                      params={"limit": 501}).status_code == 422
    assert client.get("/api/quality/logs", headers=auth,
                      params={"level": "fatal"}).status_code == 422


def test_export_over_http_writes_a_redacted_previewable_package(client: TestClient, auth,
                                                                isolated, seeded):
    body = client.post("/api/quality/logs/export",
                       json={"level": "error", "preview": True}, headers=auth).json()
    assert body["contains_credentials"] is False
    assert body["log_count"] == 1
    assert body["preview"]["sample"][0]["details"]["api_key"] == "[已脱敏]"

    logs = Path(body["directory"]) / "logs.json"
    assert "sk-should-never-leak" not in logs.read_text(encoding="utf-8")

    listing = client.get("/api/quality/logs/exports", headers=auth).json()
    assert [m["export_id"] for m in listing["items"]] == [body["export_id"]]

    detail = client.get(f"/api/quality/logs/exports/{body['export_id']}",
                        headers=auth).json()
    assert detail["manifest"]["export_id"] == body["export_id"]
    assert detail["logs"]["count"] == 1

    assert client.get("/api/quality/logs/exports/deadbeef", headers=auth).status_code == 404


def test_export_body_rejects_unknown_fields(client: TestClient, auth, isolated):
    assert client.post("/api/quality/logs/export",
                       json={"owner_id": "someone-else"}, headers=auth).status_code == 422


def test_savepoints_endpoint_reads_real_work_stashes(client: TestClient, auth, session_maker):
    from find_yourself.db.staging_models import WorkStash

    session = session_maker()
    try:
        session.add(WorkStash(
            id="stash-1", owner_id=OWNER_ID, title="写前快照", content="draft",
            stash_metadata={"kind": "pre_write_snapshot", "snapshot_id": "snap-1", "files": 2},
        ))
        session.add(WorkStash(
            id="stash-2", owner_id=OWNER_ID, title="手动暂存", content="x",
            stash_metadata={"kind": "manual_note"},
        ))
        session.commit()
    finally:
        session.close()

    body = client.get("/api/quality/logs/savepoints", headers=auth).json()
    assert body["total"] == 1
    assert body["time_axis"] == "wallclock"
    assert body["items"][0]["snapshot_id"] == "snap-1"


# --------------------------------------------------------------------------- #
# A-基座质保-13 · 性能预算门禁
# --------------------------------------------------------------------------- #
GOOD = {
    "realtime_save_p95_ms": 38,
    "recover_gap_ms": 240,
    "conflict_compare_ms": 150,
    "log_filter_p95_ms": 90,
    "import_export_block_ms": 60,
}


def test_budget_tiers_limits_and_self_test_plan(client: TestClient, auth):
    tiers = client.get("/api/quality/performance-budget/tiers", headers=auth).json()
    assert tiers["default_tier"] == "standard"
    assert [t["id"] for t in tiers["tiers"]] == ["low", "standard", "high"]

    limits = client.get("/api/quality/performance-budget/limits", headers=auth,
                        params={"tier": "low"}).json()
    assert limits["limits"]["realtime_save_p95_ms"]["limit"] == 100

    plan = client.get("/api/quality/performance-budget/self-test", headers=auth).json()
    assert len(plan["steps"]) == 5
    assert all(s["how"] for s in plan["steps"])
    assert "self_test" in plan["local_script"]

    overview = client.get("/api/quality/performance-budget", headers=auth).json()
    assert overview["baseline_version"] == "1.0.0"
    assert [m["metric"] for m in overview["metrics"]] == [
        "realtime_save_p95_ms", "recover_gap_ms", "conflict_compare_ms",
        "log_filter_p95_ms", "import_export_block_ms",
    ]


def test_budget_evaluate_passes_and_reports_ci_exit_zero(client: TestClient, auth):
    body = client.post("/api/quality/performance-budget/evaluate",
                       json={"samples": GOOD}, headers=auth).json()
    assert body["ok"] is True
    assert body["ci"]["exit_code"] == 0
    assert body["failures"] == []


def test_budget_over_limit_fails_with_metric_and_threshold(client: TestClient, auth):
    body = client.post("/api/quality/performance-budget/evaluate",
                       json={"tier": "standard",
                             "samples": {**GOOD, "log_filter_p95_ms": 900}},
                       headers=auth).json()
    assert body["ok"] is False
    assert body["ci"]["exit_code"] == 1
    fail = body["failures"][0]
    assert fail["metric"] == "log_filter_p95_ms"
    assert fail["observed"] == 900 and fail["limit"] == 150
    assert "900" in body["ci"]["reason"] and "150" in body["ci"]["reason"]


def test_budget_missing_sample_fails_over_http(client: TestClient, auth):
    partial = {k: v for k, v in GOOD.items() if k != "recover_gap_ms"}
    body = client.post("/api/quality/performance-budget/evaluate",
                       json={"samples": partial}, headers=auth).json()
    assert body["ok"] is False
    assert "缺少样本" in body["summary"]


def test_budget_unknown_tier_and_bad_body_are_rejected(client: TestClient, auth):
    assert client.post("/api/quality/performance-budget/evaluate",
                       json={"tier": "quantum", "samples": GOOD},
                       headers=auth).status_code == 422
    assert client.post("/api/quality/performance-budget/evaluate",
                       json={"samples": GOOD, "owner_id": "x"},
                       headers=auth).status_code == 422


# --------------------------------------------------------------------------- #
# A-基座质保-11 · 基座接入校验与豁免
# --------------------------------------------------------------------------- #
def test_base_contract_endpoint_self_describes(client: TestClient, auth):
    body = client.get("/api/quality/base-contract", headers=auth).json()
    assert body["contract_version"] == "1.0.0"
    assert [c["id"] for c in body["capabilities"]] == [
        "realtime_save", "audit_trail", "local_first", "error_receipt",
    ]
    assert body["declaration"]["markers"] == ["useBase(", "<BaseBound"]
    assert body["audit_endpoint"].endswith("/base-contract/audit")


def test_base_audit_blocks_and_names_the_missing_capabilities(client: TestClient, auth, isolated):
    body = client.get("/api/quality/base-contract/audit", headers=auth).json()
    assert body["ok"] is False
    assert body["ci"]["exit_code"] == 1
    kinds = {v["path"]: v["kind"] for v in body["violations"]}
    assert kinds["web/src/pages/EditablePage.tsx"] == "base_not_declared"
    assert kinds["web/src/pages/DisplayPage.tsx"] == "base_not_declared"
    assert body["declared"] == 1                       # DeclaredPage 简写声明四项
    assert [a["path"] for a in body["advisories"]] == ["web/src/components/DisplayComp.tsx"]
    for item in body["violations"]:
        assert item["missing_capabilities"]
        assert {h["capability"] for h in item["how_to_fix"]} == {
            "realtime_save", "audit_trail", "local_first", "error_receipt"}

    pages_only = client.get("/api/quality/base-contract/audit", headers=auth,
                            params={"include_components": False}).json()
    assert pages_only["scanned_dirs"] == ["web/src/pages"]
    assert pages_only["advisories"] == []


def test_base_exemption_flow_over_http(client: TestClient, auth, isolated):
    assert client.get("/api/quality/base-contract/exemptions", headers=auth).json()["total"] == 0

    requested = client.post("/api/quality/base-contract/exemptions", headers=auth, json={
        "path": "web/src/pages/DisplayPage.tsx",
        "reason": "纯展示页：只渲染简报，确认没有任何可编辑控件",
    }).json()
    assert requested["exemption"]["status"] == "pending"

    # 未审批：仍然拦
    pending = client.get("/api/quality/base-contract/audit", headers=auth).json()
    kinds = {v["path"]: v["kind"] for v in pending["violations"]}
    assert kinds["web/src/pages/DisplayPage.tsx"] == "exemption_pending"

    approved = client.post("/api/quality/base-contract/exemptions/approve", headers=auth,
                           json={"path": "web/src/pages/DisplayPage.tsx",
                                 "note": "已核对只读"}).json()
    assert approved["exemption"]["status"] == "approved"

    after = client.get("/api/quality/base-contract/audit", headers=auth).json()
    paths = {v["path"] for v in after["violations"]}
    assert "web/src/pages/DisplayPage.tsx" not in paths
    assert after["exempted"] == 1
    listing = client.get("/api/quality/base-contract/exemptions", headers=auth).json()
    assert listing["approved"] == ["web/src/pages/DisplayPage.tsx"]
    assert listing["pending"] == []

    client.post("/api/quality/base-contract/exemptions/revoke", headers=auth,
                json={"path": "web/src/pages/DisplayPage.tsx"})
    back = client.get("/api/quality/base-contract/audit", headers=auth).json()
    assert "web/src/pages/DisplayPage.tsx" in {v["path"] for v in back["violations"]}


def test_base_exemption_rules_over_http(client: TestClient, auth, isolated):
    editable = client.post("/api/quality/base-contract/exemptions", headers=auth, json={
        "path": "web/src/pages/EditablePage.tsx",
        "reason": "这里确实有输入框，但我就是想绕过门禁试试看",
    })
    assert editable.status_code == 422
    assert editable.json()["error"]["code"] == "exemption_not_applicable"

    short = client.post("/api/quality/base-contract/exemptions", headers=auth, json={
        "path": "web/src/pages/DisplayPage.tsx", "reason": "只读",
    })
    assert short.status_code == 422
    assert short.json()["error"]["code"] == "exemption_reason_missing"

    unknown_scope = client.post("/api/quality/base-contract/exemptions", headers=auth, json={
        "path": "web/src/pages/DisplayPage.tsx",
        "reason": "纯展示页：只渲染简报，确认没有任何可编辑控件",
        "scope": "everything",
    })
    assert unknown_scope.status_code == 422
    assert unknown_scope.json()["error"]["code"] == "exemption_scope_invalid"


def test_storage_endpoint_points_at_the_quality_dir(client: TestClient, auth, isolated):
    body = client.get("/api/quality/storage", headers=auth).json()
    assert body["quality_dir"] == str(isolated / "quality")
    assert body["endpoints"]["export"] == "/api/quality/logs/export"


# --------------------------------------------------------------------------- #
# openapi 形状
# --------------------------------------------------------------------------- #
def test_quality_paths_are_auto_discovered(app):
    paths = app.openapi()["paths"]
    for expected in (
        "/api/quality/logs/levels",
        "/api/quality/logs",
        "/api/quality/logs/savepoints",
        "/api/quality/logs/export",
        "/api/quality/logs/exports",
        "/api/quality/logs/exports/{export_id}",
        "/api/quality/performance-budget",
        "/api/quality/performance-budget/tiers",
        "/api/quality/performance-budget/limits",
        "/api/quality/performance-budget/self-test",
        "/api/quality/performance-budget/evaluate",
        "/api/quality/base-contract",
        "/api/quality/base-contract/audit",
        "/api/quality/base-contract/exemptions",
        "/api/quality/base-contract/exemptions/approve",
        "/api/quality/base-contract/exemptions/revoke",
        "/api/quality/storage",
    ):
        assert expected in paths, f"missing {expected}"


def test_no_quality_path_ends_with_plan(app):
    offenders = [p for p in app.openapi()["paths"] if p.endswith("/plan")]
    assert offenders == []
