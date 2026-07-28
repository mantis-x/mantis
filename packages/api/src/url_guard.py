"""
SSRF guard for customer-supplied webhook URLs. A webhook URL is something we
will POST to from inside our own network, so an unvalidated one lets a customer
aim us at cloud metadata (169.254.169.254), our own Redis/Postgres, or other
internal hosts. Validate at registration and on the test endpoint.

Requires https and rejects any URL whose host resolves to a private, loopback,
link-local, reserved, or multicast address. Note: this checks at registration
time; a determined attacker could still DNS-rebind between check and delivery —
a fuller mitigation (re-resolve + pin at send time) is a hardening follow-up,
noted for the security-review gate, not built in Phase B.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeWebhookURL(ValueError):
    pass


def validate_webhook_url(url: str) -> None:
    """Raise UnsafeWebhookURL if the URL is malformed, non-https, or resolves
    to a non-public address."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeWebhookURL("webhook url must use https")
    if not parsed.hostname:
        raise UnsafeWebhookURL("webhook url has no host")

    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeWebhookURL(f"webhook host does not resolve: {exc}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            raise UnsafeWebhookURL(f"webhook host resolves to a non-public address ({ip})")
