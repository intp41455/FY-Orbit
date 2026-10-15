"""Shared network predicates.

``is_loopback`` exists because the previous per-module literal sets
``{"127.0.0.1", "localhost", "::1"}`` refused the IPv4-mapped IPv6 form
(``::ffff:127.0.0.1``) that dual-stack sockets (Docker/WSL/uvicorn) report
for plain 127.0.0.1 connections. That locked every fresh install out of the
product: guest minting, local dev-token login and metrics all 403'd.
"""

from __future__ import annotations

import ipaddress


def is_loopback(host: str | None) -> bool:
    """True for every spelling of a local peer a dual-stack server may report."""
    if not host:
        return False
    h = host.strip().lower()
    if h == "localhost":
        return True
    if h.startswith("::ffff:"):
        h = h[len("::ffff:"):]
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False
