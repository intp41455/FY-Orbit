"""T-COMPACT context compaction tests.

Pin-down of the agreed contract: turn-based folding (never split a half
turn), pinned ids survive verbatim, and overflow is reported loudly instead
of silently truncating.
"""

from __future__ import annotations

import pytest

from find_yourself.services.compaction import (
    CompactionService,
    NullSummarizer,
    as_message,
    estimate_tokens,
)


def m(id: str, role: str, content: str) -> dict:
    return {"id": id, "role": role, "content": content}


def turn(n: int, size: int = 400) -> list[dict]:
    """One user→assistant turn; each message ~`size` chars."""
    return [
        m(f"u{n}", "user", "u" * size),
        m(f"a{n}", "assistant", "a" * size),
    ]


@pytest.fixture()
def svc():
    return CompactionService()


# --- token estimate -------------------------------------------------------------

def test_estimate_deterministic():
    assert estimate_tokens("x" * 400) == 100
    assert estimate_tokens("x" * 400) == estimate_tokens("x" * 400)
    with pytest.raises(ValueError):
        estimate_tokens("abc", chars_per_token=0)


# --- basic behaviour ------------------------------------------------------------

def test_short_conversation_not_triggered(svc):
    msgs = [m("s", "system", "sys")] + turn(1) + turn(2)
    out = svc.compact(msgs, budget_tokens=100000)
    assert out["triggered"] is False
    assert out["overflow"] is False
    assert out["folded"] is None
    assert [x["id"] for x in out["messages"]] == ["s", "u1", "a1", "u2", "a2"]


def test_long_conversation_folds_oldest_turns(svc):
    # each message 400 chars ≈ 104 tokens/pair; 6 turns ≈ 624 + head
    msgs = [m("sys0", "system", "S" * 40)] + [t for n in range(6) for t in turn(n)]
    out = svc.compact(msgs, budget_tokens=300)
    assert out["triggered"] is True
    assert out["overflow"] is False
    ids = [x["id"] for x in out["messages"]]
    # newest turn survives verbatim
    assert ids[-2:] == ["u5", "a5"]
    # fold block is injected first, as a system-role block with source_ids
    block = out["messages"][0]
    assert block["role"] == "system"
    assert "已折叠" in block["content"]
    assert "u0" in block["source_ids"] and "u5" not in block["source_ids"]
    assert out["token_estimate"] <= 300 + 8  # small slack for the fold block


# --- 钉子 1: turn-based — a whole turn is the atom ------------------------------

def test_never_splits_half_a_turn(svc):
    # budget fits exactly one full turn, not a user-only half of the next
    msgs = [t for n in range(4) for t in turn(n)]
    out = svc.compact(msgs, budget_tokens=230)
    ids = [x["id"] for x in out["messages"]]
    body_ids = [i for i in ids if i != "fold-block"]
    # every surviving original message appears as complete u/a pairs
    users = [i for i in body_ids if i.startswith("u")]
    assistants = [i for i in body_ids if i.startswith("a")]
    assert len(users) == len(assistants), "half a turn leaked through"
    for u, a in zip(users, assistants):
        assert int(a[1:]) == int(u[1:]), f"orphan pair {u}/{a}"


# --- 钉子 2: pinned messages survive verbatim ------------------------------------

def test_pinned_inside_folded_turn_is_kept_and_excluded_from_sources(svc):
    msgs = [t for n in range(5) for t in turn(n)]
    # 450 fits the floor (newest turn 208 + pinned 104) but not all 5 turns
    # (1040), so turn 0 lands in the folded prefix while its pinned u0 stays.
    out = svc.compact(msgs, budget_tokens=450, pinned_ids=["u0"])
    assert out["triggered"] is True
    ids = [x["id"] for x in out["messages"]]
    assert "u0" in ids, "pinned message was folded away"
    assert out["messages"][ids.index("u0")]["content"] == "u" * 400  # verbatim
    block = out["messages"][0]
    assert "u0" not in block["source_ids"], "pinned id must not be claimed as folded"
    assert "a0" in block["source_ids"]  # its turn-mate did fold
    assert "u0" in out["pinned_kept"]


