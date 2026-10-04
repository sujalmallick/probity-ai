"""SSRF-safe URL fetcher (Security.md §5, Guardrails G9).

https only · resolve and block private/loopback/link-local/metadata ranges · redirects re-validated ·
size and time caps.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 8.0
MAX_REDIRECTS = 3
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata"}


class FetchBlocked(ValueError):
    pass


class FetchUnreachable(FetchBlocked):
    """The host could not be resolved (no internet / DNS failure) — not a policy block."""


def _ip_blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (addr.version == 6 and addr.ipv4_mapped is not None and _ip_blocked(str(addr.ipv4_mapped)))
    )


def check_url(url: str, resolver=socket.getaddrinfo) -> None:  # type: ignore[no-untyped-def]
    p = urlparse(url)
    if p.scheme != "https":
        raise FetchBlocked(f"scheme {p.scheme!r} not allowed (https only)")
    host = (p.hostname or "").lower()
    if not host or host in _BLOCKED_HOSTS or host.endswith(".internal") or host.endswith(".local"):
        raise FetchBlocked(f"host {host!r} blocked")
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        try:
            ips = sorted({ai[4][0] for ai in resolver(host, p.port or 443)})
        except socket.gaierror as e:
            raise FetchUnreachable(f"cannot resolve {host} (no network or DNS failure)") from e
    for ip in ips:
        if _ip_blocked(ip):
            raise FetchBlocked(f"{host} resolves to blocked address {ip}")


def safe_get(url: str) -> tuple[str, str]:
    """Return (final_url, text). Raises FetchBlocked on policy violation."""
    current = url
    with httpx.Client(follow_redirects=False, timeout=TIMEOUT, headers={"User-Agent": "ProbityBot/0.1 (+verification)"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current)
            with client.stream("GET", current) as r:
                if r.is_redirect:
                    current = urljoin(current, r.headers.get("location", ""))
                    continue
                r.raise_for_status()
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        raise FetchBlocked("response exceeds size cap")
                return current, buf.decode(r.encoding or "utf-8", errors="replace")
    raise FetchBlocked("too many redirects")
