"""Server-side caller identity (FROZEN_CONTRACT §2, §5.1) — 兼容垫片。

``Actor`` 实体已下沉至 L1 契约单源 :mod:`find_yourself.identity`（ADR-0001 D3/D6），
此处仅为向后兼容的 re-export 垫片，供既有调用方继续
``from ..services.actor import Actor``。``runtime``（L2.0）此前正是因此产生
``runtime → services`` 的 D3R 禁止方向。
新代码请直接 ``from ..identity import Actor``。
"""

from ..identity import Actor

__all__ = ["Actor"]