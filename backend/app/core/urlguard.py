"""Server-side request forgery (SSRF) protection for admin-supplied upstream URLs.

An admin who can register an MCP server URL can make the gateway send requests from
inside the cluster. This module limits where those requests may go.

* Always blocked: cloud metadata and link-local addresses (169.254.0.0/16, fe80::/10,
  AWS/GCP/Azure metadata names), multicast, unspecified and reserved ranges.
* Loopback and private ranges: blocked unless the host is on the allowlist, or the
  deployment explicitly allows private networks (the default, for local development).

Hostnames are resolved and *every* address is checked, so a name that points at a
blocked address is rejected. The check runs when a server is saved and again before
each connection. A hostname could still change its answer in the instant between the
check and the connection (DNS rebinding); the threat model notes this residual risk.
"""

import fnmatch
import ipaddress
import socket
from urllib.parse import urlsplit

_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
        "metadata.azure.com",
    }
)
_ALWAYS_BLOCKED_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, includes 169.254.169.254
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fd00:ec2::/32"),  # AWS IPv6 metadata
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT, used by some metadata services
    ipaddress.ip_network("192.0.0.0/24"),
]


class UnsafeURLError(ValueError):
    """The URL points somewhere the gateway must not connect to."""


def _check_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool) -> None:
    # An IPv4-mapped IPv6 address (::ffff:169.254.169.254) must be judged as the IPv4 one.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped

    # Loopback is decided by the private-network policy alone. It must be handled first:
    # Python files ::1 under the IPv6 "reserved" block, which would block localhost
    # even where private networks are allowed.
    if address.is_loopback:
        if not allow_private:
            raise UnsafeURLError("Private and loopback addresses are not allowed here")
        return

    if (
        address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or any(address in net for net in _ALWAYS_BLOCKED_NETWORKS)
    ):
        raise UnsafeURLError("That address is not allowed (link-local, metadata or reserved range)")
    if not allow_private and address.is_private:
        raise UnsafeURLError("Private and loopback addresses are not allowed here")


def check_upstream_url(url: str, *, allow_private: bool, allowed_hosts: list[str]) -> None:
    """Raise UnsafeURLError unless the gateway may connect to this URL."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise UnsafeURLError("URL is not valid") from None
    if parsed.scheme not in {"http", "https"} or not host:
        raise UnsafeURLError("URL must be http or https with a host name")
    if host in _METADATA_HOSTS or host.endswith(".metadata.google.internal"):
        raise UnsafeURLError("Cloud metadata endpoints are not allowed")

    explicitly_allowed = any(fnmatch.fnmatch(host, pattern) for pattern in allowed_hosts)
    effective_allow_private = allow_private or explicitly_allowed

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        _check_address(literal, allow_private=effective_allow_private)
        return

    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        # An unresolvable name is not a security problem; it will simply show as
        # unreachable. Do not leak resolver details.
        return
    for text in resolved:
        _check_address(ipaddress.ip_address(text.split("%")[0]), allow_private=effective_allow_private)
