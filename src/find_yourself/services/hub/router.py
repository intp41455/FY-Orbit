"""能力画像与统一路由（W6 §1.3）。

v1 是**确定性评分**，不是模型智能路由：

.. code-block:: text

    score = 2.0 × 命中标签数
          + 1.0 × 能力名命中
          + 1.5 × 最近探活通过
          − 2.0 × 最近探活失败
          + 0.1 × 用户偏好权重（0-10）
          − 1.0 × 状态为 needs_credentials / disabled

**v2 才由 LLM 做语义路由**（任务书明确：v1 不做）。这里刻意把顺序与理由一并返回，
让「为什么选中它」可解释、可复核——黑箱路由在计费与权限场景下是不可接受的。

诚实原则：一个都匹配不上就返回空数组，绝不随便挑一个「看起来像」的连接凑数。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select as sa_select

from ...db.workbench_models import HubConnection
from ..errors import ValidationFailed
from .adapters import Capability
from .connections import HubService

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+#.-]*")
_CJK_RE = re.compile(r"[㐀-鿿぀-ヿ]")

#: 中文提示词没有空格分词：退化为「整串 + 二元组」，命中即算。
_CJK_NGRAM = 2

#: 同义标签兜底（v1 手写表，v2 由 LLM 接管）。
SYNONYMS: dict[str, tuple[str, ...]] = {
    "聊天": ("chat", "llm"),
    "对话": ("chat", "llm"),
    "写作": ("chat", "llm", "text"),
    "写代码": ("chat", "llm", "code"),
    "搜索": ("search",),
    "检索": ("search",),
    "知识": ("knowledge",),
    "文档": ("knowledge", "document"),
    "工具": ("tool",),
    "本地": ("local",),
}


def tokenize(hint: str) -> list[str]:
    """ASCII tokens + CJK n-grams. Lowercased, used for tag matching."""
    text = (hint or "").lower()
    tokens = [t for t in _TOKEN_RE.findall(text) if len(t) >= 2]
    cjk = "".join(_CJK_RE.findall(text))
    if cjk:
        tokens.append(cjk)
        for i in range(len(cjk) - _CJK_NGRAM + 1):
            tokens.append(cjk[i:i + _CJK_NGRAM])
    for word, mapped in SYNONYMS.items():  # 中文同义词 -> 英文标签
        if word in text:
            tokens.extend(mapped)
    return list(dict.fromkeys(tokens))


def _matches(capability: Capability, tokens: list[str], hint: str) -> list[str]:
    hits: list[str] = []
    lowered = hint.lower()
    for tag in capability.tags:
        low = tag.lower()
        if low in tokens or (len(low) >= 2 and low in lowered):
            hits.append(tag)
    if capability.name.lower() in tokens or capability.name.lower() in lowered:
        hits.append(capability.name)
    return list(dict.fromkeys(hits))


class CapabilityRouter:
    """Capability registry + deterministic candidate ranking."""

    def __init__(self, session, owner_id: str):
        self.s = session
        self.owner_id = owner_id

    # -- capability registry ------------------------------------------------- #
    def register_capability(self, conn_id: str, capability: dict[str, Any] | Capability) -> dict[str, Any]:
        """Add (or refresh) one capability on a connection. Idempotent by name."""
        from ..errors import NotFound, PermissionDenied

        conn = self.s.get(HubConnection, conn_id)
        if conn is None:
            raise NotFound("hub_connection_not_found", "连接不存在")
        if conn.owner_id != self.owner_id:
            raise PermissionDenied("hub_connection_forbidden", "无权访问该连接", 403)
        cap = Capability.from_public(capability)
        current = [c for c in (conn.capabilities or []) if isinstance(c, dict)]
        replaced = False
        for i, item in enumerate(current):
            if item.get("name") == cap.name:
                current[i] = cap.to_public()
                replaced = True
                break
        if not replaced:
            current.append(cap.to_public())
        conn.capabilities = current
        self.s.flush()
        return conn.capabilities

    def unregister_capability(self, conn_id: str, capability_name: str) -> list[dict[str, Any]]:
        from ..errors import NotFound, PermissionDenied

        conn = self.s.get(HubConnection, conn_id)
        if conn is None:
            raise NotFound("hub_connection_not_found", "连接不存在")
        if conn.owner_id != self.owner_id:
            raise PermissionDenied("hub_connection_forbidden", "无权访问该连接", 403)
        conn.capabilities = [
            c for c in (conn.capabilities or [])
            if isinstance(c, dict) and c.get("name") != capability_name
        ]
        self.s.flush()
        return conn.capabilities

    # -- routing -------------------------------------------------------------- #
    def candidates(self, *, kind: str | None = None,
                   include_unhealthy: bool = False) -> list[dict[str, Any]]:
        stmt = sa_select(HubConnection).where(HubConnection.owner_id == self.owner_id)
        if kind:
            stmt = stmt.where(HubConnection.kind == kind)
        rows = self.s.execute(stmt).scalars().all()
        out: list[dict[str, Any]] = []
        for conn in rows:
            if conn.state in {"disabled", "needs_credentials"}:
                continue
            if not include_unhealthy and conn.last_health_ok is False:
                continue
            for cap in (conn.capabilities or []):
                if not isinstance(cap, dict) or not cap.get("name"):
                    continue
                try:
                    parsed = Capability.from_public(cap)
                except ValidationFailed:
                    continue
                out.append({
                    "connection_id": conn.id,
                    "connection_name": conn.name,
                    "kind": conn.kind,
                    "group": conn.group,
                    "icon": conn.icon,
                    "state": conn.state,
                    "healthy": conn.last_health_ok,
                    "preference": conn.preference or 0,
                    "capability": parsed,
                })
        return out

    def route(self, hint: str, *, top_k: int = 5, kind: str | None = None,
              include_unhealthy: bool = False) -> list[dict[str, Any]]:
        """Rank connections for ``hint``. Empty list when nothing matches."""
        text = (hint or "").strip()
        if not text:
            raise ValidationFailed("hub_route_hint_required", "路由试算需要一句任务描述")
        tokens = tokenize(text)
        scored: dict[str, dict[str, Any]] = {}
        for item in self.candidates(kind=kind, include_unhealthy=include_unhealthy):
            capability: Capability = item["capability"]
            hits = _matches(capability, tokens, text)
            if not hits:
                continue
            reasons = [f"标签命中：{', '.join(hits)}"]
            score = 2.0 * len([h for h in hits if h in capability.tags])
            if capability.name in hits:
                score += 1.0
            if item["healthy"] is True:
                score += 1.5
                reasons.append("最近探活通过")
            elif item["healthy"] is False:
                score -= 2.0
                reasons.append("最近探活失败（已降权）")
            else:
                reasons.append("尚未探活（不计健康分）")
            if item["preference"]:
                score += 0.1 * int(item["preference"])
                reasons.append(f"用户偏好 +{item['preference']}")
            key = item["connection_id"]
            best = scored.get(key)
            entry = {
                "connection_id": key,
                "connection_name": item["connection_name"],
                "kind": item["kind"],
                "group": item["group"],
                "icon": item["icon"],
                "healthy": item["healthy"],
                "score": round(score, 3),
                "reasons": reasons,
                "capability": capability.to_public(),
            }
            # 同一连接多条能力命中：保留最高分，但把理由合并，避免重复卡片。
            if best is None or entry["score"] > best["score"]:
                if best is not None:
                    entry["reasons"] = list(dict.fromkeys(best["reasons"] + reasons))
                scored[key] = entry
            else:
                best["reasons"] = list(dict.fromkeys(best["reasons"] + reasons))
        ranked = sorted(scored.values(), key=lambda e: (-e["score"], e["connection_name"]))
        return ranked[: max(1, top_k)]


def route_for_owner(session, owner_id: str, hint: str, *, top_k: int = 5,
                    kind: str | None = None) -> list[dict[str, Any]]:
    """Convenience wrapper used by the API layer."""
    return CapabilityRouter(session, owner_id).route(hint, top_k=top_k, kind=kind)


__all__ = ["CapabilityRouter", "HubService", "SYNONYMS", "route_for_owner", "tokenize"]
