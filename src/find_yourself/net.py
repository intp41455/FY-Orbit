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


def is_local_peer(host: str | None, extra_cidrs: str) -> bool:
    """Loopback, or an explicitly trusted local-range peer (e.g. Docker NAT).

    Docker port-mapping rewrites the client address: a browser hitting
    ``localhost:18000`` on the host arrives at the container as the bridge
    gateway (``172.17.0.1``), never as ``127.0.0.1`` — so a pure loopback
    check locks every Docker deployment out of guest minting. Callers that
    face this situation configure ``extra_cidrs`` (comma-separated CIDRs,
    from e.g. ``FY_LOCAL_PEER_CIDRS``); empty config keeps the strict
    loopback-only behaviour byte-for-byte. Trust is explicit, never silent.
    """
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("::ffff:"):
        h = h[len("::ffff:"):]
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    for cidr in extra_cidrs.split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue  # malformed CIDR: skip, never crash the request path
    return False
