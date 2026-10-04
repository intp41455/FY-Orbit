"""插件包生态的数据模型（需求 14，第一切片：签名 / 扫描 / 上架门禁）。

本模块只拥有插件生态自己的**一张**表，**不改** ``db/models.py`` 的其余部分
（``Skill`` 表的签名/扫描列在 ``db/models.py`` 内就地扩展，因为它属于那张表）：

:class:`PluginSigningKey` —— 一个**公钥**及其元数据。包签名（需求 14）用非对称
签名把「这份包是谁发布的」这件事钉死在**不可变的** ``skills.package_hash`` 上。

为什么这里**只存公钥**、绝无第二份私钥列
----------------------------------------
签名要成立，前提是「验证者拿得到公钥、拿不到私钥」。一旦把私钥（哪怕加密后）
写进库，任何能读这张表（超出 SQL 注入、备份泄露、运维导出等**任何一次**读权限
外泄）的人都能**伪造**任意包的签名，整套门禁当场作废——因为验证用的公钥和被
伪造签名用的私钥来自同一个数据面。所以这里是**结构性的**「无私钥」：不是「我们
记得不写」，而是**根本没有那一列可写**。发布方在库外签名，只把公钥登记进来
（与 ``service`` 只登记公钥 id 而同一条纪律）。

``state`` 只允许 ``active`` / ``revoked``：密钥轮换时旧 key **吊销而非删除**，
否则「用旧 key 签的历史包」的验证结论会在行被删后无法复算——审计链需要能回答
「当时那把 key 是什么状态」。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow

#: 支持的签名算法。刻意是白名单：只有在 :mod:`find_yourself.services.plugin_signing`
#: 里真的实现了验证的算法才允许登记。未知算法无法验证，登记它只会制造一个
#: 「看起来签了名、其实没人能验」的假象。
SIGNING_KEY_ALGORITHMS = ("ed25519",)

#: 公钥生命周期。
SIGNING_KEY_STATES = ("active", "revoked")


class PluginSigningKey(Base):
    """一个被信任的**公钥**。``id`` 即 ``skills.signing_key_id`` 指向的密钥 id。

    Ed25519 的公钥是固定 32 字节，用 base64 存成文本；**不存私钥**（见模块
    docstring：这是结构性保证，不是纪律承诺）。
    """

    __tablename__ = "plugin_signing_keys"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="ed25519")
    #: base64(raw 32-byte ed25519 public key)。**这里永远是公钥**。
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "algorithm IN ('ed25519')", name="ck_psk_algorithm"
        ),
        CheckConstraint("state IN ('active','revoked')", name="ck_psk_state"),
        CheckConstraint("version >= 1", name="ck_psk_version_positive"),
        CheckConstraint("length(public_key) > 0", name="ck_psk_public_key_nonempty"),
        # 同一个公钥只登记一次：重复登记会让「key_id -> 公钥」出现多值，
        # 验证时取哪一行就变成一个不可回答的问题。
        UniqueConstraint("public_key", name="uq_psk_public_key"),
        Index("ix_psk_owner_state", "owner_id", "state"),
    )
