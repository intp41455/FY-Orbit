"""Short-term memory strategy: rolling window with extractive summary (P1-07).

Manages the in-session message window for conversations:

- messages are grouped into "turns" (a user message plus the assistant/system
  replies that follow it);
- when the whole conversation's estimated token footprint exceeds the window
  budget, the oldest turns are folded into a summary while the most recent
  ``keep_recent_turns`` turns stay verbatim;
- token estimation is a character-based approximation (CJK ≈ 1 token/char,
  other scripts ≈ 1 token / 4 chars). A model-aware estimator (e.g. tiktoken)
  can be swapped in via :func:`register_token_estimator` — the interface
  already carries the ``model`` parameter for that replacement;
- summarization goes through an injectable ``summarizer`` callable. The
  default is a deterministic extractive stub (first/keyword sentences of each
  folded message, joined in order) so behaviour is reproducible without a
  live model. To route through the project's inference abstraction
  (``runtime.gateway.ModelGateway``), pass ``summarizer=my_llm_callable`` —
  the callable receives the list of folded messages and returns a string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

DEFAULT_KEEP_RECENT_TURNS = 6
DEFAULT_MAX_WINDOW_TOKENS = 800
DEFAULT_SUMMARY_TOKEN_BUDGET = 300

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?\n])\s*")
_NOISE = re.compile(r"[\s，、,.;:；：（）()\[\]\"']+")


# ---------------------------------------------------------------------------
# Token estimation (model-aware replacement point)
# ---------------------------------------------------------------------------

TokenEstimator = Callable[[str, str | None], int]


def _char_based_estimate(text: str, model: str | None = None) -> int:
    """Deterministic char-based approximation; ``model`` is unused here."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + -(-other // 4)  # ceil(other / 4)


_token_estimator: TokenEstimator = _char_based_estimate


def register_token_estimator(estimator: TokenEstimator) -> None:
    """Swap in a model-aware estimator (signature: ``(text, model) -> int``)."""
    global _token_estimator
    _token_estimator = estimator


def estimate_tokens(text: str, model: str | None = None) -> int:
    return _token_estimator(text, model)


# ---------------------------------------------------------------------------
# Summarizer (LLM replacement point)
# ---------------------------------------------------------------------------

Summarizer = Callable[[Sequence[Any]], str]


def extractive_summary(messages: Sequence[Any], max_chars: int = 1200) -> str:
    """Deterministic extractive stub: keep the first salient sentence per message.

    User messages are folded first (they carry intent/decisions), assistant
    messages after. Replace with an LLM call (e.g. via ModelGateway) by
    passing a custom ``summarizer`` to :func:`build_window`.
    """
    lines: list[str] = []
    seen: set[str] = set()
    ordered = sorted(messages, key=lambda m: 0 if getattr(m, "role", "") == "user" else 1)
    for m in ordered:
        content = (getattr(m, "content", "") or "").strip()
        if not content:
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(content) if s.strip()]
        if not sentences:
            continue
        # Prefer the longest sentence among the first two — usually the
        # information-carrying one — falling back to the first.
        pick = max(sentences[:2], key=len)
        key = _NOISE.sub("", pick.lower())
        if key in seen:
            continue
        seen.add(key)
        role = getattr(m, "role", "?")
        lines.append(f"[{role}] {pick}")
        if sum(len(l) for l in lines) > max_chars:
            break
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Window building
# ---------------------------------------------------------------------------


@dataclass
class MemoryWindow:
    """Result of applying the short-term memory strategy to a conversation."""

    summary: str = ""
    summarized_message_ids: list[str] = field(default_factory=list)
    recent_messages: list[Any] = field(default_factory=list)
    token_estimate: int = 0
    original_token_estimate: int = 0
    compressed: bool = False
    turns_total: int = 0
    turns_summarized: int = 0
    turns_recent: int = 0

    @property
    def source_ids(self) -> list[str]:
        """Every message id the window covers (traceability, cf. M11)."""
        return self.summarized_message_ids + [
            getattr(m, "id", "") for m in self.recent_messages
        ]

    def render(self) -> str:
        """Rendered context block to prepend to the next model call."""
        parts: list[str] = []
        if self.summary:
            parts.append(f"[会话摘要/早前 {self.turns_summarized} 轮]\n{self.summary}")
        for m in self.recent_messages:
            parts.append(f"[{getattr(m, 'role', '?')}] {getattr(m, 'content', '')}")
        return "\n".join(parts)


def group_turns(messages: Sequence[Any]) -> list[list[Any]]:
    """Group messages into turns: each turn starts at a ``user`` message and
    contains the following assistant/system replies."""
    turns: list[list[Any]] = []
    for m in messages:
        if getattr(m, "role", "") == "user" or not turns:
            turns.append([m])
        else:
            turns[-1].append(m)
    return turns


def build_window(
    messages: Sequence[Any],
    *,
    model: str | None = None,
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
    max_window_tokens: int = DEFAULT_MAX_WINDOW_TOKENS,
    summary_token_budget: int = DEFAULT_SUMMARY_TOKEN_BUDGET,
    summarizer: Summarizer | None = None,
) -> MemoryWindow:
    """Build the compressed short-term memory window for a conversation.

    No compression happens while the full conversation fits the budget. Once
    over budget: fold all but the most recent ``keep_recent_turns`` turns into
    a summary; if the window still exceeds ``max_window_tokens`` the oldest
    recent turns are dropped one by one until it converges.
    """
    if not messages:
        return MemoryWindow()

    summarize = summarizer or extractive_summary
    turns = group_turns(messages)
    original_tokens = sum(estimate_tokens(getattr(m, "content", ""), model) for m in messages)

    window = MemoryWindow(
        turns_total=len(turns),
        original_token_estimate=original_tokens,
        recent_messages=list(messages),
        token_estimate=original_tokens,
    )
    if original_tokens <= max_window_tokens:
        window.turns_recent = len(turns)
        return window

    keep = min(max(1, keep_recent_turns), len(turns))
    while keep >= 1:
        folded = turns[:-keep] if keep < len(turns) else []
        recent_flat = [m for t in turns[len(turns) - keep:] for m in t]
        folded_flat = [m for t in folded for m in t]
        summary = summarize(folded_flat) if folded_flat else ""
        # Hard-cap the summary so it can never blow the budget.
        while estimate_tokens(summary, model) > summary_token_budget and len(summary) > 40:
            summary = summary[: len(summary) // 2].rstrip()
            summary = summary + "\n…[摘要截断]"
        recent_tokens = sum(estimate_tokens(getattr(m, "content", ""), model) for m in recent_flat)
        total = estimate_tokens(summary, model) + recent_tokens
        if total <= max_window_tokens or keep == 1:
            window.summary = summary
            window.summarized_message_ids = [
                getattr(m, "id", "") for t in folded for m in t
            ]
            window.recent_messages = recent_flat
            window.token_estimate = total
            window.compressed = True
            window.turns_summarized = len(folded)
            window.turns_recent = keep
            return window
        keep -= 1
    return window  # pragma: no cover — keep==1 branch always returns
