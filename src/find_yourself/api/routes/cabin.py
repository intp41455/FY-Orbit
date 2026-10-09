"""数码小屋室内布置 HTTP surface (W1).

* ``GET    /api/cabin/interiors/{house_id}`` — read this owner's layout for one
  house template. Answers ``defaulted: true`` (version 0) when nothing is
  stored yet; the client then renders its deterministic default.
* ``PUT    /api/cabin/interiors/{house_id}`` — create/update the layout under an
  optimistic lock (``expected_version``); a stale version is 409.
* ``DELETE /api/cabin/interiors/{house_id}`` — drop the layout back to default.
* ``GET    /api/cabin/furniture`` — the shared furniture registry, so the client
  never hardcodes a second, divergent copy of the catalog.

Auth model: reads require a verified session; writes additionally pass the
CSRF/Origin gate (``csrf_protected``), and the service enforces owner-only
access — a service identity gets 403, another owner's row is 404.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...services.actor import Actor
from ...services.cabin_interior import (
    FURNITURE_CATALOG,
    HOUSE_IDS,
    MAX_ITEMS,
    MAX_LAYOUT_BYTES,
    CabinExteriorService,
    CabinInteriorService,
)
from ..deps import csrf_protected, get_actor, get_db

router = APIRouter(prefix="/api/cabin", tags=["cabin"])


class InteriorPutBody(BaseModel):
    # extra="forbid": an unknown field is a client bug, not something to ignore.
    model_config = ConfigDict(extra="forbid")

    layout: dict[str, Any] = Field(description="Furniture layout payload")
    expected_version: int = Field(
        ge=0,
        description="Optimistic lock: 0 = must not exist yet, n = must be at version n",
    )


class ExteriorPutBody(BaseModel):
    """室外家具移除清单（黑名单）。"""

    # extra="forbid": unknown field is a client bug, not something to ignore.
    model_config = ConfigDict(extra="forbid")

    removed_ids: list[str] = Field(
        default_factory=list,
        description="用户明确移除的室外家具 id（黑名单）",
    )
    expected_version: int = Field(
        ge=0,
        description="Optimistic lock: 0 = must not exist yet, n = must be at version n",
    )


@router.get("/exterior/{house_id}")
async def get_exterior(
    house_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    """Read which exterior furniture items the owner removed (blacklist)."""
    return CabinExteriorService(db).get_exterior(actor, house_id)


@router.put("/exterior/{house_id}")
async def put_exterior(
    house_id: str,
    body: ExteriorPutBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """Persist the removed-items blacklist (optimistic lock)."""
    return CabinExteriorService(db).put_exterior(
        actor, house_id, body.removed_ids, body.expected_version
    )


@router.delete("/exterior/{house_id}")
async def delete_exterior(
    house_id: str,
    expected_version: int,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """Restore all exterior furniture (clear the blacklist)."""
    return CabinExteriorService(db).delete_exterior(actor, house_id, expected_version)


@router.get("/furniture")
async def list_furniture(actor: Actor = Depends(get_actor)) -> dict:
    """The shared furniture registry (id, label, grid size, mount)."""
    actor.require_authenticated()
    return {
        "items": [
            {
                "id": fid,
                "label": label,
                "size_cells": {"w": w, "h": h},
                "mount": mount,
            }
            for fid, (label, w, h, mount) in FURNITURE_CATALOG.items()
        ],
        "count": len(FURNITURE_CATALOG),
        "max_items": MAX_ITEMS,
        "max_layout_bytes": MAX_LAYOUT_BYTES,
        "house_ids": sorted(HOUSE_IDS),
    }


@router.get("/interiors/{house_id}")
async def get_interior(
    house_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    """Read the owner's layout for one house template."""
    return CabinInteriorService(db).get_interior(actor, house_id)


@router.put("/interiors/{house_id}")
async def put_interior(
    house_id: str,
    body: InteriorPutBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """Create or update the layout (optimistic lock via ``expected_version``)."""
    return CabinInteriorService(db).put_interior(
        actor, house_id, body.layout, body.expected_version
    )


@router.delete("/interiors/{house_id}")
async def delete_interior(
    house_id: str,
    expected_version: int,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """Reset one house template back to its default arrangement."""
    return CabinInteriorService(db).delete_interior(actor, house_id, expected_version)
