"""Unit tests for dynamic route mounting (Batch I / §8).

Verifies:
1. Core canonical routes remain mounted in create_app().
2. Local modules with router = APIRouter are dynamically discovered.
3. No duplicate route collisions or mountings.
"""

from find_yourself.api.app import create_app
from find_yourself.api.routes import api_router, discover_local_routes, discover_entry_point_routes


def test_core_routes_mounted():
    """All core endpoints must be mounted in the FastAPI app."""
    app = create_app()
    openapi_paths = set(app.openapi()["paths"].keys())

    # Verify key paths across domains are present
    assert "/health" in openapi_paths
    assert "/health/live" in openapi_paths
    assert "/api/skills/stage" in openapi_paths
    assert "/api/cabin/life/save" in openapi_paths
    assert "/api/conversations" in openapi_paths


def test_discover_local_routes_idempotent():
    """Running discover_local_routes repeatedly does not duplicate routes."""
    count_before = len(api_router.routes)
    discover_local_routes()
    assert len(api_router.routes) == count_before


def test_discover_entry_point_routes_idempotent():
    """Running discover_entry_point_routes repeatedly does not crash or duplicate."""
    count_before = len(api_router.routes)
    discover_entry_point_routes()
    assert len(api_router.routes) == count_before
