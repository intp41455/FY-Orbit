"""Core service layer.

Runtime/Workflow must reach data through these services (or explicit
repositories). Caller-supplied ``owner_id``/``role``/``domain`` are never
trusted; identity comes from a server-side :class:`Actor` bound to an
authenticated session or service identity.
"""
