"""One bounded request to a documentation URL: does it answer?

Reachability and nothing more. No crawl, no body read, one request (a second only
when a server refuses HEAD), a short timeout. Three outcomes that must never be
confused:

    reachable      the server answered with a status below 400
    unreachable    it answered with an error status, or did not answer at all
    not_checked    no request was made (offline, or disabled) -- says nothing
                   about the site

`not_checked` is not `unreachable`: reporting a check that did not happen would
turn "we did not look" into "it is down".
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict

TIMEOUT_SECONDS = 8.0
_UA = "resource-explorer/1.0 (+docs reachability)"
_OFFLINE_ENV = "RE_OFFLINE"


@dataclass
class ProbeResult:
    state: str                  # reachable | unreachable | not_checked
    status: int | None = None
    final_url: str = ""
    last_modified: str = ""
    reason: str = ""            # why unreachable / why not checked

    def as_dict(self) -> dict:
        return asdict(self)


def network_disabled() -> bool:
    return (os.environ.get(_OFFLINE_ENV, "") or "").strip().lower() in ("1", "true", "yes", "on")


class HttpProbe:
    """The HTTP boundary. Tests substitute an autospec of this class."""

    def probe(self, url: str, timeout: float = TIMEOUT_SECONDS) -> ProbeResult:
        if network_disabled():
            return ProbeResult("not_checked", reason="network disabled")
        result = self._once(url, "HEAD", timeout)
        # Some servers refuse HEAD but serve GET; ask once more, for one byte.
        if result.status in (403, 405, 501) or (result.state == "unreachable" and result.status is None):
            retry = self._once(url, "GET", timeout)
            if retry.state == "reachable" or retry.status is not None:
                return retry
        return result

    def _once(self, url: str, method: str, timeout: float) -> ProbeResult:
        headers = {"User-Agent": _UA}
        if method == "GET":
            headers["Range"] = "bytes=0-0"
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return ProbeResult(
                    "reachable" if status < 400 else "unreachable", status,
                    resp.geturl() or url, resp.headers.get("Last-Modified", "") or "",
                    "" if status < 400 else f"HTTP {status}")
        except urllib.error.HTTPError as exc:
            return ProbeResult("unreachable", exc.code, getattr(exc, "url", "") or url,
                               exc.headers.get("Last-Modified", "") if exc.headers else "",
                               f"HTTP {exc.code}")
        except Exception as exc:
            return ProbeResult("unreachable", None, "", "", f"{type(exc).__name__}: {str(exc)[:80]}")
