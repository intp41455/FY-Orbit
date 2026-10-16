"""Context compaction (T-COMPACT, slice 1) — deterministic, offline, explicit.

Design contract (agreed with the product owner, 2026-10-16):

1. **按轮切 (turn-based)** — compaction never splits a half-finished turn.
   A turn starts at a ``user`` message and runs until the next ``user``
   message; the unit of folding is the whole turn. Messages before the first
   ``user`` message (leading system/developer preamble) are head matter and
   are always kept verbatim.
2. **钉子保留 (pinned)** — pinned message ids survive compaction verbatim.
   If a pinned message lives inside an otherwise-folded turn, it is lifted
   out of that turn and kept; the fold summary's ``source_ids`` then excludes
   it (every folded insight still traces back, M11 style).
3. **触顶明确报满 (explicit overflow)** — if the untouchable floor (head
   matter + pinned + the newest turn) alone exceeds the budget, the service
   does NOT silently truncate. It returns ``overflow=True`` with a readable
   ``overflow_reason`` and hands back the untouched input; expanding the
   budget or failing the request is the caller's explicit choice.

Deterministic summarizer by default (``NullSummarizer``): folded turns
become a structured placeholder block with source ids — no model call, fully
offline. An LLM summarizer can be plugged in later behind the same
``Summarizer`` protocol.

Token accounting is a deterministic estimate (``len // chars_per_token``);
it is a budgeting heuristic, not a tokenizer. ``chars_per_token`` defaults
to 4 (ASCII-fair; CJK text will underestimate somewhat — callers managing
CJK-heavy contexts should lower the budget or the ratio).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

logger = logging.getLogger(__name__)

DEFAULT_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str, *, chars_per_token: int = DEFAULT_CHARS_PER_TOKEN) -> int:
    """Deterministic token estimate. Pure arithmetic, no model, no cache."""
    if chars_per_token <= 0:
        raise ValueError("chars_per_token must be positive")
    return len(text) // chars_per_token


class CompactableMessage(Protocol):
    """Duck-typed message: ORM ``Message`` satisfies this, so do plain dicts
    wrapped via :func:`as_message`."""

    id: str
    role: str
    content: str


@dataclass(frozen=True)
class _Msg:
    id: str
    role: str
    content: str


def as_message(obj: Any) -> CompactableMessage:
    """Accept ORM rows or plain dicts ({id, role, content}) uniformly."""
    if isinstance(obj, dict):
        missing = [k for k in ("id", "role", "content") if k not in obj]
        if missing:
            raise ValueError(f"message dict missing keys: {missing}")
        return _Msg(str(obj["id"]), str(obj["role"]), str(obj["content"]))
    for attr in ("id", "role", "content"):
        if not hasattr(obj, attr):
            raise ValueError(f"message object lacks attribute '{attr}'")
    return obj  # type: ignore[return-value]


class Summarizer(Protocol):
    """Pluggable folding strategy. Receives whole turns; returns summary text."""

    def summarize(self, turns: list[list[CompactableMessage]]) -> str: ...


class NullSummarizer:
    """Deterministic offline fold: one line per folded turn, ids preserved.

    Emits NO prose, NO model call — the block is pure bookkeeping so a human
    (or the model) can see what was elided and trace it via ``source_ids``.
    """

    def summarize(self, turns: list[list[CompactableMessage]]) -> str:
        lines = [f"[已折叠上下文 · {sum(len(t) for t in turns)} 条消息 / {len(turns)} 轮，确定性折叠，原文可按 id 回溯]"]
        for turn in turns:
            ids = [m.id for m in turn]
            roles = sorted({m.role for m in turn})
            lines.append(f"- 轮次({','.join(roles)}): {len(turn)} 条 msg_ids={','.join(ids)}")
        return "\n".join(lines)


class LlmSummarizer:
    """Model-backed fold. **Degrades, never raises** (repo philosophy):
    any failure — missing callable, exception, non-string output — falls back
    to :class:`NullSummarizer` for that call and logs a warning.

    ``complete_fn``: ``Callable[[str], str]`` — send the folded-turns request
    text, get the summary text back. Hosts wire this to their governed
    ``ModelGateway.complete`` (or any summarisation channel they trust);
    kept as a plain callable so this module stays free of gateway imports.
    """

    def __init__(self, complete_fn: Callable[[str], str] | None, *, fallback: bool = True):
        self._complete_fn = complete_fn
        self._fallback = fallback
        self._null = NullSummarizer()
        self.last_error: str = ""

    def summarize(self, turns: list[list[CompactableMessage]]) -> str:
        if self._complete_fn is None:
            self.last_error = "no complete_fn wired"
            return self._null.summarize(turns) if self._fallback else ""
        request = (
            "把以下被折叠的对话轮次压缩成一段简短的要点摘要（中文，"
            "保留关键事实、结论与未决问题，不要逐字复述）：\n\n"
            + "\n".join(f"{m.role}: {m.content}" for t in turns for m in t)
        )
        try:
            out = self._complete_fn(request)
        except Exception as exc:  # noqa: BLE001 — summarisation is best-effort
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("LlmSummarizer degraded to NullSummarizer: %s", self.last_error)
            return self._null.summarize(turns) if self._fallback else ""
        if not isinstance(out, str) or not out.strip():
            self.last_error = f"non-string/empty summary: {type(out).__name__}"
            logger.warning("LlmSummarizer degraded to NullSummarizer: %s", self.last_error)
            return self._null.summarize(turns) if self._fallback else ""
        self.last_error = ""
        return out.strip()


@dataclass
class CompactionService:
    """Turn-based, pin-aware, overflow-explicit context compaction."""

    summarizer: Summarizer = field(default_factory=NullSummarizer)
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN
    fold_marker_role: str = "system"   # role used for the injected fold block

    # -- token accounting ---------------------------------------------------

    def _msg_tokens(self, m: CompactableMessage) -> int:
        # role overhead + content; join separators are accounted per turn
        return estimate_tokens(m.content, chars_per_token=self.chars_per_token) + 4

    def _turn_tokens(self, turn: list[CompactableMessage]) -> int:
        return sum(self._msg_tokens(m) for m in turn)

    # -- turn segmentation ---------------------------------------------------

    @staticmethod
    def _split_turns(messages: list[CompactableMessage]) -> tuple[list[CompactableMessage], list[list[CompactableMessage]]]:
        """Return (head matter, turns). Head = everything before first user msg."""
        first_user = next((i for i, m in enumerate(messages) if m.role == "user"), len(messages))
        head = messages[:first_user]
        turns: list[list[CompactableMessage]] = []
        for m in messages[first_user:]:
            if m.role == "user" or not turns:
                turns.append([m])
            else:
                turns[-1].append(m)
        return head, turns

    # -- the service ----------------------------------------------------------

    def compact(
        self,
        messages: Iterable[Any],
        *,
        budget_tokens: int,
        pinned_ids: Iterable[str] = (),
        overflow_policy: str = "report",
    ) -> dict[str, Any]:
        """Compact ``messages`` into ``budget_tokens``.

        ``overflow_policy``:
        * ``"report"`` (default) — floor doesn't fit => overflow=True, input
          handed back untouched (explicit-is-better-than-truncated).
        * ``"force_fold"`` — chat-style fallback: fold *everything* older
          than the newest turn (head matter and pins still kept verbatim),
          even when that floor exceeds the budget; the result is still
          flagged ``overflow=True`` with honest accounting so callers can
          see the budget was blown, but a usable (if oversized) context is
          produced instead of refusing to answer.

        Returns a JSON-safe dict (see module docstring for the contract).
        Raises ``ValueError`` for bad budgets / malformed messages — caller
        bugs deserve loud failure; *content* problems never crash this.
        """
        if overflow_policy not in ("report", "force_fold"):
            raise ValueError("overflow_policy must be 'report' or 'force_fold'")
        if budget_tokens <= 0:
            raise ValueError("budget_tokens must be positive")
        msgs = [as_message(m) for m in messages]
        pinned = {str(p) for p in pinned_ids}
        unknown_pins = pinned - {m.id for m in msgs}
        if unknown_pins:
            raise ValueError(f"pinned ids not present in messages: {sorted(unknown_pins)}")

        head, turns = self._split_turns(msgs)
        head_tokens = self._turn_tokens(head)

        # --- overflow check: the untouchable floor must fit, else report full.
        newest_turn = turns[-1] if turns else []
        newest_ids = {m.id for m in newest_turn}
        head_ids = {m.id for m in head}
        # pinned messages inside the newest turn are already counted there;
        # count only pinned messages elsewhere (excluding head) as floor adds.
        floor_extra = [
            m for m in msgs
            if m.id in pinned and m.id not in newest_ids and m.id not in head_ids
        ]
        floor = head_tokens + self._turn_tokens(newest_turn) + sum(self._msg_tokens(m) for m in floor_extra)
        if floor > budget_tokens and overflow_policy == "report":
            return {
                "triggered": False,
                "overflow": True,
                "overflow_reason": (
                    f"预算 {budget_tokens} tokens 装不下不可压缩下限（头部 {head_tokens}"
                    f" + 最新一轮 {self._turn_tokens(newest_turn)}"
                    f" + 钉子 {sum(self._msg_tokens(m) for m in floor_extra)}）——"
                    "请扩预算或显式失败，本服务拒绝静默截断。"
                ),
                "messages": [{"id": m.id, "role": m.role, "content": m.content} for m in msgs],
                "folded": None,
                "source_ids": [m.id for m in msgs],
                "pinned_kept": sorted(pinned),
                "token_estimate": sum(self._msg_tokens(m) for m in msgs),
                "budget_tokens": budget_tokens,
            }

        # --- pack whole turns from the newest backwards.
        # The newest turn is kept unconditionally (floor semantics, already
        # validated by the overflow check), so packing starts above it.
        kept_turns: list[list[CompactableMessage]] = []
        used = head_tokens + self._turn_tokens(newest_turn) + sum(self._msg_tokens(m) for m in floor_extra)
        for turn in reversed(turns[:-1]):
            t_tokens = self._turn_tokens(turn)
            if used + t_tokens > budget_tokens:
                break
            kept_turns.insert(0, turn)
            used += t_tokens

        kept_ids = {m.id for t in kept_turns for m in t} | {m.id for m in head} | {m.id for m in newest_turn}

        folded_turns: list[list[CompactableMessage]] = []
        # older turns = all turns except the newest; kept_turns is their
        # newest suffix. Fold the prefix (explicit arithmetic — slicing with
        # [:-0] is a Python trap that silently yields an empty list).
        n_older = len(turns) - 1
        older = turns[: n_older - len(kept_turns)]
        for turn in older:
            pin_msgs = [m for m in turn if m.id in pinned]
            rest = [m for m in turn if m.id not in pinned]
            if rest:
                folded_turns.append(rest)
            kept_ids.update(m.id for m in pin_msgs)

        kept_msgs = [m for m in msgs if m.id in kept_ids]
        final_tokens = sum(self._msg_tokens(m) for m in kept_msgs)

        triggered = bool(folded_turns)
        if triggered:
            summary_text = self.summarizer.summarize(folded_turns)
            folded_block = {
                "id": "fold-block",
                "role": self.fold_marker_role,
                "content": summary_text,
                "source_ids": [m.id for t in folded_turns for m in t],
            }
            body = [{"id": m.id, "role": m.role, "content": m.content} for m in kept_msgs]
            estimate = final_tokens + estimate_tokens(summary_text, chars_per_token=self.chars_per_token)
            # force_fold may legitimately blow the budget (oversized floor);
            # report it honestly instead of pretending the output fits.
            blown = estimate > budget_tokens
            return {
                "triggered": True,
                "overflow": blown,
                "overflow_reason": (
                    f"force_fold 后仍超预算：下限（头部+钉子+最新一轮）{floor} > 预算 {budget_tokens}，"
                    f"实际输出约 {estimate} tokens——已保留全部钉子与最新一轮，其余折叠。"
                    if blown else ""
                ),
                "messages": [folded_block] + body,
                "folded": folded_block,
                "source_ids": [m.id for m in msgs],
                "pinned_kept": sorted(pinned),
                "token_estimate": estimate,
                "budget_tokens": budget_tokens,
            }

        # nothing needed folding — pass through untouched
        blown = final_tokens > budget_tokens  # possible only under force_fold
        return {
            "triggered": False,
            "overflow": blown,
            "overflow_reason": (
                f"无可折叠内容仍超预算：floor（头部+钉子+最新一轮）{floor} > 预算 {budget_tokens}，"
                "原样返回。" if blown else ""
            ),
            "messages": [{"id": m.id, "role": m.role, "content": m.content} for m in msgs],
            "folded": None,
            "source_ids": [m.id for m in msgs],
            "pinned_kept": sorted(pinned),
            "token_estimate": final_tokens,
            "budget_tokens": budget_tokens,
        }


def compact_history(
    messages: Iterable[Any],
    *,
    budget_tokens: int,
    pinned_ids: Iterable[str] = (),
    summarizer: Summarizer | None = None,
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN,
    overflow_policy: str = "keep_all",
) -> tuple[str, dict[str, Any]]:
    """One-call convenience for prompt composers (companion / butler / teams).

    Takes a DB message list, returns ``(text, meta)`` where ``text`` is the
    "近期对话" block ready to drop into a composed prompt and ``meta`` is the
    underlying :meth:`CompactionService.compact` result.

    * Off budget (no folding): text is **byte-identical** to the naive
      ``"\n".join(f"{m.role}: {m.content}" ...)`` over non-empty messages —
      callers can adopt this without changing any golden output.
    * ``overflow_policy``:
      - ``"keep_all"`` (default) — overflow hands back the full history
        (report-loudly semantics, matching :meth:`compact`).
      - ``"force_fold"`` — chat-style: fold everything older than the newest
        turn even if the floor blows the budget; ``meta["overflow"]`` stays
        honest about the overrun.

    Empty-content messages are filtered first, mirroring the composers'
    existing ``if m.content`` behaviour.
    """
    msgs = [m for m in messages if (m.get("content") if isinstance(m, dict) else m.content)]
    svc = CompactionService(summarizer=summarizer or NullSummarizer(), chars_per_token=chars_per_token)
    result = svc.compact(
        msgs,
        budget_tokens=budget_tokens,
        pinned_ids=pinned_ids,
        overflow_policy="force_fold" if overflow_policy == "force_fold" else "report",
    )
    lines = [f"{m['role']}: {m['content']}" for m in result["messages"] if m.get("content")]
    return "\n".join(lines), result


# Process-wide default (deterministic, offline). Hosts may construct their own
# with an LLM summarizer once slice 2 lands.
compaction_service = CompactionService()
