"""Who may do what on the public requests site.

Pure functions, no aiohttp and no files, so the rules can be tested alone.

Two rules, both failing closed:

* **Admin only from the home network.** The site sits behind an nginx proxy,
  so the address the bot sees is the proxy's, not the visitor's. The real
  visitor address is read from the proxy's X-Forwarded-For / X-Real-IP header,
  but only when the connection really comes from a proxy listed in
  ``trusted_proxies``. A request that carries proxy headers from any other
  address is treated as "not on the home network", and so is a trusted proxy
  that sends no visitor address. Nothing is ever local by default except the
  bot's own machine: the home subnet must be listed in ``admin_networks``.
* **Invite code to create an account.** The code comes only from the config
  file (``invite_code``). If none is set, sign-ups are closed.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
from typing import Iterable, Mapping

log = logging.getLogger("audiobooks.access")

# Headers a reverse proxy adds. Seeing any of them means the peer is a proxy.
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded",
                 "x-forwarded-host", "x-forwarded-proto")
# What counts as local when admin_networks is not configured: this machine only.
LOOPBACK_NETWORKS = ("127.0.0.1/32", "::1/128")


def parse_ip(text: object):
    """An ip_address, or None. IPv4-mapped IPv6 addresses become IPv4."""
    try:
        ip = ipaddress.ip_address(str(text).strip())
    except ValueError:
        return None
    return ip.ipv4_mapped if getattr(ip, "ipv4_mapped", None) else ip


def parse_networks(values: Iterable[object] | None) -> list:
    """CIDR strings (or single addresses) to networks; bad entries are logged
    and skipped. An empty or missing list means this machine only."""
    networks = []
    for value in (values or LOOPBACK_NETWORKS):
        try:
            networks.append(ipaddress.ip_network(str(value).strip(), strict=False))
        except ValueError:
            log.warning("Ignoring invalid admin_networks entry: %r", value)
    return networks


def parse_proxies(values: Iterable[object] | None) -> set[str]:
    """Trusted proxy addresses as normalised strings; bad entries are skipped."""
    out: set[str] = set()
    for value in (values or ()):
        ip = parse_ip(value)
        if ip is None:
            log.warning("Ignoring invalid trusted_proxies entry: %r", value)
        else:
            out.add(str(ip))
    return out


def _lowered(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(k).lower(): v for k, v in headers.items()}


def client_ip(peer: object, headers: Mapping[str, str], trusted_proxies: set[str]):
    """The visitor's address as an ip_address, or None when it can not be
    known (treat None as "not local")."""
    peer_ip = parse_ip(peer) if peer else None
    if peer_ip is None:
        return None
    seen = _lowered(headers)
    if str(peer_ip) in trusted_proxies:
        # The proxy appends the address it saw as the LAST entry, so the last
        # entry can not be forged by the visitor.
        forwarded = [p for p in seen.get("x-forwarded-for", "").split(",") if p.strip()]
        for candidate in ([forwarded[-1]] if forwarded else []) + [seen.get("x-real-ip", "")]:
            ip = parse_ip(candidate) if candidate else None
            if ip is not None:
                return ip
        return None                    # a trusted proxy that named no visitor
    if any(name in seen for name in PROXY_HEADERS):
        return None                    # proxied through something we do not trust
    return peer_ip                     # a direct connection


def is_local(ip, networks: list) -> bool:
    return ip is not None and any(ip in net for net in networks)


def invite_status(configured: object, given: object) -> str:
    """'ok', 'wrong', or 'closed' (no invite code configured)."""
    expected = str(configured or "").strip()
    if not expected:
        return "closed"
    attempt = str(given or "").strip()
    same = hmac.compare_digest(attempt.encode("utf-8"), expected.encode("utf-8"))
    return "ok" if same else "wrong"
