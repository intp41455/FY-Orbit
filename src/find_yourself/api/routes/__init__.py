"""Route aggregation for the FastAPI application."""

from fastapi import APIRouter

from . import a2a, agents, assessments, auth, canvas, catalog, charts, conversations, export, health, inference, media, memory, profiles, proposals, skills, sync, tasks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(tasks.router)
api_router.include_router(memory.router)
api_router.include_router(proposals.router)
api_router.include_router(assessments.router)
api_router.include_router(catalog.router)
api_router.include_router(export.router)
api_router.include_router(inference.router)
api_router.include_router(a2a.router)
api_router.include_router(agents.router)
api_router.include_router(skills.router)
api_router.include_router(media.router)
api_router.include_router(profiles.router)
api_router.include_router(canvas.router)
api_router.include_router(sync.router)
api_router.include_router(charts.router)

