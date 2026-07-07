"""SSRF guard for outbound user-supplied URLs.

Runo fetches arbitrary URLs on behalf of API callers, which without
validation lets a caller pivot to internal services (cloud metadata,
localhost, RFC1918 ranges, link-local). ``validate_outbound_url`` is the
single entry point used by the fetcher and the image-augmentation pass to
reject those before any socket is opened.

The guard is cheap: scheme check + a single ``ipaddress.ip_address``
parse; only does a DNS lookup when the host is a name. Disabled entirely
if ``settings.ssrf_guard_enabled`` is false (escape hatch for self-hosted
deployments that legitimately scrape internal targets).

DNS rebinding defense: validation always re-resolves (no cache TTL), and
the httpx response hook in ``api/core/fetcher.py`` re-checks the actual
peer address surfaced via ``response.extensions['network_stream']`` so a
rebind that flips the answer between validate-time and connect-time is
caught before the response body is exposed to callers.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from runo.config import settings
from runo.exceptions import URLUnreachableError


_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _ip_is_forbidden(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_ip_forbidden(ip_str: str) -> bool:
    """Public helper: True if the IP literal targets a forbidden range.
    Used by post-connect response hooks to detect DNS rebinding attempts
    where the resolved IP at validate-time differs from the peer IP at
    connect-time."""
    if not settings.ssrf_guard_enabled:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return _ip_is_forbidden(ip)


def _resolve_host(host: str) -> tuple[str, ...]:
    """Always re-resolves — no cache. Validation runs immediately before
    each outbound request, so caching shaves microseconds at the cost of a
    longer rebinding window. Microseconds aren't worth it on the SSRF path."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return ()
    return tuple({info[4][0] for info in infos})


def validate_outbound_url(url: str) -> None:
    """Raise ``URLUnreachableError`` if the URL targets a forbidden scheme or
    address space. No-op when SSRF guard is disabled.
    """
    if not settings.ssrf_guard_enabled:
        return

    parsed = urlsplit(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise URLUnreachableError(f"Forbidden URL scheme: {scheme or '(empty)'}")

    host = parsed.hostname or ""
    if not host:
        raise URLUnreachableError("URL has no host component.")

    # Direct IP literal — check immediately, no DNS.
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None

    if ip is not None:
        if _ip_is_forbidden(ip):
            raise URLUnreachableError(f"Forbidden destination IP: {ip}")
        return

    # Hostname — resolve and reject if any answer is in a forbidden range.
    # Empty resolution result is allowed through; the actual fetch will fail
    # with a normal DNS error and we don't want to second-guess the resolver.
    for addr in _resolve_host(host):
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_is_forbidden(resolved):
            raise URLUnreachableError(
                f"Hostname {host} resolves to forbidden address: {resolved}"
            )
