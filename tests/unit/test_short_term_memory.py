"""Unit tests for P1-07 short-term memory strategy (window + summary).

Acceptance (p1-task-breakdown §4 P1-07): build a 30-turn conversation, assert
a summary is generated, the window token estimate converges within the
threshold, and the summary retains early key information. Before/after
comparison logs are printed (run pytest with ``-s`` to capture them).
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from find_yourself.db.models import Conversation, Message
from find_yourself.services.short_term_memory import (
    DEFAULT_MAX_WINDOW_TOKENS,
    MemoryWindow,
    build_window,
    estimate_tokens,
    extractive_summary,
    group_turns,
    register_token_estimator,
)


class _Msg:
    """Plain message stand-in (duck-typed with ORM Message)."""

    def __init__(self, id: str, role: str, content: str):
        self.id = id
        self.role = role
        self.content = content


# 30 turns; early turns carry distinctive facts that must survive compression.
EARLY_FACTS = ("项目代号 HYACINTH", "预算上限 12 万")
RECENT_KEYWORD = "最终部署方案"


def make_messages(turns: int = 30) -> list[_Msg]:
    msgs: list[_Msg] = []
    for i in range(1, turns + 1):
        if i == 2:
            user = f"先记一下：{EARLY_FACTS[0]}，对外统一用这个名字。"
        elif i == 3:
            user = f"还有，{EARLY_FACTS[1]}，超了要走审批。"
        else:
            user = f"第{i}轮：讨论需求点 {i}，请分析现状并给出建议。"
        msgs.append(_Msg(f"m{i:03d}u", "user", user))
        assistant = f"第{i}轮回复：针对需求点 {i} 的分析、风险与建议。"
        if i == turns:
            assistant = f"好的，{RECENT_KEYWORD}已经准备好，待你确认后执行。"
        msgs.append(_Msg(f"m{i:03d}a", "assistant", assistant))
    return msgs


def test_estimate_tokens_char_approximation():
    assert estimate_tokens("") == 0
    # CJK counts ~1 token per char
    assert estimate_tokens("你好世界") == 4
    # ASCII ~4 chars per token (ceil)
    assert estimate_tokens("abcdefgh") == 2
    # mixed
    assert estimate_tokens("你好abcd") == 3


def test_no_compression_below_threshold():
    msgs = make_messages(3)
    w = build_window(msgs, max_window_tokens=100_000)
    assert w.compressed is False
    assert w.summary == ""
    assert len(w.recent_messages) == len(msgs)
    assert w.token_estimate == w.original_token_estimate


def test_group_turns_30():
    msgs = make_messages(30)
    turns = group_turns(msgs)
    assert len(turns) == 30
    assert all(t[0].role == "user" for t in turns)


def test_30_turn_window_converges_and_keeps_early_facts(capsys):
    msgs = make_messages(30)
    before = sum(estimate_tokens(m.content) for m in msgs)
    w = build_window(msgs, keep_recent_turns=6, max_window_tokens=800)

    print(
        json.dumps(
            {
                "case": "30-turn window compression",
                "turns": w.turns_total,
                "messages": len(msgs),
                "before_tokens": before,
                "after_tokens": w.token_estimate,
                "max_window_tokens": DEFAULT_MAX_WINDOW_TOKENS,
                "compressed": w.compressed,
                "turns_summarized": w.turns_summarized,
                "turns_recent": w.turns_recent,
                "summarized_message_ids": len(w.summarized_message_ids),
            },
            ensure_ascii=False,
        )
    )
    print("[P1-07] 摘要样例:\n" + w.summary)

    assert w.compressed is True
    assert w.turns_total == 30
    # 摘要生成
    assert w.summary.strip() != ""
    # 窗口 token 收敛到阈值内
    assert w.token_estimate <= 800
    assert w.token_estimate < before * 0.5  # 显著收敛
    # 早期关键信息保留在摘要中
    assert EARLY_FACTS[0] in w.summary
    assert EARLY_FACTS[1] in w.summary
    # 最近轮原文保留
    assert len(w.recent_messages) > 0
    assert any(RECENT_KEYWORD in m.content for m in w.recent_messages)
    assert w.turns_recent == 6
    assert w.turns_summarized == 24
    # 全量 source 可追溯
    assert len(w.source_ids) == len(msgs)
    assert set(w.summarized_message_ids).isdisjoint(
        {getattr(m, "id") for m in w.recent_messages}
    )


def test_window_render_contains_summary_and_recent():
    w = build_window(make_messages(30))
    rendered = w.render()
    assert EARLY_FACTS[0] in rendered
    assert RECENT_KEYWORD in rendered


def test_idempotent_second_pass():
    msgs = make_messages(30)
    w1 = build_window(msgs)
    # Second pass over the rendered window (summary block + recent msgs) must
    # stay within budget and not re-summarize into something larger.
    class _SummaryMsg:
        id = "summary-1"
        role = "system"
        content = f"[早前会话摘要]\n{w1.summary}"

    w2 = build_window([_SummaryMsg(), *w1.recent_messages])
    assert w2.token_estimate <= DEFAULT_MAX_WINDOW_TOKENS
    assert EARLY_FACTS[0] in w2.render()


def test_custom_summarizer_receives_folded_messages():
    seen: list[int] = []

    def fake_llm_summarizer(messages):
        seen.append(len(messages))
        return f"LLM 摘要（{len(messages)} 条）: 项目代号 HYACINTH，预算上限 12 万"

    w = build_window(make_messages(30), summarizer=fake_llm_summarizer)
    assert seen and seen[0] == 48  # 24 folded turns x 2 messages
    assert w.summary.startswith("LLM 摘要")
    assert EARLY_FACTS[0] in w.summary


def test_token_estimator_replacement_point():
    register_token_estimator(lambda text, model: len(text) * (2 if model else 1))
    assert estimate_tokens("abcd", "gpt-x") == 8
    assert estimate_tokens("abcd", None) == 4
    # restore default
    from find_yourself.services import short_term_memory as stm

    register_token_estimator(stm._char_based_estimate)
    assert estimate_tokens("你好世界") == 4


def test_extractive_summary_dedupes_and_orders_user_first():
    msgs = make_messages(4)
    folded = [m for t in group_turns(msgs) for m in t]
    s = extractive_summary(folded)
    lines = s.splitlines()
    assert lines[0].startswith("[user]")
    assert len(lines) == len(set(lines))


# ---------------------------------------------------------------------------
# DB-backed variant: real ORM Message rows on isolated in-memory SQLite
# ---------------------------------------------------------------------------


@pytest.fixture()
def conv_30(session):
    conv = Conversation(
        id="conv-p107", owner_id="owner-1", title="P1-07 验收",
        domain="personal", mode="research",
    )
    session.add(conv)
    for m in make_messages(30):
        session.add(Message(
            id=m.id, conversation_id=conv.id, role=m.role, content=m.content,
            client_message_id=uuid4().hex,
        ))
    session.flush()
    return conv


def test_db_backed_30_turn_session(session, conv_30, capsys):
    from sqlalchemy import select

    rows = list(session.execute(
        select(Message)
        .where(Message.conversation_id == conv_30.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).scalars())
    assert len(rows) == 60

    w = build_window(rows)
    print(
        json.dumps(
            {
                "case": "DB-backed 30-turn session (ORM Message rows)",
                "conversation_id": conv_30.id,
                "messages": len(rows),
                "before_tokens": w.original_token_estimate,
                "after_tokens": w.token_estimate,
                "compressed": w.compressed,
                "summary_head": w.summary.splitlines()[0] if w.summary else "",
            },
            ensure_ascii=False,
        )
    )
    assert isinstance(w, MemoryWindow)
    assert w.compressed is True
    assert w.token_estimate <= DEFAULT_MAX_WINDOW_TOKENS
    assert EARLY_FACTS[0] in w.summary and EARLY_FACTS[1] in w.summary
