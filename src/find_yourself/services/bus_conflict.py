"""B7 冲突裁决（⑤冲突层，N9 地基）。

多个 Agent 抢同一资源（麦克风、GPU、总线写位……）时按确定性规则裁决，
全程留痕（每次裁决带理由 + 竞争者名单，调用方负责写审计）。
规则优先级：owner 否决 > priority 高者 > 先到先得 > claimant id 字典序。
纯内存判定器（无 DB）：进程内裁决；跨进程/持久化随总线后端走（B8 后续）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    """一次资源申领。``is_owner`` 必须由调用方从服务端 Actor 推导，
    绝不接受请求体自报（与全仓身份铁律一致）。"""

    resource: str
    claimant: str
    priority: int = 0
    ts: float = 0.0
    is_owner: bool = False

    def __post_init__(self) -> None:
        if not self.resource or not self.resource.strip():
            raise ValueError("claim.resource 必须非空")
        if not self.claimant or not self.claimant.strip():
            raise ValueError("claim.claimant 必须非空")


@dataclass(frozen=True)
class Decision:
    """一次裁决结果（留痕载体）。"""

    resource: str
    winner: str
    reason: str
    contenders: tuple[str, ...] = ()


class ConflictArbiter:
    """资源 → 当前持有者 + 候选队列。单实例常驻调用方（服务/路由）手中。"""

    def __init__(self) -> None:
        self._claims: dict[str, list[Claim]] = {}

    def claim(self, claim: Claim) -> Decision:
        pool = self._claims.setdefault(claim.resource, [])
        if claim.claimant not in {c.claimant for c in pool}:
            pool.append(claim)
        return self._decide(claim.resource)

    def release(self, resource: str, claimant: str) -> bool:
        pool = self._claims.get(resource, [])
        for i, c in enumerate(pool):
            if c.claimant == claimant:
                # 只有持有者能释放：非持有者冒名释放一律拒绝。
                if self.holder(resource) != claimant:
                    return False
                del pool[i]
                if not pool:
                    self._claims.pop(resource, None)
                return True
        return False

    def holder(self, resource: str) -> str | None:
        pool = self._claims.get(resource)
        if not pool:
            return None
        return self._decide(resource).winner

    def contenders(self, resource: str) -> list[str]:
        return [c.claimant for c in self._claims.get(resource, [])]

    def _decide(self, resource: str) -> Decision:
        pool = list(self._claims.get(resource, []))
        contenders = tuple(c.claimant for c in pool)
        owners = [c for c in pool if c.is_owner]
        if owners:
            # owner 否决：最早申领的 owner 胜（多人场景仍确定）。
            winner = sorted(owners, key=lambda c: (c.ts, c.claimant))[0]
            return Decision(resource, winner.claimant, "owner_veto", contenders)
        ordered = sorted(pool, key=lambda c: (-c.priority, c.ts, c.claimant))
        winner = ordered[0]
        if len({c.priority for c in pool}) > 1:
            reason = "priority"
        elif len({c.ts for c in pool}) > 1:
            reason = "first_writer_wins"
        else:
            reason = "claimant_id_order"
        return Decision(resource, winner.claimant, reason, contenders)
