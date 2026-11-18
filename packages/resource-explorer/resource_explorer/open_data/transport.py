"""The one place outbound HTTP happens for open data.

Adapters take a transport, so a test hands them a fake and CI never touches the network. A fake must bind this
signature (`tests/test_open_data.py::test_the_fake_transport_binds_the_real_signature`).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Protocol, TypeVar
from urllib.parse import urlparse

USER_AGENT = "ResourceExplorer-OpenData/1 (read-only catalog queries)"
TIMEOUT_SECONDS = 20.0
#: Reaching a portal (TCP connect plus the TLS handshake). A busy portal can take a while to finish a handshake;
#: 2026-11-09 healthdata.gov answered one in 0.8 s and timed out another at 20 s minutes apart.
CONNECT_TIMEOUT_SECONDS = 30.0
#: How many times a GET is tried when the portal could not be REACHED (connect / handshake / pool). Nothing has been
#: read by then, so a retry costs one more handshake, not a re-download. A failure after the answer started is not
#: retried: that would read a large feed again.
CONNECT_TRIES = 3
RETRY_PAUSE_SECONDS = 1.0
#: The most a JSON answer may weigh. A catalog `data.json` can be tens of MB; one past this is refused, not half-read.
MAX_JSON_BYTES = 64 * 1024 * 1024


class TransportError(RuntimeError):
    """The source could not be reached or answered with an error status. Never read as "found nothing"."""


T = TypeVar("T")


def failure_words(url: str, exc: Exception, tries: int = 1) -> str:
    """What happened, in words a person can act on. The library's own text (`_ssl.c:1015: The handshake operation
    timed out`) is kept at the end for whoever debugs it."""
    import httpx

    host = urlparse(url).hostname or url
    after = f" ({tries} tries)" if tries > 1 else ""
    detail = str(exc).strip()
    tail = f" [{detail}]" if detail else ""
    if isinstance(exc, httpx.ConnectTimeout):
        return f"{url}: timed out reaching {host}{after}; it may be busy, try again later{tail}"
    if isinstance(exc, httpx.ReadTimeout):
        return f"{url}: {host} stopped answering part-way (nothing for {TIMEOUT_SECONDS:.0f} s); try again later{tail}"
    if isinstance(exc, httpx.PoolTimeout):
        return f"{url}: no free connection to {host}{after}; try again later{tail}"
    if isinstance(exc, httpx.ConnectError):
        return f"{url}: could not connect to {host}{after}; check the portal's address, or try again later{tail}"
    return f"{url}: {detail or type(exc).__name__}; check the portal's address, or try again later"


def status_words(url: str, status: int) -> str:
    """An error status in words a person can act on. `HTTP <code>` stays in the text: it is what a log search or a
    reader who knows HTTP looks for."""
    host = urlparse(url).hostname or url
    if status in (404, 410):
        return f"{url}: HTTP {status}, not found at {host}; the dataset or feed may have moved"
    if status in (401, 403):
        return f"{url}: HTTP {status}, {host} refused access; the portal may need an API key"
    if status == 429:
        return f"{url}: HTTP 429, {host} is limiting how often it is asked; wait a while and try again"
    if status == 503:
        return f"{url}: HTTP 503, {host} is busy or down for maintenance; try again later"
    if status >= 500:
        return f"{url}: HTTP {status}, {host} had a server error; try again later"
    return f"{url}: HTTP {status}, {host} refused the request"


def _timeout():
    import httpx

    return httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)


def _reaching(url: str, attempt: Callable[[], T]) -> T:
    """Run `attempt` (one GET), retrying only when the portal could not be reached. Every httpx failure leaves as a
    `TransportError` carrying `failure_words`."""
    import httpx

    for n in range(1, CONNECT_TRIES + 1):
        try:
            return attempt()
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
            if n == CONNECT_TRIES:
                raise TransportError(failure_words(url, exc, n)) from exc
            time.sleep(RETRY_PAUSE_SECONDS * n)
        except httpx.HTTPError as exc:
            raise TransportError(failure_words(url, exc, n)) from exc
    raise AssertionError("unreachable")


class Transport(Protocol):
    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> Any: ...

    def get_text(self, url: str, params: dict | None = None, headers: dict | None = None) -> str: ...

    def get_bytes(self, url: str, max_bytes: int, headers: dict | None = None) -> tuple[bytes, int | None, bool]: ...


class HttpTransport:
    """GET JSON over httpx. Read-only; a non-2xx answer or non-JSON body raises `TransportError`."""

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> Any:
        import json

        import httpx

        sent = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}

        def attempt() -> tuple[bytearray, str]:
            with httpx.stream("GET", url, params=params, headers=sent, timeout=_timeout(),
                              follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    raise TransportError(status_words(url, resp.status_code))
                stated = resp.headers.get("content-length")
                if stated and stated.isdigit() and int(stated) > MAX_JSON_BYTES:
                    raise TransportError(f"{url}: the answer is {int(stated):,} bytes, over the {MAX_JSON_BYTES:,} byte read limit; not read")
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_JSON_BYTES:
                        raise TransportError(f"{url}: the answer is over the {MAX_JSON_BYTES:,} byte read limit; not read")
                return buf, resp.headers.get("content-type", "").split(";")[0].strip() or "not stated"

        buf, ctype = _reaching(url, attempt)
        try:
            return json.loads(bytes(buf))
        except ValueError as exc:
            raise TransportError(f"{url}: the answer was not JSON (content-type {ctype})") from exc

    def get_text(self, url: str, params: dict | None = None, headers: dict | None = None) -> str:
        """GET a text body (re3data answers XML). Same rules as `get_json`: read-only, non-2xx raises."""
        import httpx

        sent = {"User-Agent": USER_AGENT, "Accept": "text/xml, application/xml", **(headers or {})}
        resp = _reaching(url, lambda: httpx.get(url, params=params, headers=sent, timeout=_timeout(),
                                                follow_redirects=True))
        if resp.status_code >= 400:
            raise TransportError(status_words(url, resp.status_code))
        return resp.text

    def get_bytes(self, url: str, max_bytes: int, headers: dict | None = None) -> tuple[bytes, int | None, bool]:
        """GET a body, reading at most `max_bytes`. Returns (bytes read, the size the source states or None,
        truncated). `truncated` is True when the body was longer than `max_bytes`: the rest was not read, and the
        caller must not treat the bytes as the whole file."""
        import httpx

        sent = {"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})}

        def attempt() -> tuple[bytes, int | None, bool]:
            with httpx.stream("GET", url, headers=sent, timeout=_timeout(), follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    raise TransportError(status_words(url, resp.status_code))
                stated = resp.headers.get("content-length")
                size = int(stated) if stated and stated.isdigit() else None
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        return bytes(buf[:max_bytes]), size, True
                return bytes(buf), size if size is not None else len(buf), False

        return _reaching(url, attempt)
