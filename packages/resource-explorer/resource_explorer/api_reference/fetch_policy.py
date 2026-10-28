"""Which addresses RE may fetch a registered API document from. Deny by default; one setting.

    RE_API_FETCH_ALLOW   a JSON list (["api.example.com", "*.corp.example"]) or a comma list. Default:
                         ["localhost", "127.0.0.1", "::1"].

Each entry is one of
    exact host        api.example.com
    wildcard suffix   *.example.com        subdomains only: it does not match example.com itself (list that
                                           too if wanted); a '*' anywhere else in a name is not accepted
    IP or CIDR        10.0.0.0/8, ::1, 192.168.1.5
    "*"               any host (the always-deny list below still applies)
each optionally followed by ":port" ("[::1]:8530" for an IPv6 literal with a port). An entry WITHOUT a port
admits any port; an entry with one admits only that port. An unreadable setting never crashes anything: bad
entries are skipped, and a setting that leaves no valid entry denies everything.

Checked at EVERY hop (the registered URL and each redirect): the hostname against host and wildcard entries, and
the addresses it resolves to against IP/CIDR entries. A host entry admits the addresses its name resolves to.
The check resolves the name ONCE and the fetch connects to the address that was checked (see `Target`), so a
resolver that answers differently the second time (DNS rebinding) is never asked a second time.

Always denied, whatever the list says (even "*"): link-local 169.254.0.0/16 and fe80::/10 (cloud metadata
services), the Alibaba and AWS-IPv6 metadata addresses, 0.0.0.0/8 and ::/128 (this host), multicast 224.0.0.0/4
and ff00::/8, broadcast, Teredo 2001::/32, IPv4-translated ::ffff:0:0:0/96 and local-use
NAT64 64:ff9b:1::/48 (RFC 8215; denied rather than unwrapped, the embedded address moves with the prefix length).
Loopback and private ranges are allowed only when the list admits them (the default
admits loopback).

An IPv6 address that CARRIES an IPv4 one is judged, and connected to, as that IPv4 address: ::ffff:a.b.c.d
(mapped), ::a.b.c.d (compatible), 64:ff9b::a.b.c.d (NAT64) and 2002:aabb:ccdd:: (6to4). Otherwise
::ffff:169.254.169.254 would walk past a v4 deny list, and ::ffff:127.0.0.1 would reach a v4-only listener.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
from dataclasses import dataclass

log = logging.getLogger(__name__)

ENV = "RE_API_FETCH_ALLOW"
DEFAULT_ALLOW = ("localhost", "127.0.0.1", "::1")
ALWAYS_DENY = tuple(ipaddress.ip_network(n) for n in (
    "169.254.0.0/16", "fe80::/10", "0.0.0.0/8", "::/128", "224.0.0.0/4", "ff00::/8",
    "100.100.100.200/32",            # Alibaba Cloud metadata
    "fd00:ec2::254/128",             # AWS IPv6 metadata
    "2001::/32",                     # Teredo: tunnels to an IPv4 server, the target is not what the name says
    "::ffff:0:0:0/96",               # IPv4-translated (RFC 2765, SIIT), not the mapped ::ffff:0:0/96 unwrapped below
    "64:ff9b:1::/48",                # local-use NAT64 (RFC 8215): the embedded IPv4 sits at a prefix-dependent offset
    "255.255.255.255/32"))           # broadcast
NOT_ALLOWED = f"not in the allowed addresses ({ENV})"
NEVER_ALLOWED = "that address is in a range RE never fetches from"


@dataclass(frozen=True)
class Rule:
    kind: str                 # any | host | wildcard | net
    value: str = ""
    net: object = None
    port: int | None = None
    text: str = ""            # the entry as written, for display


def _split_port(entry: str):
    """(host, port text) or None for a malformed entry ('[::1]x', '[::1', 'host:')."""
    if entry.startswith("["):
        host, close, rest = entry[1:].partition("]")
        if not close or not host:
            return None
        if not rest:
            return host, ""
        if rest.startswith(":") and len(rest) > 1:
            return host, rest[1:]
        return None
    if entry.count(":") == 1:
        host, _, port = entry.partition(":")
        return (host, port) if host and port else None
    return entry, ""


def parse_entry(entry) -> Rule | None:
    if not isinstance(entry, str) or not entry.strip():
        return None
    text = entry.strip()
    split = _split_port(text)
    if split is None:
        return None
    host, port_s = split
    port = None
    if port_s:
        if not port_s.isdigit() or not 0 < int(port_s) < 65536:
            return None
        port = int(port_s)
    host = host.strip().lower()
    if host == "*":
        return Rule("any", port=port, text=text)
    if "/" in host:
        try:
            return Rule("net", net=ipaddress.ip_network(host, strict=False), port=port, text=text)
        except ValueError:
            return None
    try:
        return Rule("net", net=ipaddress.ip_network(ipaddress.ip_address(host)), port=port, text=text)
    except ValueError:
        pass
    if host.startswith("*."):
        suffix = host[2:]
        return Rule("wildcard", value=suffix, port=port, text=text) if suffix and "*" not in suffix else None
    if "*" in host or not host or any(c.isspace() for c in host):
        return None
    return Rule("host", value=host, port=port, text=text)


def parse_allow(raw) -> list:
    """The rules for a setting value (a JSON list, a comma list, or an already-parsed list). Never raises."""
    items: list = []
    try:
        if isinstance(raw, (list, tuple)):
            items = list(raw)
        elif isinstance(raw, str):
            body = raw.strip()
            if body.startswith("["):
                try:
                    loaded = json.loads(body)
                    items = loaded if isinstance(loaded, list) else []
                except (ValueError, RecursionError):
                    items = [x.strip(" \"'") for x in body.strip("[]").split(",")]
            else:
                items = [x for x in body.split(",")]
    except (TypeError, AttributeError):
        items = []
    rules = [r for r in (parse_entry(i) for i in items) if r is not None]
    skipped = len([i for i in items if isinstance(i, str) and i.strip()]) - len(rules)
    if skipped > 0:
        log.warning("%s: %d entr(ies) could not be read and were skipped", ENV, skipped)
    return rules


def effective_rules(allow=None) -> list:
    if allow is not None:
        return parse_allow(allow)
    raw = os.environ.get(ENV)
    return parse_allow(list(DEFAULT_ALLOW)) if raw is None else parse_allow(raw)


def describe() -> dict:
    """What the admin page shows, read-only."""
    raw = os.environ.get(ENV)
    return {"setting": ENV, "source": "default" if raw is None else "environment",
            "allow": [r.text for r in effective_rules()],
            "always_deny": [str(n) for n in ALWAYS_DENY],
            "note": "An entry without a port admits any port. Checked on every redirect hop; the connection goes "
                    "to the address that was checked."}


def system_resolver(host: str, port: int) -> list:
    return [info[4][0].split("%")[0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def unwrap(ip):
    """The IPv4 address an IPv6 address carries, or the address itself. Mapped, compatible (above ::ffff so ::1
    stays ::1), NAT64 and 6to4."""
    if ip.version != 6:
        return ip
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    n = int(ip)
    if ip in _NAT64:
        return ipaddress.ip_address(n & 0xFFFFFFFF)
    if 0xFFFF < n < (1 << 32):
        return ipaddress.ip_address(n)
    return ip


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


@dataclass
class Target:
    ok: bool
    reason: str = ""
    host: str = ""
    port: int = 0
    ip: str = ""              # the first address that was checked: connect to THESE, not to the name again
    ips: tuple = ()           # every checked address, in resolver order, unwrapped to IPv4 where one is carried
    unresolved: bool = False


def _rule_admits_name(rule: Rule, host: str, port: int) -> bool:
    if rule.port is not None and rule.port != port:
        return False
    if rule.kind == "any":
        return True
    if rule.kind == "host":
        return host == rule.value
    if rule.kind == "wildcard":
        return host.endswith("." + rule.value)
    return False


def _rule_admits_ip(rule: Rule, ip, port: int) -> bool:
    return (rule.kind == "net" and (rule.port is None or rule.port == port)
            and ip.version == rule.net.version and ip in rule.net)


def _denied(ip) -> bool:
    return any(ip.version == n.version and ip in n for n in ALWAYS_DENY)


def check_target(host: str, port: int, *, allow=None, resolver=None) -> Target:
    """Decide whether `host`:`port` may be fetched, resolving it ONCE. Reasons name the rule, never the answers.
    Every address is unwrapped (see the module doc) BEFORE the always-deny list and the rules look at it, and the
    unwrapped address is the one returned for connecting."""
    host = (host or "").strip("[]").lower()
    rules = effective_rules(allow)
    try:
        raw = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            raw = [ipaddress.ip_address(a) for a in (resolver or system_resolver)(host, port)]
        except (OSError, ValueError):
            raw = []
    ips = []
    for ip in raw:
        eff = unwrap(ip)
        if _denied(ip) or _denied(eff):
            return Target(False, NEVER_ALLOWED, host, port)
        if eff not in ips:
            ips.append(eff)
    named = any(_rule_admits_name(r, host, port) for r in rules)
    by_ip = bool(ips) and all(any(_rule_admits_ip(r, ip, port) for r in rules) for ip in ips)
    if not (named or by_ip):
        return Target(False, NOT_ALLOWED, host, port)
    if not ips:
        return Target(True, "", host, port, unresolved=True)       # allowed by name; the connect will fail by itself
    return Target(True, "", host, port, str(ips[0]), tuple(str(i) for i in ips))