def test_pinned_in_newest_turn_counts_once(svc):
    msgs = [t for n in range(3) for t in turn(n)]
    out = svc.compact(msgs, budget_tokens=250, pinned_ids=["u2", "a2"])
    assert out["overflow"] is False
    assert out["triggered"] is True
    assert "u2" in [x["id"] for x in out["messages"]]


# --- 钉子 3: explicit overflow — no silent truncation ----------------------------

def test_overflow_reports_full_instead_of_truncating(svc):
    msgs = [m("s", "system", "S" * 1000)] + turn(1)
    out = svc.compact(msgs, budget_tokens=50)
    assert out["overflow"] is True
    assert out["triggered"] is False
    # input handed back untouched
    assert [x["id"] for x in out["messages"]] == ["s", "u1", "a1"]
    assert "拒绝静默截断" in out["overflow_reason"]
    assert out["token_estimate"] > 50


def test_overflow_with_huge_pin(svc):
    msgs = [turn(1)[0], m("pin", "user", "P" * 5000), turn(2)[0], turn(2)[1]]
    out = svc.compact(msgs, budget_tokens=100, pinned_ids=["pin"])
    assert out["overflow"] is True


# --- validation -------------------------------------------------------------------

def test_bad_budget_raises(svc):
    with pytest.raises(ValueError):
        svc.compact([], budget_tokens=0)


def test_unknown_pin_raises(svc):
    with pytest.raises(ValueError) as exc:
        svc.compact([turn(1)[0]], budget_tokens=100, pinned_ids=["ghost"])
    assert "ghost" in str(exc.value)


def test_malformed_message_raises(svc):
    with pytest.raises(ValueError):
        as_message({"role": "user", "content": "no id"})
    with pytest.raises(ValueError):
        as_message(object())


def test_orm_like_object_accepted(svc):
    class Row:  # duck-typed ORM row
        id = "r1"
        role = "user"
        content = "hello"

    out = svc.compact([Row()], budget_tokens=10000)
    assert out["messages"][0]["id"] == "r1"


# --- determinism / pluggability ---------------------------------------------------

def test_null_summarizer_is_deterministic(svc):
    msgs = [t for n in range(5) for t in turn(n)]
    out1 = svc.compact(msgs, budget_tokens=280)
    out2 = svc.compact(msgs, budget_tokens=280)
    assert out1 == out2


def test_custom_summarizer_is_plugged_in():
    class UpperSummarizer(NullSummarizer):
        def summarize(self, turns):
            return "CUSTOM:" + super().summarize(turns)

    svc = CompactionService(summarizer=UpperSummarizer())
    msgs = [t for n in range(4) for t in turn(n)]
    out = svc.compact(msgs, budget_tokens=250)
    assert out["triggered"] is True
    assert out["folded"]["content"].startswith("CUSTOM:")


# --- slice 2: LlmSummarizer / force_fold / compact_history -----------------------

from find_yourself.services.compaction import LlmSummarizer, compact_history


def test_llm_summarizer_happy_path():
    svc = CompactionService(summarizer=LlmSummarizer(lambda req: "LLM摘要内容"))
    msgs = [t for n in range(5) for t in turn(n)]
    out = svc.compact(msgs, budget_tokens=280)
    assert out["triggered"] is True
    assert out["folded"]["content"] == "LLM摘要内容"
    assert "u0" in out["folded"]["source_ids"]


def test_llm_summarizer_degrades_on_exception():
    def boom(_req):
        raise RuntimeError("provider down")

    s = LlmSummarizer(boom)
    msgs = [t for n in range(3) for t in turn(n)]
    out = CompactionService(summarizer=s).compact(msgs, budget_tokens=280)
    assert "已折叠" in out["folded"]["content"]  # deterministic fallback
    assert "RuntimeError" in s.last_error


