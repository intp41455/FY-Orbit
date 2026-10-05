"""B 包 · HTTP 服务层（服务端权威结算）。

`cabin_life` 的纯函数模块只负责「给定输入算出结果」，本模块负责把它们串成
**有身份的、有事务的**动作入口：先按 actor 取存档，再跑纯函数，再落库。

与 W2（`services/cabin_gameplay.py`）同构的关键约束：

* **服务端权威**：客户端不能直接写金币/背包/好感/技能经验。只有 ``/action``
  里的白名单动作能改动它们，每个动作的数值都由 `cabin_life` 纯函数算出。
  ``PUT`` 只接受玩家偏好字段，其余字段一律 422。
* **动作白名单**：``ACTIONS`` 是唯一入口表；未知 action → 422 ``life_unknown_action``。
* **纯函数抛错即 422**：``ValueError`` / ``KeyError`` 来自规则层，转成带原因的 422，
  **不**转成「假装成功的空操作」。

时钟与天气是派生的：每次改动时钟都会重算天气块（规则同样来自 `clock` 模块），
所以存档里存的天气永远是「与当前 day/minute 自洽」的那一份。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session

from ..actor import Actor
from ..errors import NotFound, PermissionDenied, ValidationFailed
from . import clock as clock_mod
from . import crafting, interaction, npcs, persistence, quests, shop, state, themes
from .clock import GameClock
from .persistence import LifeSaveRow
from .shop import ShopState
from .state import LifeSave

#: `POST /api/cabin/life/action` 唯一合法的 action 集合。
ACTIONS: frozenset[str] = frozenset(
    {
        "gather",       # 采集
        "craft",        # 制作
        "restock",      # 进货
        "sleep",        # 睡觉 = 日结算 + 跳到次日 06:00
        "gift",         # 送礼
        "accept_quest", # 接任务
        "claim_quest",  # 领奖
        "shop_upgrade", # 店铺升级
        "set_price",    # 改定价
    }
)

#: `PUT /api/cabin/life/save` 唯一允许客户端写入的键。其余一律 422。
WRITABLE_SETTINGS: frozenset[str] = frozenset({"active_theme"})


def _log_to_dict(log: quests.QuestLog) -> dict[str, object]:
    return {"day": log.day, "entries": [asdict(e) for e in log.entries]}


def _shop_to_dict(st: ShopState) -> dict[str, object]:
    return asdict(st)


class LifeService:
    """B 包存档与动作的服务端权威入口。"""

    def __init__(self, db: Session):
        self._db = db

    # ---------------- 身份与存档 ----------------

    def _require_owner(self, actor: Actor) -> str:
        """存档是 owner 私有：服务身份一律 403（与 W2 同一口径）。"""
        actor.require_authenticated()
        if actor.subject_type != "owner":
            raise PermissionDenied(
                "owner_only", "Life saves are owner-private", 403
            )
        if not actor.owner_id:
            raise PermissionDenied("owner_required", "Missing owner identity", 403)
        return actor.owner_id

    def _row(self, owner: str) -> LifeSaveRow:
        row = persistence.fetch(self._db, owner)
        if row is None:
            raise NotFound(
                "life_save_missing",
                f"owner {owner!r} 还没有 B 包存档，请先 POST 创建",
            )
        return row

    def _clock_of(self, save: LifeSave) -> GameClock:
        return GameClock(day=int(save.clock["day"]), minute=int(save.clock["minute"]))

    def _shop_of(self, save: LifeSave) -> ShopState:
        return ShopState(**save.shop)  # type: ignore[arg-type]

    def _weather_block(self, save: LifeSave, c: GameClock) -> dict[str, object]:
        """按当前时钟重算天气块（公式与 `state._weather_block` 同源）。"""
        w = clock_mod.weather_of(c, save.owner, save.theme)
        return {
            "id": w.id,
            "label": w.label,
            "icon": w.icon,
            "light": clock_mod.light_level(c, save.owner, save.theme),
            "yield_pct": w.yield_pct,
            "pace_pct": w.pace_pct,
            "demand_pct": w.demand_pct,
            "blocks_gather": w.blocks_gather,
        }

    def _sync_clock(self, save: LifeSave, c: GameClock) -> None:
        """把 `GameClock` 写回存档，并让天气与它保持自洽。"""
        save.clock = {
            "day": c.day,
            "minute": c.minute,
            "part": c.part,
            "part_label": c.part_label,
        }
        save.weather = self._weather_block(save, c)

    def _save(self, save: LifeSave) -> LifeSave:
        """落库并自增版本号（乐观锁）。"""
        save.version += 1
        persistence.store(self._db, save)
        self._db.commit()
        return save

    # ---------------- 读 ----------------

    def snapshot(
        self, actor: Actor, *, player_tile: tuple[int, int] | None = None
    ) -> dict[str, object]:
        """完整面板快照：存档 + HUD 文案 + 社交/经营/制作/采集一览。

        一次性返回所有面板数据，是为了让前端一次请求就能画完整个 HUD，
        也就没有「四个请求结果互相不一致」的中间态可渲染。
        """
        owner = self._require_owner(actor)
        save = persistence.read_save(self._row(owner))
        return {
            "save": state.to_dict(save),
            "hud_line": state.hud_line(save),
            "npcs": state.npc_rows(save),
            "shop": state.shop_rows(save),
            "craft": state.craft_rows(save),
            "gather": state.gather_rows(save, player_tile),
            "version": save.version,
        }

    def create(self, actor: Actor, *, theme: str, day: int = 1) -> dict[str, object]:
        """建新档。已存在则 422（不覆盖已有进度）。"""
        owner = self._require_owner(actor)
        if theme not in themes.theme_ids():
            raise ValidationFailed(
                "life_unknown_theme",
                f"未知主题 {theme!r}，可用：{', '.join(themes.theme_ids())}",
            )
        if day < 1:
            raise ValidationFailed("life_bad_day", f"day 必须 >= 1，收到 {day}")
        if persistence.fetch(self._db, owner) is not None:
            raise ValidationFailed(
                "life_save_exists",
                f"owner {owner!r} 已有存档；重建会丢进度，故拒绝。如需重开请先 DELETE。",
            )
        save = state.new_save(owner, theme, day=day)
        self._save(save)
        return {"save": state.to_dict(save), "hud_line": state.hud_line(save)}

    def delete(self, actor: Actor) -> dict[str, object]:
        """删档（显式动作；用于「重开一周目」，不做任何静默清理）。"""
        owner = self._require_owner(actor)
        self._db.delete(self._row(owner))
        self._db.commit()
        return {"deleted": True, "owner": owner}

    def put_settings(self, actor: Actor, payload: dict[str, Any]) -> dict[str, object]:
        """只写玩家偏好。客户端提交数值字段 → 422（服务端权威）。"""
        owner = self._require_owner(actor)
        save = persistence.read_save(self._row(owner))

        forbidden = sorted(
            k
            for k in payload
            if k not in WRITABLE_SETTINGS
            and k not in {"settings", "expected_version"}
        )
        if forbidden:
            raise ValidationFailed(
                "life_server_authoritative",
                "金币/背包/好感/技能经验只能由 /action 结算，PUT 不接受："
                + ", ".join(forbidden),
            )
        expected = payload.get("expected_version")
        if expected is not None and int(expected) != save.version:
            raise ValidationFailed(
                "life_version_conflict",
                f"存档版本已变（当前 {save.version}，你基于 {int(expected)}），请重新读取",
            )

        settings = payload.get("settings") or {}
        if not isinstance(settings, dict):
            raise ValidationFailed("life_bad_settings", "settings 应为对象")
        new_theme = settings.get("active_theme")
        if new_theme is not None:
            if new_theme not in themes.theme_ids():
                raise ValidationFailed(
                    "life_unknown_theme",
                    f"未知主题 {new_theme!r}，可用：{', '.join(themes.theme_ids())}",
                )
            # 换主题 = 换一套内容（NPC/配方/地图），存档随之重建；
            # 金币、背包、技能经验、任务进度**保留** —— 那是玩家挣来的，
            # 不该因为换个画风就被清零。
            old = save
            save = state.new_save(owner, str(new_theme), day=int(old.clock["day"]))
            # 版本必须跟着存档走（否则会回退，使优情锁失效）。
            save.version = old.version
            save.coins = old.coins
            save.bag = dict(old.bag)
            save.skill_exp = old.skill_exp
            save.quest_log = dict(old.quest_log)
            save.gather_counts = dict(old.gather_counts)
            save.affinity = dict(old.affinity)
            save.gifts_today = dict(old.gifts_today)
        self._save(save)
        return {"save": state.to_dict(save), "hud_line": state.hud_line(save)}

    # ---------------- 写：唯一动作入口 ----------------

    def action(self, actor: Actor, payload: dict[str, Any]) -> dict[str, Any]:
        """服务端权威动作入口。未知 action → 422；规则层报错 → 422 带原因。"""
        owner = self._require_owner(actor)
        name = str(payload.get("action") or "")
        if name not in ACTIONS:
            raise ValidationFailed(
                "life_unknown_action",
                f"未知 action {name!r}；可用：{', '.join(sorted(ACTIONS))}",
            )
        save = persistence.read_save(self._row(owner))
        handler = getattr(self, f"_act_{name}")
        try:
            result = handler(save, payload)
        except (ValueError, KeyError) as exc:
            # 规则层拒绝：材料不足 / 金币不足 / 天气禁采集 / 未解锁 / 主题不符……
            # 一律 422 并把原因原样带给客户端；**不落任何改动**。
            self._db.rollback()
            raise ValidationFailed(f"life_{name}_rejected", str(exc)) from exc
        self._save(save)
        out: dict[str, Any] = {"action": name, "save": state.to_dict(save)}
        out.update(result)
        out["hud_line"] = state.hud_line(save)
        return out

    # -- 采集 --

    def _act_gather(self, save: LifeSave, payload: dict[str, Any]) -> dict[str, Any]:
        node_id = str(payload.get("node_id") or "")
        c = self._clock_of(save)
        counts = dict(save.gather_counts)
        receipt = interaction.gather(
            save.owner, save.theme, node_id, c, dict(save.bag),
            times_today=int(counts.get(node_id, 0)),
        )
        save.bag = receipt.bag_after
        self._sync_clock(save, receipt.clock_after)
        counts[node_id] = int(counts.get(node_id, 0)) + 1
        save.gather_counts = counts
        log, changes = quests.advance(state.log_from_dict(save.quest_log), "gather")
        save.quest_log = _log_to_dict(log)
        return {
            "note": receipt.note,
            "material": receipt.material,
            "qty": receipt.qty,
            "minutes": receipt.minutes,
            "animation": receipt.animation,
            "gatherable_rows": interaction.gatherable_rows(save.theme),
            "quest_progress": changes,
        }

    # -- 制作 --

    def _act_craft(self, save: LifeSave, payload: dict[str, Any]) -> dict[str, Any]:
        recipe_id = str(payload.get("recipe_id") or "")
        level = crafting.skill_level(save.skill_exp)
        receipt = crafting.craft(
            recipe_id, dict(save.bag),
            coins=save.coins, level=level, theme=save.theme,
        )
        save.bag = receipt.bag_after
        save.coins = receipt.coins_after
        save.skill_exp += receipt.exp_gained
        log, changes = quests.advance(state.log_from_dict(save.quest_log), "craft")
        save.quest_log = _log_to_dict(log)
        return {
            "note": receipt.note,
            "outputs": [{"id": m, "qty": n} for m, n in receipt.outputs],
            "spent": [{"id": m, "qty": n} for m, n in receipt.spent],
            "exp_gained": receipt.exp_gained,
            "level": crafting.skill_level(save.skill_exp),
            "craft_rows": state.craft_rows(save),
            "quest_progress": changes,
        }

    # -- 进货 --

    def _act_restock(self, save: LifeSave, payload: dict[str, Any]) -> dict[str, Any]:
        good_id = str(payload.get("good_id") or "")
        qty = int(payload.get("qty") or 0)
        c = self._clock_of(save)
        st = self._shop_of(save)
        receipt = shop.restock(
            save.owner, good_id, c.day, qty,
            coins=save.coins, stock=st.stock_of(good_id),
        )
        save.coins = receipt.coins_after
        save.shop = _shop_to_dict(
            ShopState(
                kind=st.kind,
                level=st.level,
                stock={**st.stock, good_id: receipt.stock_after},
                ask_prices=st.ask_prices,
            )
        )
        return {
            "note": f"进货 {receipt.good_id}×{receipt.qty}，花费 {receipt.cost} 金币",
            "cost": receipt.cost,
            "unit_buy": receipt.unit_buy,
            "shop_rows": state.shop_rows(save),
        }

    # -- 睡觉 = 日结算 + 跨日 --

    def _act_sleep(self, save: LifeSave, payload: dict[str, Any]) -> dict[str, Any]:
        c = self._clock_of(save)
        nxt = c.slept_to_next_morning()
        st = self._shop_of(save)
        result = shop.daily_settlement(
            save.owner, st, c, save.theme,
            coins=save.coins, next_clock=nxt,
        )
        save.coins = int(result["coins_after"])
        save.shop = _shop_to_dict(result["shop"])  # type: ignore[arg-type]
        self._sync_clock(save, nxt)
        # 跨日重置：每日计数与今日送礼次数。金币、背包、好感、任务进度**不清零**。
        save.gather_counts = {}
        save.gifts_today = {}
        old_log = state.log_from_dict(save.quest_log)
        save.quest_log = _log_to_dict(
            quests.QuestLog(entries=old_log.entries, day=nxt.day)
        )
        return {
            "settlement": {k: v for k, v in result.items() if k != "shop"},
            "weather": save.weather,
            "note": f"睡到第 {nxt.day} 天 06:00；没卖出的货品如实留在仓库。",
        }

    # -- 送礼 --

    def _act_gift(self, save: LifeSave, payload: dict[str, Any]) -> dict[str, Any]:
        npc_id = str(payload.get("npc_id") or "")
        gift = str(payload.get("gift_id") or "")
        npc = npcs.get_npc(npc_id)
        if npc.theme != save.theme:
            raise ValueError(
                f"{npc.name} 不在当前主题 {save.theme} 里（属于 {npc.theme}）"
            )
        base = npcs.gift_points(npc, gift)
        if base == 0:
            # 厌恶的礼物：明说「不加好感」并拒绝，而不是收下装作无事发生。
            kind = "喜欢" if gift in npc.likes else "讨厌"
            raise ValueError(f"{npc.name}{kind}这个（好感不会增加，本次未送出）")

        affinity = dict(save.affinity)
        gifts_today = dict(save.gifts_today)
        points, hearts, delta, note = npcs.apply_gift(
            npc, gift, int(affinity.get(npc_id, 0)),
            gifts_today=int(gifts_today.get(npc_id, 0)),
        )
        if delta == 0:
            # 每日上限已满：好感不变，且**不**记一次送礼
            # （否则明天会被误判成「今天已经送过了」）。
            raise ValueError(note)
        affinity[npc_id] = points
        gifts_today[npc_id] = int(gifts_today.get(npc_id, 0)) + 1
        save.affinity = affinity
        save.gifts_today = gifts_today
        log, changes = quests.advance(state.log_from_dict(save.quest_log), "gift")
        save.quest_log = _log_to_dict(log)
        return {
            "note": note,
            "hearts": hearts,
            "hearts_display": state.hearts_display(save, npc_id),
            "delta": delta,
            "npc_rows": state.npc_rows(save),
            "quest_progress": changes,
        }

    # -- 任务 --

    def _act_accept_quest(
        self, save: LifeSave, payload: dict[str, Any]
    ) -> dict[str, Any]:
        quest_id = str(payload.get("quest_id") or "")
        log = quests.accept(state.log_from_dict(save.quest_log), quest_id)
        save.quest_log = _log_to_dict(log)
        return {"note": f"接取任务 {quest_id}", "npc_rows": state.npc_rows(save)}

    def _act_claim_quest(
        self, save: LifeSave, payload: dict[str, Any]
    ) -> dict[str, Any]:
        quest_id = str(payload.get("quest_id") or "")
        giver = payload.get("giver")
        log, items, coins, hearts, note = quests.claim(
            state.log_from_dict(save.quest_log), quest_id
        )
        bag = dict(save.bag)
        for material, count in items:
            bag[material] = int(bag.get(material, 0)) + count
        save.bag = bag
        save.coins += coins
        if giver and hearts:
            # 领奖好感记给发布任务的那个 NPC（hearts 是**点数**，不是心数）。
            affinity = dict(save.affinity)
            affinity[str(giver)] = int(affinity.get(str(giver), 0)) + hearts
            save.affinity = affinity
        save.quest_log = _log_to_dict(log)
        return {
            "note": note,
            "reward_items": [{"id": m, "qty": n} for m, n in items],
            "reward_coins": coins,
            "reward_hearts_points": hearts,
            "npc_rows": state.npc_rows(save),
        }

    # -- 店铺 --

    def _act_shop_upgrade(
        self, save: LifeSave, payload: dict[str, Any]
    ) -> dict[str, Any]:
        st = self._shop_of(save)
        new_shop, coins, reason = shop.upgrade(st, save.coins)
        save.shop = _shop_to_dict(new_shop)
        save.coins = coins
        return {"note": reason, "shop_rows": state.shop_rows(save)}

    def _act_set_price(
        self, save: LifeSave, payload: dict[str, Any]
    ) -> dict[str, Any]:
        good_id = str(payload.get("good_id") or "")
        price = int(payload.get("price") or 0)
        st = self._shop_of(save)
        save.shop = _shop_to_dict(shop.set_ask_price(st, good_id, price))
        return {
            "note": f"{shop.get_good(good_id).label} 定价改为 {price}",
            "shop_rows": state.shop_rows(save),
        }
