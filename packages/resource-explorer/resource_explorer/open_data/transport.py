"""The one place outbound HTTP happens for open data.

Adapters take a transport, so a test hands them a fake and CI never touches the network. A fake must bind this
signature (`tests/test_open_data.py::test_the_fake_transport_binds_the_real_signature`).
"""
from __future__ import annotations

from typing import Any, Protocol

USER_AGENT = "ResourceExplorer-OpenData/1 (read-only catalog queries)"
TIMEOUT_SECONDS = 20.0
#: The most a JSON answer may weigh. A catalog `data.json` can be tens of MB; one past this is refused, not half-read.
MAX_JSON_BYTES = 64 * 1024 * 1024


class TransportError(RuntimeError):
    """The source could not be reached or answered with an error status. Never read as "found nothing"."""


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
        try:
            with httpx.stream("GET", url, params=params, headers=sent, timeout=TIMEOUT_SECONDS,
                              follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    raise TransportError(f"{url}: HTTP {resp.status_code}")
                stated = resp.headers.get("content-length")
                if stated and stated.isdigit() and int(stated) > MAX_JSON_BYTES:
                    raise TransportError(f"{url}: the answer is {int(stated):,} bytes, over the {MAX_JSON_BYTES:,} byte read limit; not read")
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_JSON_BYTES:
                        raise TransportError(f"{url}: the answer is over the {MAX_JSON_BYTES:,} byte read limit; not read")
                ctype = resp.headers.get("content-type", "").split(";")[0].strip() or "not stated"
        except httpx.HTTPError as exc:
            raise TransportError(f"{url}: {exc}") from exc
        try:
            return json.loads(bytes(buf))
        except ValueError as exc:
            raise TransportError(f"{url}: the answer was not JSON (content-type {ctype})") from exc

    def get_text(self, url: str, params: dict | None = None, headers: dict | None = None) -> str:
        """GET a text body (re3data answers XML). Same rules as `get_json`: read-only, non-2xx raises."""
        import httpx

        sent = {"User-Agent": USER_AGENT, "Accept": "text/xml, application/xml", **(headers or {})}
        try:
            resp = httpx.get(url, params=params, headers=sent, timeout=TIMEOUT_SECONDS, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise TransportError(f"{url}: {exc}") from exc
        if resp.status_code >= 400:
            raise TransportError(f"{url}: HTTP {resp.status_code}")
        return resp.text

    def get_bytes(self, url: str, max_bytes: int, headers: dict | None = None) -> tuple[bytes, int | None, bool]:
        """GET a body, reading at most `max_bytes`. Returns (bytes read, the size the source states or None,
        truncated). `truncated` is True when the body was longer than `max_bytes`: the rest was not read, and the
        caller must not treat the bytes as the whole file."""
        import httpx

        sent = {"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})}
        try:
            with httpx.stream("GET", url, headers=sent, timeout=TIMEOUT_SECONDS, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    raise TransportError(f"{url}: HTTP {resp.status_code}")
                stated = resp.headers.get("content-length")
                size = int(stated) if stated and stated.isdigit() else None
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        return bytes(buf[:max_bytes]), size, True
                return bytes(buf), size if size is not None else len(buf), False
        except httpx.HTTPError as exc:
            raise TransportError(f"{url}: {exc}") from exc
