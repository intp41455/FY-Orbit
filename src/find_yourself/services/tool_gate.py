"""Tool admission gate (T-GATE).

Every ``tool_registry.invoke`` call passes through this gate **before** any
schema validation or execution. Three actions:

* ``allow`` — proceed as before (default when no policy matches).
* ``deny``  — hard reject with a readable reason; never executes.
* ``ask``   — route to a human approval hook. If no approval channel is wired
  (headless run, plain HTTP caller), the call is *denied* — an ``ask`` tool
  must never silently execute just because nobody is around to approve.

Policy sources (both active at once, ``deny`` wins over ``ask``):

* environment: ``FY_TOOL_GATE_DENY`` / ``FY_TOOL_GATE_ASK`` — comma-separated
  tool names or ``prefix.*`` wildcards (e.g. ``shell.*,http.raw``).
* config: a dict ``{"deny": [...], "ask": [...]}`` handed to ``ToolGate()``.

Default wiring: :func:`install_default_gate` is the single place where the
env-derived gate is attached to the process-wide ``tool_registry`` singleton,
so the policy is in force on *every* run path (HTTP route, agent dispatch, chat
orchestration, dynamic MCP) and setting the env vars is all an operator does.

Design rules honoured from the repo philosophy:

* degrade, don't crash — an unreadable env value degrades to "no rules from
  that source", never a boot failure; *policy* errors (bad pattern) raise.
* the gate is fail-closed: unknown action values are treated as ``deny``.
* every decision is returned structured so the registry can log it in the
  same call-log evidence trail it already keeps.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

DENY_ENV = "FY_TOOL_GATE_DENY"
ASK_ENV = "FY_TOOL_GATE_ASK"

_ALLOWED_ACTIONS = ("allow", "deny", "ask")
# Tool names are validated by TOOL_NAME_RE at registration time; wildcard
# patterns add only the ``*`` metachar (prefix form, e.g. ``shell.*``).
_PATTERN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,62}\*$|^[a-z][a-z0-9_.-]{1,63}$")


@dataclass(frozen=True)
class GateDecision:
    """Structured outcome of one gate check (loggable, JSON-safe)."""

    action: str                 # "allow" | "deny" | "ask"
    reason: str = ""
    matched_rule: str = ""      # the pattern that fired ("" when allow-by-default)
    source: str = ""            # "env" | "config" | "default"

    def __post_init__(self) -> None:
        if self.action not in _ALLOWED_ACTIONS:
            raise ValueError(f"gate action must be one of {_ALLOWED_ACTIONS}, got {self.action!r}")


@dataclass
class ToolGate:
    """Prefix-wildcard aware admission policy for tool invocations.

    ``ask_hook``: optional ``callable(tool_name, arguments, decision) -> dict``
    returning ``{"approved": bool, "reason": str}``. Wired by hosts that have
    an actor / HITL context (chat orchestration, agent dispatch). Absent hook
    => every ``ask`` decision degrades to ``deny`` (fail-closed).
    """

    deny_rules: tuple[str, ...] = ()
    ask_rules: tuple[str, ...] = ()
    ask_hook: Callable[[str, dict[str, Any], GateDecision], dict[str, Any]] | None = None
    from_env: bool = False   # True when built via gate_from_env (affects log provenance)
    _deny: list[re.Pattern[str]] = field(default_factory=list, repr=False)
    _ask: list[re.Pattern[str]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._deny = self._compile(self.deny_rules, "deny")
        self._ask = self._compile(self.ask_rules, "ask")

    # -- pattern handling -------------------------------------------------------

    @staticmethod
    def _compile(rules: tuple[str, ...] | list[str], kind: str) -> list[re.Pattern[str]]:
        compiled: list[re.Pattern[str]] = []
        for rule in rules:
            if not isinstance(rule, str) or not _PATTERN_RE.fullmatch(rule):
                raise ValueError(
                    f"invalid {kind} rule {rule!r}: expected a tool name or 'prefix.*' pattern"
                )
            if rule.endswith("*"):
                # prefix.*  →  match the literal prefix followed by anything
                # (the ``*`` itself is the only metachar we honour; everything
                # else is escaped so dots don't turn into wildcards).
                compiled.append(re.compile("^" + re.escape(rule[:-1]) + ".*$"))
            else:
                compiled.append(re.compile("^" + re.escape(rule) + "$"))
        return compiled

    @staticmethod
    def _first_match(patterns: list[re.Pattern[str]], name: str) -> str:
        for pat in patterns:
            if pat.fullmatch(name):
                return pat.pattern
        return ""

    # -- the gate ----------------------------------------------------------------

    def check(self, tool_name: str, arguments: dict[str, Any] | None = None) -> GateDecision:
        """Decide admission for ``tool_name``. deny > ask > allow (default)."""
        if not isinstance(tool_name, str) or not tool_name:
            # Garbage input is a caller bug — deny with a readable reason.
            return GateDecision("deny", "tool name must be a non-empty string", source="default")

        hit = self._first_match(self._deny, tool_name)
        if hit:
            return GateDecision(
                "deny",
                f"tool '{tool_name}' is denied by gate rule '{hit}' "
                "(remove it from the deny list to enable)",
                matched_rule=hit,
                source="env" if self.from_env else "config",
            )

        hit = self._first_match(self._ask, tool_name)
        if hit:
            decision = GateDecision(
                "ask",
                f"tool '{tool_name}' requires human approval (matched '{hit}')",
                matched_rule=hit,
                source="env" if self.from_env else "config",
            )
            if self.ask_hook is None:
                # Fail-closed: no approval channel wired => do not execute.
                return GateDecision(
                    "deny",
                    f"tool '{tool_name}' requires human approval but no approval "
                    "channel is wired in this context; refusing to execute. "
                    "Wire an ask_hook (HITL) or remove it from the ask list.",
                    matched_rule=hit,
                    source="config",
                )
            verdict = self.ask_hook(tool_name, arguments if isinstance(arguments, dict) else {}, decision)
            if not isinstance(verdict, dict):
                raise ValueError("ask_hook must return {'approved': bool, 'reason': str}")
            if verdict.get("approved") is True:
                return GateDecision(
                    "allow",
                    str(verdict.get("reason", "approved by human")),
                    matched_rule=hit,
                    source="env" if self.from_env else "config",
                )
            return GateDecision(
                "deny",
                str(verdict.get("reason") or f"approval denied for '{tool_name}'"),
                matched_rule=hit,
                source="env" if self.from_env else "config",
            )

        return GateDecision("allow", "no gate rule matched", source="default")


def gate_from_env(environ: dict[str, str] | None = None) -> ToolGate:
    """Build a gate from ``FY_TOOL_GATE_DENY`` / ``FY_TOOL_GATE_ASK``.

    Unreadable entries (non-string env values) degrade to "no rules" — a
    broken environment must not take the whole API down on boot. Malformed
    *patterns* still raise (policy errors deserve loud failure).
    """
    env = os.environ if environ is None else environ

    def _split(key: str) -> list[str]:
        raw = env.get(key)
        if not raw or not isinstance(raw, str):
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    try:
        return ToolGate(
            deny_rules=tuple(_split(DENY_ENV)),
            ask_rules=tuple(_split(ASK_ENV)),
            from_env=True,
        )
    except ValueError as exc:
        # Fail fast, and say *where* the bad policy came from: a malformed
        # pattern is a misconfiguration, not a transient error, and a boot that
        # silently degrades to "no rules" is exactly the fail-open we forbid.
        raise ValueError(
            f"invalid tool gate policy from environment "
            f"({DENY_ENV}={env.get(DENY_ENV)!r}, {ASK_ENV}={env.get(ASK_ENV)!r}): {exc}"
        ) from exc


def install_into(registry: Any, gate: ToolGate | None = None) -> ToolGate:
    """Attach ``gate`` (default: built from env) to a ``ToolRegistryService``.

    Returns the gate actually installed so callers can assert on it.
    """
    if gate is None:
        gate = gate_from_env()
    registry.tool_gate = gate
    return gate


def install_default_gate(registry: Any | None = None, *, replace: bool = False) -> ToolGate:
    """Give ``registry`` the env-derived gate; return the gate actually in force.

    * ``registry=None`` resolves the process-wide ``tool_registry`` singleton
      (imported lazily so this module stays free of a ``tool_registry`` import
      cycle). This is the default-assembly entry point used by
      ``tool_registry`` itself and by ``api.deps.get_services``.
    * ``replace=False`` (default) makes the call **idempotent**: a gate that is
      already installed is kept, so a host that wired its own gate (typically
      with a HITL ``ask_hook``) is never silently downgraded to the env default
      on a later request. Pass ``replace=True`` to force the env policy back.
    * A malformed pattern raises :class:`ValueError` *before* anything is
      assigned, so a rejected policy leaves the registry exactly as it was.
    """
    if registry is None:
        from .tool_registry import tool_registry as registry
    existing = getattr(registry, "tool_gate", None)
    if existing is not None and not replace:
        return existing
    return install_into(registry, gate_from_env())
