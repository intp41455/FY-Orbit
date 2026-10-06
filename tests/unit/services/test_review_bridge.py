"""P9 单测 · 点哪评哪桥接（A-点哪评哪-05/06/07/08/09/10）。

覆盖：意见落库（不再只 localStorage）、圈选区域坐标校验、画笔/语音批注、
TOKEN 优化三项（真写标记 / 短码 / DOM 指纹差分）、热刷新闭环、
Markdown 导出（含紧凑形态）、双路线策略（白盒优先 + 兜底必须给理由）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.review_bridge import (
    MODE_DOM,
    MODE_FREEHAND,
    MODE_REGION,
    NOTE_DISMISSED,
    NOTE_OPEN,
    NOTE_RESOLVED,
    REFRESH_APPLIED,
    REFRESH_FAILED,
    ROUTE_COMPUTER_USE,
    ROUTE_WHITEBOX,
    NoteDraft,
    ReviewBridge,
    build_marker,
    domain_digest,
    region_is_normalized,
    render_compact,
    short_code_for,
)


@pytest.fixture()
def svc(session, audit):
    return ReviewBridge(session, audit)


# --------------------------------------------------------------------------- #
# A-点哪评哪-08/09/10 · 纯函数（TOKEN 优化三项）
# --------------------------------------------------------------------------- #


def test_marker_is_short_and_predictable():
    """A-点哪评哪-08：真写标记必须**短**（二十来字符），且可复现。"""
    m = build_marker("button", "save", "primary large extra")
    assert m == "<button#save.primary.large>"
    assert len(m) <= 30, f"标记应控制在 ~30 字符内，实际 {len(m)}"


def test_marker_truncates_long_ids():
    m = build_marker("div", "x" * 200, "")
    assert len(m) <= 60  # 硬上限


def test_short_code_sequence():
    """A-点哪评哪-09：短码随序号递增且稳定。"""
    assert short_code_for(1) == "r1"
    assert short_code_for(42) == "r42"
    with pytest.raises(ValidationFailed):
        short_code_for(0)


def test_domain_digest_ignores_text_and_class_order():
    """A-点哪评哪-10：指纹只吃**结构**（路径/标签/class 集合），不吃文本。"""
    d1 = domain_digest(["body", "div", "button"], "button", "a b")
    d2 = domain_digest(["body", "div", "button"], "BUTTON", "b   a")  # 大小写/空白/顺序
    assert d1 == d2, "class 顺序与空白不应改变指纹"
    d3 = domain_digest(["body", "div", "span"], "span", "a b")
    assert d1 != d3, "结构不同必须指纹不同"


def test_domain_digest_stable_across_calls():
    assert domain_digest(["body", "p"], "p") == domain_digest(["body", "p"], "p")


def test_region_is_normalized_gate():
    assert region_is_normalized({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4})
    assert not region_is_normalized({"x": 1.5, "y": 0, "w": 0.3, "h": 0.4})
    assert not region_is_normalized({"x": 0.1, "y": 0.2, "w": 0, "h": 0.4})
    assert not region_is_normalized({"x": 0.1, "y": 0.2})


# --------------------------------------------------------------------------- #
# 会话与意见落库（本包核心升级）
# --------------------------------------------------------------------------- #


def test_open_session_defaults_to_whitebox(svc, owner):
    s = svc.open_session(owner, page="/chat")
    assert s.route == ROUTE_WHITEBOX
    assert s.iteration == 1
    assert s.refresh_state == "idle"


def test_open_session_rejects_unknown_route(svc, owner):
    """A-路线-03：路线必须显式且合法。"""
    with pytest.raises(ValidationFailed):
        svc.open_session(owner, page="/chat", route="magic")


def test_note_persists_across_sessions(svc, owner, session):
    """★ 核心：意见真的落库了（换一次 session 也能读回）。"""
    s = svc.open_session(owner, page="/chat")
    svc.add_note(owner, s.id, NoteDraft(page="/chat", tag="button",
                                        selector="body > div > button",
                                        note="按钮太大"))
    session.commit()
    # 新开一个 Bridge（模拟另一次请求）——仍能读到
    from find_yourself.services.audit import AuditService
    fresh = ReviewBridge(session, AuditService(session))
    notes = fresh.list_notes(owner, s.id)
    assert len(notes) == 1
    assert notes[0].note == "按钮太大"
    assert notes[0].short_code == "r1"


def test_note_short_codes_increment(svc, owner):
    """A-点哪评哪-09：同一会话内短码递增。"""
    s = svc.open_session(owner, page="/chat")
    codes = [
        svc.add_note(owner, s.id, NoteDraft(tag="button", selector=f"#b{i}")).short_code
        for i in range(3)
    ]
    assert codes == ["r1", "r2", "r3"]


def test_notes_are_owner_scoped(svc, owner, session):
    """不同 owner 看不到彼此的会话（越权返回 404 而非泄露）。"""
    s = svc.open_session(owner, page="/chat")
    other = Actor.owner("owner-2")
    with pytest.raises(NotFound):
        svc.list_notes(other, s.id)


def test_delete_note(svc, owner):
    s = svc.open_session(owner, page="/chat")
    n = svc.add_note(owner, s.id, NoteDraft(tag="a", selector="#a"))
    svc.delete_note(owner, n.id)
    assert svc.list_notes(owner, s.id) == []
    with pytest.raises(NotFound):
        svc.update_note(owner, n.id, note="x")


# --------------------------------------------------------------------------- #
# A-点哪评哪-06 · 圈选区域评论
# --------------------------------------------------------------------------- #


def test_region_note_accepts_normalized_rect(svc, owner):
    s = svc.open_session(owner, page="/chat")
    n = svc.add_note(owner, s.id, NoteDraft(
        mode=MODE_REGION, region={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.15},
        note="这块颜色太浅",
    ))
    assert n.mode == MODE_REGION
    assert n.region["w"] == 0.3


def test_region_note_rejects_pixel_coords(svc, owner):
    """A-点哪评哪-06：像素坐标不可复原，必须拒。"""
    s = svc.open_session(owner, page="/chat")
    with pytest.raises(ValidationFailed):
        svc.add_note(owner, s.id, NoteDraft(
            mode=MODE_REGION, region={"x": 120, "y": 40, "w": 300, "h": 80},
        ))


def test_region_note_rejects_missing_rect(svc, owner):
    s = svc.open_session(owner, page="/chat")
    with pytest.raises(ValidationFailed):
        svc.add_note(owner, s.id, NoteDraft(mode=MODE_REGION, note="没框"))


# --------------------------------------------------------------------------- #
# A-点哪评哪-07 · 语音 + 画笔批注
# --------------------------------------------------------------------------- #


def test_freehand_note_with_strokes(svc, owner):
    s = svc.open_session(owner, page="/chat")
    strokes = [
        {"points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}], "color": "#f00", "width": 2},
    ]
    n = svc.add_note(owner, s.id, NoteDraft(mode=MODE_FREEHAND, strokes=strokes, note="圈出这里"))
    assert n.mode == MODE_FREEHAND
    assert len(n.strokes) == 1


def test_freehand_note_with_audio_only(svc, owner):
    """A-点哪评哪-07：只有语音也算有效批注。"""
    s = svc.open_session(owner, page="/chat")
    n = svc.add_note(owner, s.id, NoteDraft(
        mode=MODE_FREEHAND, audio_ref="review-audio/abc.webm",
        audio_transcript="这里应该往左挪一点",
    ))
    assert n.audio_ref == "review-audio/abc.webm"
    assert n.audio_transcript.startswith("这里应该")


def test_freehand_note_rejects_empty(svc, owner):
    """A-点哪评哪-07：空白批注（无笔迹无语音）不接受。"""
    s = svc.open_session(owner, page="/chat")
    with pytest.raises(ValidationFailed):
        svc.add_note(owner, s.id, NoteDraft(mode=MODE_FREEHAND, note="空"))


# --------------------------------------------------------------------------- #
# A-点哪评哪-10 · DOM 差分
# --------------------------------------------------------------------------- #


def test_dom_diff_detects_real_structure_change(svc, owner):
    """同一元素两次提交，结构变了 → changed=True。"""
    s = svc.open_session(owner, page="/chat")
    sel = "body > div > button"
    svc.add_note(owner, s.id, NoteDraft(tag="button", selector=sel, dom_path=["body", "div", "button"]))
    n2 = svc.add_note(owner, s.id, NoteDraft(tag="button", selector=sel, dom_path=["body", "section", "button"]))
    assert n2.changed is True
    assert n2.prev_dom_digest != n2.dom_digest


def test_dom_diff_silent_when_unchanged(svc, owner):
    s = svc.open_session(owner, page="/chat")
    sel = "body > div > button"
    svc.add_note(owner, s.id, NoteDraft(tag="button", selector=sel, dom_path=["body", "div", "button"]))
    n2 = svc.add_note(owner, s.id, NoteDraft(tag="button", selector=sel, dom_path=["body", "div", "button"]))
    assert n2.changed is False


def test_dom_diff_first_note_is_not_changed(svc, owner):
    """首条意见无基准 → 不猜，changed=False。"""
    s = svc.open_session(owner, page="/chat")
    n = svc.add_note(owner, s.id, NoteDraft(tag="button", selector="#solo"))
    assert n.prev_dom_digest == ""
    assert n.changed is False


# --------------------------------------------------------------------------- #
# A-点哪评哪-05 · 改完自动刷新预览闭环
# --------------------------------------------------------------------------- #


def test_refresh_applied_bumps_iteration(svc, owner):
    s = svc.open_session(owner, page="/chat")
    assert s.iteration == 1
    s2 = svc.mark_refreshed(owner, s.id, state=REFRESH_APPLIED, note="HMR 已热更新")
    assert s2.iteration == 2
    assert s2.refresh_state == REFRESH_APPLIED
    assert s2.refresh_note == "HMR 已热更新"


def test_refresh_failed_does_not_bump_iteration(svc, owner):
    """★ 诚实：刷新失败不推进轮次、如实记原因。"""
    s = svc.open_session(owner, page="/chat")
    s2 = svc.mark_refreshed(owner, s.id, state=REFRESH_FAILED, note="HMR 连接断开")
    assert s2.iteration == 1
    assert s2.refresh_state == REFRESH_FAILED
    assert "断开" in s2.refresh_note


def test_refresh_rejects_unknown_state(svc, owner):
    s = svc.open_session(owner, page="/chat")
    with pytest.raises(ValidationFailed):
        svc.mark_refreshed(owner, s.id, state="whatever")


def test_resolve_note_sets_applied_at(svc, owner):
    """闭环完成锚点：state=resolved 落 applied_at。"""
    s = svc.open_session(owner, page="/chat")
    n = svc.add_note(owner, s.id, NoteDraft(tag="a", selector="#a", note="改这里"))
    assert n.applied_at is None
    n2 = svc.update_note(owner, n.id, state=NOTE_RESOLVED)
    assert n2.state == NOTE_RESOLVED
    assert n2.applied_at is not None
    # 退回 open 应清掉锚点
    n3 = svc.update_note(owner, n.id, state=NOTE_OPEN)
    assert n3.applied_at is None


# --------------------------------------------------------------------------- #
# 导出（Markdown / 紧凑形态）
# --------------------------------------------------------------------------- #


def test_export_markdown_contains_marker_and_code(svc, owner):
    s = svc.open_session(owner, page="/chat")
    svc.add_note(owner, s.id, NoteDraft(tag="button", element_id="save",
                                        selector="body > button#save", note="改文案"))
    md = svc.export(owner, s.id)
    assert "<button#save>" in md       # A-点哪评哪-08 真写标记
    assert "[r1]" in md                # A-点哪评哪-09 短码
    assert "改文案" in md
    assert "/chat" in md


def test_export_compact_is_shorter(svc, owner):
    """A-点哪评哪-08/09 的可验证收益：紧凑形态显著更短。"""
    s = svc.open_session(owner, page="/chat")
    for i in range(5):
        svc.add_note(owner, s.id, NoteDraft(
            tag="button", selector=f"body > div:nth-of-type({i}) > button.very-long-class",
            note=f"第{i}条意见",
        ))
    full = svc.export(owner, s.id, compact=False)
    compact = svc.export(owner, s.id, compact=True)
    assert len(compact) < len(full)
    assert compact.count("\n") < full.count("\n")


def test_render_compact_lists_each_note_one_line(svc, owner):
    s = svc.open_session(owner, page="/chat")
    svc.add_note(owner, s.id, NoteDraft(tag="a", selector="#a", note="N1"))
    svc.add_note(owner, s.id, NoteDraft(tag="b", selector="#b", note="N2"))
    notes = svc.list_notes(owner, s.id)
    out = render_compact(s, notes)
    body = [ln for ln in out.splitlines() if ln.startswith(("![", "v["))]
    assert len(body) == 2


def test_session_summary_counts(svc, owner):
    s = svc.open_session(owner, page="/chat")
    n1 = svc.add_note(owner, s.id, NoteDraft(tag="a", selector="#a"))
    svc.add_note(owner, s.id, NoteDraft(tag="b", selector="#b"))
    svc.update_note(owner, n1.id, state=NOTE_RESOLVED)
    summ = svc.session_summary(owner, s.id)
    assert summ["total"] == 2
    assert summ["open"] == 1
    assert summ["resolved"] == 1
    assert summ["dismissed"] == 0
    assert summ["route"] == ROUTE_WHITEBOX


def test_note_state_dismissed(svc, owner):
    s = svc.open_session(owner, page="/chat")
    n = svc.add_note(owner, s.id, NoteDraft(tag="a", selector="#a"))
    n2 = svc.update_note(owner, n.id, state=NOTE_DISMISSED)
    assert n2.state == NOTE_DISMISSED


def test_dom_mode_requires_target(svc, owner):
    s = svc.open_session(owner, page="/chat")
    with pytest.raises(ValidationFailed):
        svc.add_note(owner, s.id, NoteDraft(mode=MODE_DOM, note="没有目标"))


def test_route_marker_used_when_selector_absent_for_diff(svc, owner):
    """圈选类无 selector 时，差分基准退化为 marker（不崩）。"""
    s = svc.open_session(owner, page="/chat")
    n1 = svc.add_note(owner, s.id, NoteDraft(
        mode=MODE_REGION, region={"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
    ))
    n2 = svc.add_note(owner, s.id, NoteDraft(
        mode=MODE_REGION, region={"x": 0.3, "y": 0.3, "w": 0.2, "h": 0.2},
    ))
    assert n1.short_code == "r1" and n2.short_code == "r2"
    assert n2.changed is False  # 无 DOM 结构 → 不报 changed