def test_llm_summarizer_degrades_on_garbage_output():
    s = LlmSummarizer(lambda _req: 42)
    out = LlmSummarizer(lambda _req: 42).summarize([[as_message(turn(1)[0])]])
    assert "已折叠" in out


def test_llm_summarizer_no_fn():
    s = LlmSummarizer(None)
    assert "已折叠" in s.summarize([])


def test_force_fold_produces_usable_oversized_context():
    # floor (head 1000 chars + newest turn) far exceeds the tiny budget,
    # yet force_fold must still return head + newest + fold block, flagged.
    msgs = [m("s", "system", "S" * 1000)] + [t for n in range(3) for t in turn(n)]
    out = CompactionService().compact(msgs, budget_tokens=50, overflow_policy="force_fold")
    assert out["overflow"] is True
    assert "仍超预算" in out["overflow_reason"]
    ids = [x["id"] for x in out["messages"]]
    assert ids[0] == "fold-block"
    assert "s" in ids and "u2" in ids and "a2" in ids  # head + newest kept


def test_force_fold_single_turn_still_honest():
    msgs = [m("s", "system", "S" * 1000)] + turn(1)
    out = CompactionService().compact(msgs, budget_tokens=50, overflow_policy="force_fold")
    assert out["overflow"] is True and out["triggered"] is False
    assert [x["id"] for x in out["messages"]] == ["s", "u1", "a1"]


def test_compact_history_off_budget_is_byte_identical():
    msgs = [m("h1", "user", "你好"), m("h2", "assistant", ""), m("h3", "user", "在吗")]
    naive = "\n".join(f"{m['role']}: {m['content']}" for m in msgs if m["content"])
    text, meta = compact_history(msgs, budget_tokens=100000)
    assert text == naive
    assert meta["triggered"] is False and meta["overflow"] is False


def test_compact_history_folds_when_over():
    msgs = [t for n in range(5) for t in turn(n)]
    text, meta = compact_history(msgs, budget_tokens=280)
    assert meta["triggered"] is True
    assert "已折叠" in text.split("\n")[0]


def test_compact_history_force_fold_on_overflow():
    msgs = [m("s", "system", "S" * 1000)] + [t for n in range(3) for t in turn(n)]
    text, meta = compact_history(msgs, budget_tokens=50, overflow_policy="force_fold")
    assert meta["overflow"] is True
    assert "已折叠" in text  # usable output despite blown budget


def test_compact_history_pins_survive():
    msgs = [t for n in range(5) for t in turn(n)]
    text, meta = compact_history(msgs, budget_tokens=450, pinned_ids=["u0"])
    assert ("user: " + "u" * 400) in text  # pinned content survives verbatim
    assert "u0" in meta["pinned_kept"]
    assert "u0" not in meta["folded"]["source_ids"]  # and isn't claimed as folded


def test_bad_overflow_policy_raises():
    with pytest.raises(ValueError):
        CompactionService().compact([turn(1)[0]], budget_tokens=100, overflow_policy="yolo")


def test_companion_history_block_env_switch(monkeypatch):
    """The companion sample wiring: off => byte-identical, on => compacted."""
    from find_yourself.services.companion import CompanionService

    history = [as_message(m) for m in [t for n in range(5) for t in turn(n)]]

    monkeypatch.delenv("FY_CONTEXT_COMPACTION_BUDGET", raising=False)
    raw, meta = CompanionService._history_block(history)  # type: ignore[attr-defined]
    assert meta is None
    assert raw.startswith("user: " + "u" * 400)

    monkeypatch.setenv("FY_CONTEXT_COMPACTION_BUDGET", "280")
    text, meta2 = CompanionService._history_block(history)  # type: ignore[attr-defined]
    assert meta2 is not None and meta2["triggered"] is True
    assert "已折叠" in text
