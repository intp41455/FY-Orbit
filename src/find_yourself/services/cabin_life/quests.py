"""B6 · 任务系统（主线 + 支线，纯逻辑层）。

说明书 §5-B6：主线 + 支线；NPC 头顶感叹号；任务日志追踪。

设计：
    * 任务用**事件计数**推进（`event` + `target`），和 W2 已有 `quest_state` 语义一致，
      这样存档迁移不需要两套进度模型；
    * `QuestLog` 是纯函数推进器：输入旧 log + 事件 → 新 log，含完成/领取状态；
    * 感叹号由 `marker_for()` 给出：`!` 有可接 / `?` 可交付 / 空 = 无，UI 直接消费。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .npcs import NPCS
from .rng import rng_for
from .themes import THEME_IDS


@dataclass(frozen=True)
class QuestDef:
    id: str
    title: str
    kind: str  # 'main' | 'side' | 'daily'
    theme: str
    giver: str  # npc_id
    event: str  # 进度事件名
    target: int
    reward_coins: int
    reward_items: tuple[tuple[str, int], ...] = ()
    reward_hearts_points: int = 0
    desc: str = ""


def _first_giver(theme: str) -> str:
    """主题的委托发起人（确定性：取该主题第一个有委托的 NPC）。

    在 `_MAIN` 构造时调用；`npcs` 已 import，`NPCS` 全局按 id 有序填充，
    这里按 (theme, id) 排序取第一个带委托的 NPC → 结果稳定。
    """
    from .npcs import _commission_for  # 局部导入避免循环

    for npc in sorted((n for n in NPCS.values() if n.theme == theme), key=lambda n: n.id):
        if _commission_for(npc):
            return npc.id
    raise KeyError(f"theme {theme!r} has no npc with commission")  # pragma: no cover



#: 主线（每主题 3 段，串起来是一条完整的生活线）
_MAIN: tuple[QuestDef, ...] = tuple(
    q
    for theme in THEME_IDS
    for q in (
        QuestDef(
            id=f"main_{theme}_1",
            title=f"{theme}·第一桶水",
            kind="main",
            theme=theme,
            giver=_first_giver(theme),
            event="gather",
            target=5,
            reward_coins=120,
            reward_items=(("bread", 1),),
            reward_hearts_points=20,
            desc="先熟悉一下这片地方，采满 5 次东西。",
        ),
        QuestDef(
            id=f"main_{theme}_2",
            title=f"{theme}·第一件作品",
            kind="main",
            theme=theme,
            giver=_first_giver(theme),
            event="craft",
            target=2,
            reward_coins=260,
            reward_items=(("handicraft", 1),),
            reward_hearts_points=30,
            desc="做两样东西出来，手艺是住下来的本钱。",
        ),
        QuestDef(
            id=f"main_{theme}_3",
            title=f"{theme}·把日子过起来",
            kind="main",
            theme=theme,
            giver=_first_giver(theme),
            event="gift",
            target=3,
            reward_coins=500,
            reward_items=(("jam", 2),),
            reward_hearts_points=40,
            desc="给邻居送三份礼物，让这里的人认得你。",
        ),
    )
)


def _first_giver(theme: str) -> str:
    """主题的委托发起人（确定性：取该主题第一个有委托的 NPC）。

    在 `_MAIN` 构造时调用；`npcs` 已 import，`NPCS` 全局按 id 有序填充，
    这里按 (theme, id) 排序取第一个带委托的 NPC → 结果稳定。
    """
    from .npcs import _commission_for  # 局部导入避免循环

    for npc in sorted((n for n in NPCS.values() if n.theme == theme), key=lambda n: n.id):
        if _commission_for(npc):
            return npc.id
    raise KeyError(f"theme {theme!r} has no npc with commission")  # pragma: no cover


#: 支线（每主题 2 条 + 通用 2 条）
_SIDE: tuple[QuestDef, ...] = tuple(
    q
    for theme in THEME_IDS
    for q in (
        QuestDef(
            id=f"side_{theme}_gather",
            title="多采一点",
            kind="side",
            theme=theme,
            giver=_first_giver(theme),
            event="gather",
            target=12,
            reward_coins=180,
            desc="这阵子需求旺，多跑几趟。",
        ),
        QuestDef(
            id=f"side_{theme}_talk",
            title="和人聊聊天",
            kind="side",
            theme=theme,
            giver=_first_giver(theme),
            event="talk",
            target=4,
            reward_coins=90,
            reward_hearts_points=15,
            desc="跟四个人说说话（不同时间去内容不同）。",
        ),
    )
) + (
    QuestDef("side_common_clean", title="把院子扫干净", kind="side", theme="",
             giver="forest_woodsman", event="clean", target=3, reward_coins=140,
             desc="扫三下地，落灰的点会亮起来。"),
    QuestDef("side_common_trade", title="生意上的小事", kind="side", theme="",
             giver="field_baker", event="sell", target=5, reward_coins=220,
             desc="卖出去五件东西，试试经营这条路。"),
)

QUESTS: tuple[QuestDef, ...] = _MAIN + _SIDE
QUESTS_BY_ID: dict[str, QuestDef] = {q.id: q for q in QUESTS}


def get_quest(quest_id: str) -> QuestDef:
    try:
        return QUESTS_BY_ID[quest_id]
    except KeyError as exc:  # pragma: no cover
        raise KeyError(f"unknown quest {quest_id!r}; known={sorted(QUESTS_BY_ID)}") from exc


# ----------------------------------------------------------------------
# 任务日志
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class QuestEntry:
    quest_id: str
    progress: int = 0
    done: bool = False
    claimed: bool = False


@dataclass(frozen=True)
class QuestLog:
    entries: tuple[QuestEntry, ...] = ()
    day: int = 1

    def entry(self, quest_id: str) -> QuestEntry | None:
        for e in self.entries:
            if e.quest_id == quest_id:
                return e
        return None

    def progress_of(self, quest_id: str) -> int:
        e = self.entry(quest_id)
        return e.progress if e else 0

    def is_claimed(self, quest_id: str) -> bool:
        e = self.entry(quest_id)
        return bool(e and e.claimed)

    def active(self) -> tuple[QuestDef, ...]:
        out = []
        for e in self.entries:
            if e.claimed:
                continue
            q = QUESTS_BY_ID.get(e.quest_id)
            if q:
                out.append(q)
        return tuple(out)

    def claimed_ids(self) -> tuple[str, ...]:
        return tuple(sorted(e.quest_id for e in self.entries if e.claimed))


def start_log(theme: str) -> QuestLog:
    """新存档：接取该主题的第一条主线（其余任务按条件解锁）。"""
    first = get_quest(f"main_{theme}_1")
    return QuestLog(entries=(QuestEntry(first.id),), day=1)


def advance(log: QuestLog, event: str, amount: int = 1) -> tuple[QuestLog, list[dict[str, object]]]:
    """推进所有订阅该 event 的未完成任务。

    返回 (新 log, 变化列表)。**已领取的任务不再累计**（防止领完还涨）。
    """
    if amount <= 0:
        raise ValueError("amount must be > 0")
    entries = list(log.entries)
    changes: list[dict[str, object]] = []
    for i, e in enumerate(entries):
        q = QUESTS_BY_ID.get(e.quest_id)
        if q is None or q.event != event or e.claimed or e.done:
            continue
        new_progress = min(q.target, e.progress + amount)
        done = new_progress >= q.target
        entries[i] = replace(e, progress=new_progress, done=done)
        changes.append({"quest_id": q.id, "title": q.title, "progress": new_progress,
                        "target": q.target, "done": done})
    return QuestLog(entries=tuple(entries), day=log.day), changes


def accept(log: QuestLog, quest_id: str) -> QuestLog:
    """接任务：已在日志里则原样返回（不重复插入）。"""
    get_quest(quest_id)
    if log.entry(quest_id) is not None:
        return log
    return QuestLog(entries=log.entries + (QuestEntry(quest_id),), day=log.day)


def claim(log: QuestLog, quest_id: str) -> tuple[QuestLog, tuple[tuple[str, int], ...], int, int, str]:
    """领奖：未完成抛 ValueError；返回 (新 log, 物品, 金币, 好感点数, 文案)。"""
    q = get_quest(quest_id)
    e = log.entry(quest_id)
    if e is None:
        raise ValueError(f"没有接取任务 {quest_id!r}")
    if e.claimed:
        raise ValueError(f"{q.title} 已经领过奖了")
    if not e.done:
        raise ValueError(
            f"{q.title} 还没完成（{e.progress}/{q.target}），不能领奖"
        )
    entries = tuple(replace(x, claimed=True) if x.quest_id == quest_id else x
                    for x in log.entries)
    items = tuple(f"{m}×{n}" for m, n in q.reward_items)
    note = f"完成 {q.title}：+{q.reward_coins} 金币"
    if items:
        note += " / " + "、".join(items)
    if q.reward_hearts_points:
        note += f" / 好感 +{q.reward_hearts_points}"
    return QuestLog(entries=entries, day=log.day), q.reward_items, q.reward_coins, \
        q.reward_hearts_points, note


# ----------------------------------------------------------------------
# 感叹号
# ----------------------------------------------------------------------

MARKER_OFFER = "!"   # 头顶感叹号：有可接
MARKER_TURN_IN = "?"  # 可交付（金色问号）
MARKER_DOING = "·"   # 进行中，无标记
MARKER_NONE = ""


def marker_for(log: QuestLog, npc_id: str) -> str:
    """NPC 头顶标记。

    规则（可复算）：
      1) 该 NPC 名下有 `done and not claimed` → `?`
      2) 该 NPC 名下有 `not done`（含新可接的支线）→ `!`
      3) 否则无标记
    """
    owned = [QUESTS_BY_ID[e.quest_id] for e in log.entries
             if e.quest_id in QUESTS_BY_ID]
    mine = [e for e in log.entries if e.quest_id in QUESTS_BY_ID
            and QUESTS_BY_ID[e.quest_id].giver == npc_id]
    if any(e.done and not e.claimed for e in mine):
        return MARKER_TURN_IN
    if mine:
        return MARKER_DOING
    # 未接但该 NPC 有可接任务 → 感叹号
    available = [q for q in QUESTS if q.giver == npc_id and log.entry(q.id) is None]
    if available:
        return MARKER_OFFER
    del owned
    return MARKER_NONE


def markers(log: QuestLog, theme: str) -> dict[str, str]:
    """整图标记一览（NPC id → 标记）。"""
    return {n.id: marker_for(log, n.id) for n in sorted(NPCS.values(), key=lambda n: n.id)
            if n.theme == theme}


def available_quests(theme: str, level: int = 1) -> tuple[QuestDef, ...]:
    """某主题当前可接任务（通用 + 本主题，主线优先）。

    未知主题直接抛错——不静默只返回通用任务，否则「切错图」会看起来完全正常。
    """
    from .themes import get_theme

    get_theme(theme)
    pool = [q for q in QUESTS if q.theme in ("", theme)]
    return tuple(sorted(pool, key=lambda q: (q.kind != "main", q.id)))


def offer_of_the_day(theme: str, day: int, level: int = 1) -> tuple[QuestDef, ...]:
    """每日委托轮换（同一天同主题 → 同一批，可复算）。"""
    pool = [q for q in available_quests(theme, level) if q.kind == "side"]
    if not pool:
        return ()
    rng = rng_for("offer", theme, day)
    idx = rng.randrange(len(pool))
    return (pool[idx],)
