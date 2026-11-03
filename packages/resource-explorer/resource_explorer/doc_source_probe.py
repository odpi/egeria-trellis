"""Read-only reachability probe for a declared documentation source.

`BRIEF-DATABASE-DOCUMENTATION-SOURCES.md` slice 1 ("Declare and probe").
Mirrors `surveyors/credential_capability.py`'s posture and state vocabulary
on purpose — both answer "what does this credential/URL actually let us
see", read-only, GET-only, no attempt to authenticate or work around a
sign-in wall. Where `credential_capability` reads back a stored probe this
module IS the probe: it is the thing that makes the one network call, run
synchronously by the route handler right after a source is declared (and
again on an explicit re-check), never speculatively and never on a schedule
(that is slice 3, "Freshness").

**Four states, no ordering** — same discipline `credential_capability`'s
module docstring argues for its own four values: each is its own predicate
over the response, not a rung on a ladder.

  * `reachable`    — a 2xx response came back.
  * `needs_sign_in`— 401/403, or a redirect that landed on something that
                      looks like a login page (`_looks_like_sign_in`).
  * `not_found`    — 404/410.
  * `blocked`      — the site ANSWERED with a refusal: other 4xx/5xx. The
                      HTTP status code is carried alongside.
  * `timed_out`    — E2 (2026-09-29): no answer inside the budget. Split out
                      of `blocked` because the owner saw a 4s timeout read
                      "blocked", which claims the site refused us.
  * `unreachable`  — E2: the site never answered at all (DNS, TLS,
                      connection refused). Also not a refusal.

**Nothing is stored from the page itself beyond title and byte count**
(the brief's own words) — this module never writes to the registry; it
returns a `ProbeResult` and the caller (`routes/doc_sources.py`) is the one
that persists it via `ProjectRegistry.record_doc_source_probe`. Body bytes
read are capped at `_MAX_BODY_BYTES` so a probe cannot be turned into an
accidental partial ingest — full content fetch/chunking is slice 2's job,
not this one's.

**No form posts, no sign-in, no cookies accepted beyond what the GET
itself carries.** A fresh `httpx.Client` per probe, closed immediately
after, so nothing about one source's probe leaks into another's — same
posture `credential_capability`'s probe takes toward the database
connection it opens and closes for exactly one check.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

REACHABLE = "reachable"
NEEDS_SIGN_IN = "needs_sign_in"
NOT_FOUND = "not_found"
BLOCKED = "blocked"          # the site ANSWERED with a refusal (4xx/5xx other than 404/410)
TIMED_OUT = "timed_out"      # no answer inside the budget (httpx.TimeoutException)
UNREACHABLE = "unreachable"  # never answered at all: DNS / TLS / connection failure

# Gate: "each shows its state within five seconds with the status code."
# Set below that so a slow/unreachable host is reported as `blocked` inside
# the budget rather than the probe itself blowing past it.
_TIMEOUT_S = 4.0
_MAX_BODY_BYTES = 65_536

# Round 5 fix (2026-09-29): a real 200 page ("Pragmatic Data Research Ltd –
# Supp…") was misclassified `needs_sign_in`. Design's precise rule (only
# these three count, per exact wording): a 401/403 status; a redirect whose
# TARGET PATH looks like a login page; or a 200 response whose body contains
# an actual password input, not the word "password"/"sign in" appearing as
# page text. A login link or keyword elsewhere on an otherwise normal page
# is `reachable`, never `needs_sign_in` — page-content keyword matching is
# exactly what produced the false positive and is not brought back here.
#
# `_LOGIN_PATH_MARKERS` match against the redirect target's PATH only (not
# the full URL/query string) — the previous version matched anywhere in the
# whole URL, including a bare "auth" substring, which false-positives on
# ordinary words/params ("author", "authorize" trackers, "oauth" callbacks
# that land back on a normal page) as readily as on a real login redirect.
_LOGIN_PATH_MARKERS = ("login", "log-in", "signin", "sign-in", "sso", "session/new")
# Known third-party identity providers — checked against the redirect
# target's host, since a redirect landing there is a login redirect
# regardless of what its path happens to be.
_KNOWN_IDP_HOSTS = ("accounts.google.com", "okta.com")

_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_PASSWORD_INPUT_RE = re.compile(
    rb"<input\b[^>]*\btype\s*=\s*[\"']?password[\"']?", re.IGNORECASE,
)


@dataclass(frozen=True)
class ProbeResult:
    state: str
    status_code: int | None
    elapsed_ms: int
    title: str = ""
    byte_count: int | None = None
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "title": self.title,
            "byte_count": self.byte_count,
            "error": self.error,
        }


def _looks_like_login_redirect(url: str) -> bool:
    parsed = urlparse(url.lower())
    if any(host in parsed.netloc for host in _KNOWN_IDP_HOSTS):
        return True
    return any(marker in parsed.path for marker in _LOGIN_PATH_MARKERS)


def _has_password_form(body: bytes) -> bool:
    return bool(_PASSWORD_INPUT_RE.search(body))


def _extract_title(body: bytes) -> str:
    m = _TITLE_RE.search(body)
    if not m:
        return ""
    raw = m.group(1).decode("utf-8", errors="ignore")
    # Collapse whitespace/newlines a real <title> sometimes carries.
    return " ".join(raw.split())[:200]


def probe(url: str) -> ProbeResult:
    """Run the read-only reachability check for one URL. Never raises —
    every failure mode (timeout, DNS, TLS, connection refused, malformed
    URL) becomes a `blocked` result with `error` set, because a probe that
    can throw is a probe a caller must remember to guard, and the whole
    point of this module is that the caller doesn't have to."""
    started = time.monotonic()
    try:
        with httpx.Client(
            timeout=_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": "resource-explorer-doc-source-probe/1.0"},
        ) as client:
            with client.stream("GET", url) as resp:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) >= _MAX_BODY_BYTES:
                        break
                byte_count = len(body)
                content_length = resp.headers.get("content-length")
                if content_length and content_length.isdigit():
                    byte_count = int(content_length)
                title = _extract_title(body) if "html" in (resp.headers.get("content-type") or "") else ""
                landed_on = str(resp.url)

                if resp.status_code in (401, 403) or (
                    resp.status_code < 400 and landed_on != url and _looks_like_login_redirect(landed_on)
                ) or (
                    200 <= resp.status_code < 300 and _has_password_form(body)
                ):
                    return ProbeResult(NEEDS_SIGN_IN, resp.status_code, elapsed_ms, title, byte_count)
                if resp.status_code in (404, 410):
                    return ProbeResult(NOT_FOUND, resp.status_code, elapsed_ms, title, byte_count)
                if 200 <= resp.status_code < 300:
                    return ProbeResult(REACHABLE, resp.status_code, elapsed_ms, title, byte_count)
                return ProbeResult(
                    BLOCKED, resp.status_code, elapsed_ms, title, byte_count,
                    error=f"HTTP {resp.status_code}",
                )
    except httpx.TimeoutException as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ProbeResult(TIMED_OUT, None, elapsed_ms, error=f"timed out: {exc}")
    except httpx.HTTPError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ProbeResult(UNREACHABLE, None, elapsed_ms, error=str(exc)[:300])
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.warning("doc source probe crashed for %s: %s", url, exc)
        return ProbeResult(UNREACHABLE, None, elapsed_ms, error=str(exc)[:300])
