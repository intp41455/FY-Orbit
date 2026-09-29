"""HTTP API layer (Runtime shard, FROZEN_CONTRACT §5).

This package owns the FastAPI application, routing, identity/CSRF/Origin
enforcement and the wiring of Core services to HTTP. Caller-supplied
``owner_id``/``role``/``domain`` are never trusted; the :class:`Actor` is built
only from a verified owner session or a server-side service identity.
"""

from .app import create_app

__all__ = ["create_app"]
