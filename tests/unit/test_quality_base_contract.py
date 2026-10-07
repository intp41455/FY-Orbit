"""P1 基座接入强制校验（A-基座质保-11 / W1）单元测试。

需求四条验收逐条对应：

① 统一接入声明/钩子（``useBase()`` / ``BaseBound``）；未接入则**校验失败**
   —— 断言 ``ok is False`` 且 ``ci.exit_code == 1``（门禁必须能真的拦下来）。
② 覆盖页面与组件 —— 断言扫描面同时命中 ``pages`` 与 ``components``。
③ 豁免必须**写明理由**且**经审批**才生效；可编辑文件不得豁免。
④ 失败信息必带「缺哪个能力 + 如何接入」——逐条断言 ``how_to_fix`` 四项齐全。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.errors import NotFound, PermissionDenied, ValidationFailed
from find_yourself.services.quality.base_contract import (
    BASE_CAPABILITY_IDS,
    BaseContractService,
    base_contract,
    declared_capabilities,
    declared_surface,
    is_declared,
    is_editable,
)

DECLARED_PAGE = """
import { useBase } from '../../hooks/useAutosave';
export function DeclaredPage() {
  const base = useBase({ surface: 'workbench' });
  return <section>{base.surface}</section>;
}
"""

DECLARED_WITH_LIST = """
import { useBase } from '../../hooks/useAutosave';
export function DeclaredComp() {
  const base = useBase({ surface: 'chat', capabilities: [
    'realtime_save', 'audit_trail', 'local_first', 'error_receipt',
  ] });
  return <div>{base.surface}</div>;
}
"""

PARTIAL_PAGE = """
import { useBase } from '../../hooks/useAutosave';
export function PartialPage() {
  const base = useBase({ capabilities: ['realtime_save'] });
  return <div>{base.ready}</div>;
}
"""

UNKNOWN_CAP_PAGE = """
import { useBase } from '../../hooks/useAutosave';
export function UnknownCapPage() {
  const base = useBase({ capabilities: [
    'realtime_save', 'audit_trail', 'local_first', 'error_receipt', 'telepathy',
  ] });
  return <div>{base.ready}</div>;
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

BOUND_COMP = """
import { BaseBound } from '../ui/SaveStatusIndicator';
export function BoundComp() {
  return <BaseBound surface="kanban-card"><p>card</p></BaseBound>;
}
"""

EDITABLE_COMP = """
export function EditableComp() {
  return <input value="x" onChange={() => {}} />;
}
"""

DISPLAY_COMP = """
export function DisplayComp() {
  return <span>badge</span>;
}
"""


@pytest.fixture()
def tree(tmp_path) -> Path:
    pages = tmp_path / "pages"
    comps = tmp_path / "components"
    (comps / "ui").mkdir(parents=True)
    pages.mkdir()
    (pages / "DeclaredPage.tsx").write_text(DECLARED_PAGE, encoding="utf-8")
    (pages / "PartialPage.tsx").write_text(PARTIAL_PAGE, encoding="utf-8")
    (pages / "UnknownCapPage.tsx").write_text(UNKNOWN_CAP_PAGE, encoding="utf-8")
    (pages / "EditablePage.tsx").write_text(EDITABLE_PAGE, encoding="utf-8")
    (pages / "DisplayPage.tsx").write_text(DISPLAY_PAGE, encoding="utf-8")
    (pages / "DisplayPage.test.tsx").write_text(DISPLAY_PAGE, encoding="utf-8")
    (comps / "DeclaredComp.tsx").write_text(DECLARED_WITH_LIST, encoding="utf-8")
    (comps / "BoundComp.tsx").write_text(BOUND_COMP, encoding="utf-8")
    (comps / "EditableComp.tsx").write_text(EDITABLE_COMP, encoding="utf-8")
    (comps / "DisplayComp.tsx").write_text(DISPLAY_COMP, encoding="utf-8")
    (comps / "ui" / "Primitive.tsx").write_text(EDITABLE_PAGE, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def svc(session, audit, tree, tmp_path):
    return BaseContractService(session, audit=audit, root=tree,
                               exemptions_path=tmp_path / "base_exemptions.json")


def _audit(svc, owner) -> dict:
    return svc.audit_tree(owner, dirs=["pages", "components"])


def _kinds(result) -> dict[str, str]:
    return {v["path"]: v["kind"] for v in result["violations"]}


# --------------------------------------------------------------------------- #
# 纯函数：声明解析
# --------------------------------------------------------------------------- #
def test_declaration_and_editable_detection():
    assert is_declared(DECLARED_PAGE) is True
    assert is_declared("<BaseBound surface='x'>") is True
    assert is_declared(DISPLAY_PAGE) is False
    assert is_editable(EDITABLE_PAGE) is True
    assert is_editable("<input value={x} />") is True
    assert is_editable("const v = useAutosave({});") is True
    assert is_editable(DISPLAY_PAGE) is False


def test_capability_parsing_shorthand_list_and_unknown():
    assert declared_capabilities(DECLARED_PAGE)[0] == list(BASE_CAPABILITY_IDS)
    assert declared_capabilities(DECLARED_PAGE)[1] == []
    known, unknown = declared_capabilities(PARTIAL_PAGE)
    assert known == ["realtime_save"] and unknown == []
    known2, unknown2 = declared_capabilities(UNKNOWN_CAP_PAGE)
    assert unknown2 == ["telepathy"]
    assert set(known2) == set(BASE_CAPABILITY_IDS)
    assert declared_surface(DECLARED_PAGE) == "workbench"
    assert declared_surface(PARTIAL_PAGE) is None


def test_contract_self_describes_the_four_capabilities():
    doc = base_contract()
    assert doc["contract_version"] == "1.0.0"
    assert [c["id"] for c in doc["capabilities"]] == list(BASE_CAPABILITY_IDS)
    assert doc["declaration"]["markers"] == ["useBase(", "<BaseBound"]
    assert doc["exemptions"]["scopes"] == ["pure_display"]
    # 「如何接入」每项都必须有可照做的一句话
    assert all(c["how"] for c in doc["capabilities"])


# --------------------------------------------------------------------------- #
# ① 门禁真的会失败；④ 失败信息给到能力与接法
# --------------------------------------------------------------------------- #
def test_undeclared_editable_page_blocks_the_gate(svc, owner):
    result = _audit(svc, owner)
    assert result["ok"] is False
    assert result["blocking"] is True
    assert result["ci"]["exit_code"] == 1
    kinds = _kinds(result)
    assert kinds["pages/EditablePage.tsx"] == "base_not_declared"
    assert kinds["components/EditableComp.tsx"] == "base_not_declared"
    # ② 扫描面覆盖页面与组件
    assert result["scanned_dirs"] == ["pages", "components"]
    assert result["scanned"] == 9          # 9 个在扫；ui/ 原语与 *.test.tsx 被排除
    assert result["editable"] == 2


def test_every_violation_says_which_capability_is_missing_and_how_to_add_it(svc, owner):
    result = _audit(svc, owner)
    assert result["violations"], "本夹具必须产生违规，否则 ④ 无从断言"
    for item in result["violations"]:
        # 「缺哪个能力」必须点名，且只能是四项里的子集
        assert item["missing_capabilities"], item
        assert set(item["missing_capabilities"]) <= set(BASE_CAPABILITY_IDS)
        # 「如何接入」四项全给（只声明一半时，缺的那三项也照给接法）
        assert [h["capability"] for h in item["how_to_fix"]] == list(BASE_CAPABILITY_IDS)
        assert all(h["how"].strip() for h in item["how_to_fix"])


def test_partial_and_unknown_capability_declarations_are_both_rejected(svc, owner):
    kinds = _kinds(_audit(svc, owner))
    assert kinds["pages/PartialPage.tsx"] == "capability_missing"
    assert kinds["pages/UnknownCapPage.tsx"] == "capability_unknown"
    partial = next(v for v in _audit(svc, owner)["violations"]
                   if v["path"] == "pages/PartialPage.tsx")
    assert partial["missing_capabilities"] == ["audit_trail", "local_first", "error_receipt"]


def test_fully_declared_pages_pass_and_display_components_are_advisory(svc, owner):
    result = _audit(svc, owner)
    # 简写 useBase() 与显式四项列表都算接入；<BaseBound> 也算
    assert result["declared"] == 3
    blocking_paths = {v["path"] for v in result["violations"]}
    assert "pages/DeclaredPage.tsx" not in blocking_paths
    assert "components/DeclaredComp.tsx" not in blocking_paths
    assert "components/BoundComp.tsx" not in blocking_paths
    # 纯展示组件不拦合并，但必须列出来（接线清单不能静默丢弃）
    assert [a["path"] for a in result["advisories"]] == ["components/DisplayComp.tsx"]
    assert result["advisory_count"] == 1
    assert result["display_only"] == 2     # DisplayPage + DisplayComp 都非可编辑


def test_test_files_and_base_primitives_are_out_of_scope(svc, owner):
    paths = {v["path"] for v in _audit(svc, owner)["violations"]}
    assert "pages/DisplayPage.test.tsx" not in paths
    assert "components/ui/Primitive.tsx" not in paths


# --------------------------------------------------------------------------- #
# ③ 豁免：理由 + 审批 + 可编辑不得豁免
# --------------------------------------------------------------------------- #
def test_exemption_requires_owner_and_a_real_reason(svc, session):
    service_actor = Actor.service("worker-1", "worker")
    with pytest.raises(PermissionDenied):
        svc.request_exemption(service_actor, path="pages/DisplayPage.tsx",
                              reason="页面纯粹只读，没有编辑控件")
    with pytest.raises(ValidationFailed) as exc:
        svc.request_exemption(Actor.owner("owner-1"), path="pages/DisplayPage.tsx",
                              reason="只读")
    assert exc.value.code == "exemption_reason_missing"
    with pytest.raises(ValidationFailed) as exc2:
        svc.request_exemption(Actor.owner("owner-1"), path="pages/DisplayPage.tsx",
                              reason="页面纯粹只读，没有编辑控件", scope="everything")
    assert exc2.value.code == "exemption_scope_invalid"


def test_editable_page_cannot_be_exempted(svc, owner):
    with pytest.raises(ValidationFailed) as exc:
        svc.request_exemption(owner, path="pages/EditablePage.tsx",
                              reason="这里确实有输入框，但我想绕过门禁")
    assert exc.value.code == "exemption_not_applicable"


def test_pending_exemption_does_not_excuse_then_approved_one_does_then_revoke(svc, owner):
    svc.request_exemption(owner, path="pages/DisplayPage.tsx",
                          reason="纯展示页：只渲染简报，无任何可编辑控件")
    # 未审批 = 仍然违规
    pending = _kinds(_audit(svc, owner))
    assert pending["pages/DisplayPage.tsx"] == "exemption_pending"

    svc.approve_exemption(owner, path="pages/DisplayPage.tsx", note="已确认只读")
    approved_result = _audit(svc, owner)
    assert "pages/DisplayPage.tsx" not in {v["path"] for v in approved_result["violations"]}
    assert approved_result["exempted"] == 1

    listing = svc.exemptions(owner)
    assert listing["approved"] == ["pages/DisplayPage.tsx"]
    record = listing["items"][0]
    assert record["reason"].startswith("纯展示页")
    assert record["approved_by"] == owner.owner_id
    assert record["approved_at"]

    svc.revoke_exemption(owner, path="pages/DisplayPage.tsx", note="该页将加编辑功能")
    assert _kinds(_audit(svc, owner))["pages/DisplayPage.tsx"] == "base_not_declared"
    assert svc.exemptions(owner)["approved"] == []


def test_exemption_decisions_are_recorded_in_the_audit_chain(svc, owner, session):
    from sqlalchemy import select

    from find_yourself.db.models import AuditEvent

    svc.request_exemption(owner, path="pages/DisplayPage.tsx",
                          reason="纯展示页：只渲染简报，无任何可编辑控件")
    svc.approve_exemption(owner, path="pages/DisplayPage.tsx")
    svc.revoke_exemption(owner, path="pages/DisplayPage.tsx")
    session.commit()
    actions = [row.action for row in session.execute(select(AuditEvent)).scalars()]
    assert "quality.exemption_requested" in actions
    assert "quality.exemption_approved" in actions
    assert "quality.exemption_revoked" in actions


def test_approving_an_unknown_exemption_is_not_found(svc, owner):
    with pytest.raises(NotFound):
        svc.approve_exemption(owner, path="pages/DisplayPage.tsx")
    with pytest.raises(NotFound):
        svc.revoke_exemption(owner, path="pages/DisplayPage.tsx")


def test_exemptions_file_is_plain_readable_json(svc, owner, tmp_path):
    svc.request_exemption(owner, path="pages/DisplayPage.tsx",
                          reason="纯展示页：只渲染简报，无任何可编辑控件")
    data = json.loads((tmp_path / "base_exemptions.json").read_text(encoding="utf-8"))
    assert data["version"] == "1.0.0"
    assert data["records"][0]["path"] == "pages/DisplayPage.tsx"


# --------------------------------------------------------------------------- #
# 真实仓库：门禁对现状如实报红（不是「默认放过」）
# --------------------------------------------------------------------------- #
def test_real_repo_page_tree_still_reports_the_wiring_backlog(svc, owner):
    from find_yourself.services.quality.base_contract import repo_root

    result = svc.audit_tree(owner, root=repo_root(), dirs=["web/src/pages"])
    assert result["scanned"] > 0
    # 全界面接线尚未铺开：门禁必须报红，且逐页给出接法与缺失能力
    assert result["ok"] is False
    assert result["ci"]["exit_code"] == 1
    assert all(v["how_to_fix"] for v in result["violations"])
