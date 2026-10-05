"""B 包 · 存档持久化（把 `LifeSave` 落成数据库行）。

**本模块是 B11 的持久化真源**：它是唯一知道 `LifeSave` 内存结构与数据库表结构
怎么对应的地方，路由层与服务层都只经由这里的函数读写。

模型放在 service 层而**不是** `db/models`，是刻意的（说明书 §2.3 选项 C）：
`migrations/versions/0001_initial.py:37` 用 `Base.metadata.create_all(bind)` 建全表，
只要模型被 `db/models.py` 导入就会在 0001 阶段建出来，后续迁移的 `op.create_table`
要么撞「表已存在」要么在已有库上变成 no-op —— 迁移就不再是唯一真源了。
不导入 `db/models` 就能让 `0034` 成为 `life_saves` 的唯一建表者，与 W2 的
`services/cabin_gameplay.py::CabinSave` 完全同构（那一套已经在生产跑通）。

诚实原则：
    * 存档损坏（主题不存在 / 时钟字段缺失 / 金币不是整数 / JSON 不是对象）时
      **抛 `LifeSaveCorrupt` 并带原因**，绝不静默回落成一份「新存档」——
      静默回落等于把玩家的进度凭空清零还不告诉他；
    * 只读不写：读函数不碰 session，写函数要求调用方显式 commit；
    * 不在这里做规则计算。所有规则来自 `cabin_life` 的纯函数模块。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...db.base import Base
from ...db.types import TZDateTime, utcnow
from ..errors import Conflict
from . import themes
from .state import LifeSave


# ----------------------------------------------------------------------
# 损坏存档的显式错误
# ----------------------------------------------------------------------


class LifeSaveCorrupt(Conflict):
    """存档读不出来时抛出，**不**回落成空档。

    继承 `Conflict`（409）：这是「服务端持有的数据坏了」，不是客户端请求写错，
    也不是 404（行确实在）。`reason` 字段给出人类可读的具体原因。
    """

    def __init__(self, owner: str, reason: str):
        self.reason = reason
        super().__init__(
            "life_save_corrupt",
            f"存档 {owner!r} 已损坏，未做静默重置：{reason}",
        )


# ----------------------------------------------------------------------
# ORM
# ----------------------------------------------------------------------


class LifeSaveRow(Base):
    """B 包存档行 (cabin_life · life_saves) —— 每 owner 一行。

    字段与 `state.LifeSave` **一一对应**，多出的 `created_at`/`updated_at` 只用于
    运维观察，不参与任何游戏规则。乐观锁用 `version`（与 W2 `CabinSave` 同做法）。
    """

    __tablename__ = "life_saves"

    owner_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    theme: Mapped[str] = mapped_column(String(32))
    clock: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    weather: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    bag: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    coins: Mapped[int] = mapped_column(Integer, default=0)
    skill_exp: Mapped[int] = mapped_column(Integer, default=0)
    affinity: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    gifts_today: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    shop: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quest_log: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    gather_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[Any] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[Any] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow
    )

    # 约束名一律传**短名**：`db/base.py` 的 `"ck": "ck_%(table_name)s_%(constraint_name)s"`
    # 会把传入的名字再当 constraint_name 再插值一次；传展开名会得到
    # `ck_life_saves_ck_life_saves_*`（这正是 P7 在 0032 上踩过的坑）。
    __table_args__ = (
        CheckConstraint("coins >= 0", name="life_save_coins_nonnegative"),
        CheckConstraint("skill_exp >= 0", name="life_save_exp_nonnegative"),
        CheckConstraint("version >= 1", name="life_save_version_positive"),
    )


# ----------------------------------------------------------------------
# 行 <-> LifeSave
# ----------------------------------------------------------------------


def apply_save(row: LifeSaveRow, save: LifeSave) -> LifeSaveRow:
    """把 `LifeSave` 写进已有行（不 commit，调用方决定事务边界）。"""
    row.theme = save.theme
    row.clock = dict(save.clock)
    row.weather = dict(save.weather)
    row.bag = {str(k): int(v) for k, v in save.bag.items()}
    row.coins = int(save.coins)
    row.skill_exp = int(save.skill_exp)
    row.affinity = {str(k): int(v) for k, v in save.affinity.items()}
    row.gifts_today = {str(k): int(v) for k, v in save.gifts_today.items()}
    row.shop = dict(save.shop)
    row.quest_log = dict(save.quest_log)
    row.gather_counts = {str(k): int(v) for k, v in save.gather_counts.items()}
    row.version = int(save.version)
    return row


def new_row(owner: str, save: LifeSave) -> LifeSaveRow:
    return apply_save(LifeSaveRow(owner_id=owner, version=1), save)


def _need_obj(raw: Any, field: str, owner: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LifeSaveCorrupt(
            owner, f"字段 {field} 应为 JSON 对象，实际是 {type(raw).__name__}"
        )
    return raw


def _need_int(value: Any, field: str, owner: str, *, minimum: int | None = None) -> int:
    # bool 是 int 的子类，但 `coins: true` 显然是坏数据而不是 1 金币。
    if isinstance(value, bool) or not isinstance(value, int):
        raise LifeSaveCorrupt(
            owner, f"字段 {field} 应为整数，实际是 {type(value).__name__}（{value!r}）"
        )
    if minimum is not None and value < minimum:
        raise LifeSaveCorrupt(
            owner, f"字段 {field} 为 {value}，低于允许下界 {minimum}"
        )
    return value


def _need_int_map(raw: Any, field: str, owner: str) -> dict[str, int]:
    obj = _need_obj(raw, field, owner)
    out: dict[str, int] = {}
    for key, value in obj.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise LifeSaveCorrupt(
                owner,
                f"字段 {field}[{key!r}] 应为整数，实际是 {type(value).__name__}",
            )
        out[str(key)] = int(value)
    return out


def read_save(row: LifeSaveRow) -> LifeSave:
    """从行还原 `LifeSave`；数据不可信时抛 `LifeSaveCorrupt` 并说明原因。

    这里**逐字段校验**而不是「能转就转」：一份时钟字段缺失的存档如果被放行，
    上层会拿着 `KeyError` 去算时间，表现成随机崩溃或更糟 —— 静默错比报错更难查。
    """
    owner = row.owner_id

    theme = row.theme
    if theme not in themes.theme_ids():
        raise LifeSaveCorrupt(
            owner, f"主题 {theme!r} 不存在（可用：{', '.join(themes.theme_ids())}）"
        )

    clock_raw = _need_obj(row.clock, "clock", owner)
    for key in ("day", "minute"):
        if key not in clock_raw:
            raise LifeSaveCorrupt(owner, f"字段 clock 缺少必需键 {key!r}")
    day = _need_int(clock_raw["day"], "clock.day", owner, minimum=1)
    minute = _need_int(clock_raw["minute"], "clock.minute", owner, minimum=0)
    if minute >= 24 * 60:
        raise LifeSaveCorrupt(owner, f"clock.minute 为 {minute}，超出一天的 1440 分钟")

    weather = _need_obj(row.weather, "weather", owner)
    bag = _need_int_map(row.bag, "bag", owner)
    coins = _need_int(row.coins, "coins", owner, minimum=0)
    skill_exp = _need_int(row.skill_exp, "skill_exp", owner, minimum=0)
    affinity = _need_int_map(row.affinity, "affinity", owner)
    gifts_today = _need_int_map(row.gifts_today, "gifts_today", owner)
    shop = _need_obj(row.shop, "shop", owner)
    quest_log = _need_obj(row.quest_log, "quest_log", owner)
    if "entries" not in quest_log:
        raise LifeSaveCorrupt(owner, "字段 quest_log 缺少必需键 'entries'")
    if not isinstance(quest_log["entries"], list):
        raise LifeSaveCorrupt(
            owner,
            f"字段 quest_log.entries 应为数组，实际是 {type(quest_log['entries']).__name__}",
        )
    for i, entry in enumerate(quest_log["entries"]):
        if not isinstance(entry, dict) or "quest_id" not in entry:
            raise LifeSaveCorrupt(owner, f"quest_log.entries[{i}] 缺少 'quest_id'")
    gather_counts = _need_int_map(row.gather_counts, "gather_counts", owner)
    version = _need_int(row.version, "version", owner, minimum=1)

    from . import clock as clock_mod

    return LifeSave(
        owner=owner,
        theme=theme,
        # part / part_label 是派生值：缺失时按 minute 重算，因为它们完全由
        # clock.day_part 决定，不是独立存档的数据。
        clock={
            "day": day,
            "minute": minute,
            "part": clock_raw.get("part") or clock_mod.day_part(minute),
            "part_label": clock_raw.get("part_label") or "",
        },
        weather=weather,
        bag=bag,
        coins=coins,
        skill_exp=skill_exp,
        affinity=affinity,
        gifts_today=gifts_today,
        shop=shop,
        quest_log=quest_log,
        gather_counts=gather_counts,
        version=version,
    )


# ----------------------------------------------------------------------
# 会话便捷函数
# ----------------------------------------------------------------------


def fetch(db: Session, owner: str) -> LifeSaveRow | None:
    return db.get(LifeSaveRow, owner)


def load(db: Session, owner: str) -> LifeSave | None:
    """读存档；行不存在返回 None，存在但坏了则抛 `LifeSaveCorrupt`。"""
    row = fetch(db, owner)
    return read_save(row) if row is not None else None


def store(db: Session, save: LifeSave) -> LifeSaveRow:
    """写存档（upsert），不 commit。`save.version` 由调用方自增后传入。"""
    row = fetch(db, save.owner)
    if row is None:
        row = new_row(save.owner, save)
        db.add(row)
    else:
        apply_save(row, save)
    return row
