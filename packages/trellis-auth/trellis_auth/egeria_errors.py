"""Is this exception Egeria refusing the caller? Decided by exception class and HTTP code, never by message text.

TR-22 (2026-11-15), mirroring the Portal's `egeria_error_mapping.auth_error_status()` (PO-9 / ERR-403).
Trellis used to read the text: "AUTHORIZATION_ERROR_401", "HTTP CODE: 403", "User not authorized",
"OPEN-METADATA-SECURITY-403", "-403-". Text reads two different things as one:

- a refused sign-in (401: a bad or expired token; signing in again is the fix), and
- Egeria refusing this request for a signed-in user (403: a zone they cannot see, a role they lack; a new
  token changes nothing);

and it reads things that are not refusals at all as refusals: OMAG-GENERIC-HANDLERS-403-005 (deleting an
element that still has dependents) carries relatedHTTPCode 403 and is an invalid-parameter error. pyegeria
(6.2.34+, PY-3) already makes that distinction when it raises: a `UserNotAuthorizedException` from Egeria, or
a transport 401, becomes `PyegeriaUnauthorizedException`; everything else does not (its
`_is_authorization_failure`). So the class and the code are the answer.

    from trellis_auth.egeria_errors import auth_error_status, NOT_PERMITTED, SIGN_IN_AGAIN

    status = auth_error_status(exc)      # 403, 401 or None

Unlike the Portal's version this matches **only pyegeria's exceptions**, not FastAPI's HTTPException or a bare
`httpx.HTTPStatusError`: a Trellis app also talks to GitHub, PyPI and OSV, and their 401/403 are not Egeria's.
pyegeria is imported lazily, so this module costs nothing where pyegeria is not installed (then nothing is an
auth error, which is the honest answer: no class to match).
"""
from __future__ import annotations

#: A refused sign-in: the token is bad or expired. Signing in again is the fix.
SIGN_IN_AGAIN = 401
#: Egeria refused this request for a signed-in user. Signing in again changes nothing.
NOT_PERMITTED = 403


def _as_code(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _classes():
    try:
        from pyegeria.core._exceptions import PyegeriaClientException, PyegeriaUnauthorizedException
    except ImportError:            # no pyegeria, or its exception module moved: no class to match
        return None, None
    return PyegeriaUnauthorizedException, PyegeriaClientException


def cause_chain(exc: BaseException | None):
    """`exc`, then what it was raised from: `__cause__` first (an explicit `raise … from`), else `__context__` only
    when it is not suppressed (`raise … from None` sets `__suppress_context__`: the raiser said the earlier error is
    not the reason). Each exception once (a cycle ends the walk)."""
    seen: set[int] = set()
    node = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        yield node
        cause = getattr(node, "__cause__", None)
        if cause is None and not getattr(node, "__suppress_context__", False):
            cause = getattr(node, "__context__", None)
        node = cause


def auth_error_status(exc: BaseException | None) -> int | None:
    """401 or 403 when `exc`, or anything in its cause chain, is Egeria refusing the caller; else None.

    - `PyegeriaUnauthorizedException` -> 403 when its `related_http_code` is 403 (Egeria's own
      UserNotAuthorizedException), else 401 (a transport 401: the token was refused);
    - `PyegeriaClientException` (an HTTP error status on the wire from Egeria) with 401 or 403 -> that status.

    Not an auth error: a plain `PyegeriaAPIException` with relatedHTTPCode 403 (e.g. OMAG-GENERIC-HANDLERS-403-005),
    any non-pyegeria exception whatever its text, a timeout, a refused connection.
    """
    unauthorized, client = _classes()
    if unauthorized is None:
        return None
    for node in cause_chain(exc):
        if isinstance(node, unauthorized):
            return NOT_PERMITTED if _as_code(getattr(node, "related_http_code", None)) == 403 else SIGN_IN_AGAIN
        if isinstance(node, client):
            code = _as_code(getattr(node, "response_code", None) or getattr(node, "http_status_code", None))
            if code in (SIGN_IN_AGAIN, NOT_PERMITTED):
                return code
    return None


def is_auth_error(exc: BaseException | None) -> bool:
    """True when Egeria refused the caller, a 401 or a 403 (see `auth_error_status`)."""
    return auth_error_status(exc) is not None
