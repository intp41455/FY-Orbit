"""数码小屋室内布置持久化 (W1 · cabin_interiors).

One row per ``(owner_id, house_id)`` holds that house template's furniture
layout. The frontend mirrors the payload in localStorage for instant paint,
but **the backend row is the source of truth** (task book §2.4).

Contract with the frontend (``web/src/components/cabin/interior/``):

* ``furniture_id`` must exist in the shared ``FURNITURE_CATALOG`` whitelist —
  the id list below is a verbatim copy of the frontend registry, kept in sync
  by ``tests/unit/test_cabin_interior.py::test_catalog_matches_frontend``.
* coordinates are 16px grid cells, clamped to the room; ``mount`` decides the
  legal band (floor / wall / ceiling).
* ``version`` is an optimistic lock: a PUT carrying a stale version is
  rejected with 409 rather than silently overwriting another tab's work.

Security (FROZEN_CONTRACT §1/§3.2): every read/write is owner-scoped; a row
belonging to another owner is reported as 404, never as 403, so the endpoint
does not confirm the existence of another tenant's object. The service takes an
:class:`Actor` and never reads ``owner_id`` from the request body.

Honesty: unknown furniture ids, oversized payloads and over-capacity layouts
are **rejected with explicit errors**; nothing is silently dropped server-side
(the lenient per-item repair pass exists only on the frontend for legacy rows).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.types import utcnow
from ..db.workbench_models import CabinExteriorRemoved, CabinInterior
from .actor import Actor
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed

# --- 共享契约：家具白名单（与前端 furnitureCatalog.ts 的 FURNITURE_CATALOG 一一对应）

#: furniture id -> (label, sizeCells w, sizeCells h, mount)
FURNITURE_CATALOG: dict[str, tuple[str, int, int, str]] = {
    "bed": ("床", 3, 2, "floor"),
    "table": ("餐桌", 2, 2, "floor"),
    "chair": ("椅子", 1, 1, "floor"),
    "bookshelf": ("书架", 2, 2, "floor"),
    "floor_lamp": ("落地灯", 1, 2, "floor"),
    "rug": ("地毯", 3, 2, "floor"),
    "plant": ("盆栽", 1, 1, "floor"),
    "cabinet": ("柜子", 2, 1, "floor"),
    "cat_bed": ("猫窝", 1, 1, "floor"),
    "fish_tank": ("鱼缸", 2, 1, "floor"),
    "crate": ("木箱", 1, 1, "floor"),
    "picture_frame": ("画框", 1, 1, "wall"),
    # W11 衔接：更衣镜（角色工坊入口）。开局可得，与前端 furnitureCatalog 同 id。
    "mirror": ("更衣镜", 1, 2, "floor"),
    "ceiling_lamp": ("吊灯", 2, 1, "ceiling"),
    "wall_shelf": ("壁架", 1, 1, "wall"),
    "rug_large": ("大块地毯", 4, 3, "floor"),
    "stove": ("炉子", 2, 2, "floor"),
    "crystal_tree": ("水晶树", 2, 3, "floor"),
    "snow_lamp": ("冰晶灯", 1, 2, "floor"),
    "herb_shelf": ("草药架", 2, 1, "wall"),
}

#: 房屋模板 id（与前端 CABIN_HOUSES 一致）。
HOUSE_IDS: frozenset[str] = frozenset(
    {"villa", "cabin", "cave", "snowcave", "bunker", "castle"}
)

# 网格与房间尺寸（与前端 interiorLayout.ts 的 GRID / ROOM_* 一致）。
GRID = 16
FLOOR_Y = 96
ROOM_COLS = 30
ROOM_ROWS = 10

# 落位方式对应的合法 y 范围（格）。
MOUNT_BANDS: dict[str, tuple[int, int]] = {
    "floor": (0, ROOM_ROWS - 1),
    "wall": (0, 3),
    "ceiling": (0, 1),
}

MAX_ITEMS = 60
MAX_LAYOUT_BYTES = 64 * 1024
MAX_COLORWAY = 8


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def validate_house_id(house_id: str) -> str:
    if not isinstance(house_id, str) or house_id not in HOUSE_IDS:
        raise ValidationFailed(
            "cabin_unknown_house",
            f"Unknown house template: {house_id!r}",
        )
    return house_id


def validate_layout(layout: Any, *, house_id: str) -> dict[str, Any]:
    """Strictly validate a client layout payload.

    Rejects (never silently repairs): unknown furniture ids, non-integer or
    out-of-range coordinates, unknown mounts, oversized payloads and layouts
    holding more than :data:`MAX_ITEMS` pieces.
    """
    if not isinstance(layout, dict):
        raise ValidationFailed("cabin_invalid_layout", "layout must be a JSON object")
    items = layout.get("items")
    if not isinstance(items, list):
        raise ValidationFailed("cabin_invalid_layout", "layout.items must be a list")
    if len(items) > MAX_ITEMS:
        raise ValidationFailed(
            "cabin_layout_too_many_items",
            f"Layout holds {len(items)} items; the limit is {MAX_ITEMS}",
        )

    # 体积上限（含容器开销）—— 超限直接拒绝，不做「截断后保存」。
    if len(json.dumps(layout, ensure_ascii=False).encode("utf-8")) > MAX_LAYOUT_BYTES:
        raise ValidationFailed(
            "cabin_layout_too_large",
            f"Layout exceeds the {MAX_LAYOUT_BYTES}-byte limit",
        )

    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ValidationFailed("cabin_invalid_item", f"items[{index}] must be an object")
        furniture_id = raw.get("furnitureId", raw.get("furniture_id"))
        if not isinstance(furniture_id, str) or furniture_id not in FURNITURE_CATALOG:
            raise ValidationFailed(
                "cabin_unknown_furniture",
                f"items[{index}].furnitureId {furniture_id!r} is not in the catalog",
            )
        _, cw, ch, mount = FURNITURE_CATALOG[furniture_id]

        item_id = raw.get("id")
        if not isinstance(item_id, str) or not item_id or len(item_id) > 64:
            raise ValidationFailed(
                "cabin_invalid_item_id", f"items[{index}].id must be a 1..64 char string"
            )
        if item_id in seen:
            raise ValidationFailed("cabin_duplicate_item_id", f"Duplicate item id {item_id!r}")
        seen.add(item_id)

        x, y = raw.get("x"), raw.get("y")
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            raise ValidationFailed(
                "cabin_invalid_coord", f"items[{index}] coordinates must be integers"
            )
        band_lo, band_hi = MOUNT_BANDS[mount]
        # 宽度/高度越界说明客户端与服务端网格认知不一致 -> 明确报错。
        if cw > ROOM_COLS or ch > ROOM_ROWS:
            raise ValidationFailed("cabin_furniture_too_large", f"{furniture_id} exceeds the room")
        x = _clamp(x, 0, ROOM_COLS - cw)
        y = _clamp(y, band_lo, max(band_lo, band_hi - ch + 1))

        colorway = raw.get("colorway", 0)
        if not isinstance(colorway, int) or isinstance(colorway, bool) or not (0 <= colorway < MAX_COLORWAY):
            raise ValidationFailed(
                "cabin_invalid_colorway",
                f"items[{index}].colorway must be an integer in [0, {MAX_COLORWAY})",
            )
        z = raw.get("z", index)
        if not isinstance(z, int) or isinstance(z, bool):
            raise ValidationFailed("cabin_invalid_z", f"items[{index}].z must be an integer")

        clean.append(
            {
                "id": item_id,
                "furnitureId": furniture_id,
                "x": x,
                "y": y,
                "flipped": bool(raw.get("flipped", False)),
                "colorway": colorway,
                "z": z,
            }
        )

    return {"houseId": house_id, "items": clean}


class CabinInteriorService:
    """Owner-scoped CRUD for per-house interior layouts."""

    def __init__(self, db: Session, audit: Any | None = None):
        self._db = db
        self._audit = audit

    # -- helpers ------------------------------------------------------------
    def _require_owner(self, actor: Actor) -> str:
        actor.require_authenticated()
        if actor.subject_type != "owner":
            raise PermissionDenied("owner_only", "Cabin interiors are owner-private", 403)
        if not actor.owner_id:
            raise PermissionDenied("owner_required", "Missing owner identity", 403)
        return actor.owner_id

    def _row(self, owner_id: str, house_id: str) -> CabinInterior | None:
        return self._db.execute(
            select(CabinInterior).where(
                CabinInterior.owner_id == owner_id, CabinInterior.house_id == house_id
            )
        ).scalar_one_or_none()

    # -- API ----------------------------------------------------------------
    def get_interior(self, actor: Actor, house_id: str) -> dict[str, Any]:
        """Return the stored layout, or a ``defaulted`` marker when unset.

        The backend does **not** fabricate a default layout: it reports
        ``defaulted=True`` and the client renders its own deterministic
        default. That keeps a single source of truth for default content.
        """
        owner_id = self._require_owner(actor)
        validate_house_id(house_id)
        row = self._row(owner_id, house_id)
        if row is None:
            return {
                "house_id": house_id,
                "layout": {"houseId": house_id, "items": [], "version": 0},
                "version": 0,
                "defaulted": True,
            }
        return {
            "house_id": house_id,
            "layout": row.layout,
            "version": row.version,
            "defaulted": False,
        }

    def put_interior(
        self, actor: Actor, house_id: str, layout: Any, expected_version: int
    ) -> dict[str, Any]:
        """Create or update a layout under an optimistic lock.

        ``expected_version`` semantics:

        * ``0`` means "I know there is no stored layout yet" — creating it is
          allowed; if a row already exists the write is a 409 (someone else
          created it first).
        * ``n > 0`` requires the stored row to still be at version ``n``,
          otherwise 409 ``cabin_version_conflict``.
        """
        owner_id = self._require_owner(actor)
        validate_house_id(house_id)
        if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 0:
            raise ValidationFailed(
                "cabin_invalid_version", "expected_version must be a non-negative integer"
            )
        clean = validate_layout(layout, house_id=house_id)

        row = self._row(owner_id, house_id)
        if row is None:
            if expected_version != 0:
                # The client believes a layout exists but there is none.
                raise Conflict(
                    "cabin_version_conflict",
                    f"Stored layout is at version 0, client sent {expected_version}",
                    409,
                )
            row = CabinInterior(
                id=f"cab_{uuid.uuid4().hex}",
                owner_id=owner_id,
                house_id=house_id,
                layout=clean,
                version=1,
            )
            self._db.add(row)
            self._db.commit()
            self._db.refresh(row)
            return self._response(row, defaulted=False)

        if row.version != expected_version:
            raise Conflict(
                "cabin_version_conflict",
                f"Stored layout is at version {row.version}, client sent {expected_version}",
                409,
            )
        row.layout = clean
        row.version += 1
        self._db.commit()
        self._db.refresh(row)
        return self._response(row, defaulted=False)

    def delete_interior(self, actor: Actor, house_id: str, expected_version: int) -> dict[str, Any]:
        owner_id = self._require_owner(actor)
        validate_house_id(house_id)
        row = self._row(owner_id, house_id)
        if row is None:
            raise NotFound("cabin_interior_not_found", f"No interior layout for house {house_id}", 404)
        if row.version != expected_version:
            raise Conflict(
                "cabin_version_conflict",
                f"Stored layout is at version {row.version}, client sent {expected_version}",
                409,
            )
        self._db.delete(row)
        self._db.commit()
        return {"house_id": house_id, "deleted": True}

    @staticmethod
    def _response(row: CabinInterior, *, defaulted: bool) -> dict[str, Any]:
        return {
            "house_id": row.house_id,
            "layout": row.layout,
            "version": row.version,
            "defaulted": defaulted,
        }


# ---------------------------------------------------------------------------
# 室外：已移除家具黑名单
# ---------------------------------------------------------------------------

#: 室外默认清单里允许被移除的家具 id。
#: 与前端 cabinPixelArt.ts 的 DEFAULT_PLACED_FURNITURE 一一对应。
EXTERIOR_REMOVABLE: frozenset[str] = frozenset(
    {
        "b4_writing_desk",
        "b4_stool",
        "b4_single_bed",
        "b4_rug_small",
        "b4_floor_lamp",
        "b4_pot_plant",
    }
)

MAX_REMOVED_IDS = 64
MAX_REMOVED_BYTES = 4 * 1024


class CabinExteriorService:
    """Persist *which* exterior furniture the owner removed.

    存黑名单而非整份摆放清单：默认清单会随版本新增家具，若存整份清单，
    旧存档会把后来新增的家具永久藏掉，且用户没有任何界面能把它找回来。
    黑名单只表达「用户明确删掉了这几件」，新增家具自动出现。

    安全与契约对齐 ``CabinInteriorService``：
    * 按 (owner_id, house_id) 隔离；他人存档报404 而非 403（不确认存在性）。
    * version 乐观锁：过期版本 409，避免多标签页互相覆盖。
    * 未知家具 id 显式报错，不静默丢弃。
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _row(self, actor: Actor, house_id: str):
        if house_id not in HOUSE_IDS:
            raise NotFound("unknown_house", f"未知房屋模板：{house_id}")
        return (
            self.db.execute(
                select(CabinExteriorRemoved).where(
                    CabinExteriorRemoved.owner_id == actor.owner_id,
                    CabinExteriorRemoved.house_id == house_id,
                )
            )
            .scalars()
            .first()
        )

    def get_exterior(self, actor: Actor, house_id: str) -> dict:
        row = self._row(actor, house_id)
        if row is None:
            return {"house_id": house_id, "removed_ids": [], "version": 0}
        return {
            "house_id": house_id,
            "removed_ids": list(row.payload.get("removed_ids", [])),
            "version": row.version,
        }

    def put_exterior(
        self, actor: Actor, house_id: str, removed_ids: list[str], expected_version: int
    ) -> dict:
        actor.require_authenticated()

        unknown = [i for i in removed_ids if i not in EXTERIOR_REMOVABLE]
        if unknown:
            raise ValidationFailed(
                "unknown_furniture",
                f"这些家具不可移除或不存在：{', '.join(sorted(unknown))}",
            )
        if len(removed_ids) > MAX_REMOVED_IDS:
            raise ValidationFailed(
                "too_many_removed", f"移除记录过多（上限 {MAX_REMOVED_IDS}）"
            )
        # 去重并排序，保证同内容同版本，便于测试断言与版本比较。
        normalized = sorted(set(removed_ids))
        payload = {"removed_ids": normalized}
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_REMOVED_BYTES:
            raise ValidationFailed("payload_too_large", "移除清单过大")

        row = self._row(actor, house_id)
        if row is None:
            if expected_version != 0:
                raise Conflict(
                    "version_mismatch",
                    f"存档不存在（期望版本 {expected_version}）",
                )
            row = CabinExteriorRemoved(
                id=str(uuid.uuid4()),
                owner_id=actor.owner_id,
                house_id=house_id,
                payload=payload,
                version=1,
            )
            self.db.add(row)
        else:
            if row.version != expected_version:
                raise Conflict(
                    "version_mismatch",
                    f"版本冲突：期望 {expected_version}，实际 {row.version}",
                )
            row.payload = payload
            row.version += 1
            row.updated_at = utcnow()
        self.db.commit()
        return {
            "house_id": house_id,
            "removed_ids": normalized,
            "version": row.version,
        }

    def delete_exterior(self, actor: Actor, house_id: str, expected_version: int) -> dict:
        """清空黑名单，恢复全部室外家具。"""
        row = self._row(actor, house_id)
        if row is None:
            return {"house_id": house_id, "removed_ids": [], "version": 0}
        if row.version != expected_version:
            raise Conflict(
                "version_mismatch", f"版本冲突：期望 {expected_version}，实际 {row.version}"
            )
        self.db.delete(row)
        self.db.commit()
        return {"house_id": house_id, "removed_ids": [], "version": 0}
