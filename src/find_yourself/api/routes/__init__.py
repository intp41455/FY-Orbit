"""Route aggregation for the FastAPI application (Batch I / §8).

Supports:
1. Canonical existing core routers mounted in deterministic order.
2. Convention-based auto-discovery: any newly added module in find_yourself.api.routes
   defining a `router = APIRouter(...)` is automatically discovered and mounted.
3. Entry-point based discovery: external extension packages declaring `find_yourself.routes`
   entry points are dynamically mounted.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
from fastapi import APIRouter

logger = logging.getLogger("find_yourself.api.routes")

# Canonical known modules
from . import (  # noqa: F401
    a2a,
    agent_dispatch,
    agent_teams,
    agents,
    artifact_gate,
    assessments,
    assets,
    auth,
    automation,
    avatar,
    bus,
    butler,
    cabin,
    cabin_gameplay,
    cabin_life,
    canvas,
    catalog,
    charts,
    collaboration,
    conversations,
    dsl_canvas,
    dsl_lifecycle,
    dsl_debug,
    export,
    git_repo,
    guest,
    health,
    hitl,
    hub,
    inference,
    kanban,
    knowledge,
    media,
    memory,
    plugins,
    profiles,
    prompts,
    proposals,
    rag_presets,
    recovery,
    runtime,
    session_state,
    skills,
    stash,
    streaming,
    sync,
    tasks,
    team_approval,
    tools,
    workbench,
    workflow_gen,
)

api_router = APIRouter()

# Keep track of mounted router ids to avoid duplicate mounting
_mounted_router_ids: set[int] = set()


def _mount_router(router: APIRouter, source: str = "") -> None:
    if id(router) in _mounted_router_ids:
        return
    api_router.include_router(router)
    _mounted_router_ids.add(id(router))


# 1. Mount all core canonical routers in deterministic order
_CORE_MODULES = [
    health,
    auth,
    conversations,
    tasks,
    memory,
    proposals,
    assessments,
    catalog,
    export,
    inference,
    a2a,
    agents,
    skills,
    media,
    profiles,
    canvas,
    sync,
    charts,
    workbench,
    agent_teams,
    git_repo,
    tools,
    streaming,
    prompts,
    stash,
    dsl_canvas,
    dsl_lifecycle,
    dsl_debug,
    agent_dispatch,
    session_state,
    butler,
    cabin,
    knowledge,
    workflow_gen,
    avatar,
    cabin_gameplay,
    guest,
    bus,
    hub,
    assets,
    automation,
    hitl,
    kanban,
    team_approval,
    artifact_gate,
    collaboration,
    cabin_life,
    plugins,
    rag_presets,
    recovery,
    runtime,
]

for mod in _CORE_MODULES:
    r = getattr(mod, "router", None)
    if isinstance(r, APIRouter):
        _mount_router(r, mod.__name__)


# 2. Convention-based auto-discovery: scan routes package directory for any newly added route modules
def discover_local_routes() -> list[str]:
    discovered: list[str] = []
    current_pkg = __name__
    for _, module_name, is_pkg in pkgutil.iter_modules(__path__):
        if is_pkg or module_name.startswith("_"):
            continue
        full_name = f"{current_pkg}.{module_name}"
        try:
            mod = importlib.import_module(full_name)
            r = getattr(mod, "router", None)
            if isinstance(r, APIRouter) and id(r) not in _mounted_router_ids:
                _mount_router(r, full_name)
                discovered.append(full_name)
        except Exception as exc:
            logger.warning("Failed to auto-mount route module %s: %s", full_name, exc)
    return discovered


# 3. Pluggable entry points discovery for external route extensions
def discover_entry_point_routes() -> list[str]:
    discovered: list[str] = []
    if sys.version_info >= (3, 10):
        from importlib.metadata import entry_points

        try:
            eps = entry_points(group="find_yourself.routes")
            for ep in eps:
                try:
                    loaded = ep.load()
                    r = getattr(loaded, "router", loaded)
                    if isinstance(r, APIRouter) and id(r) not in _mounted_router_ids:
                        _mount_router(r, ep.name)
                        discovered.append(ep.name)
                except Exception as exc:
                    logger.warning("Failed to load route entry point %s: %s", ep.name, exc)
        except Exception:
            pass
    return discovered


# Perform discovery upon module load
discover_local_routes()
discover_entry_point_routes()
