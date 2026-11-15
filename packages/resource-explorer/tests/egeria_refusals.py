"""What pyegeria actually raises when Egeria refuses the caller, for tests (TR-22).

RE decides auth errors by exception class and HTTP code (`trellis_auth.egeria_errors`), never by message text, so a
test that fakes a refusal must raise what pyegeria raises, not a RuntimeError carrying the old words (the Portal's
PO-9 change made the same move, egeria-workspaces caf07d7b):

- `refused_token()`: a transport 401 (a bad or expired token): PyegeriaUnauthorizedException, related_http_code 401;
- `not_permitted(words)`: Egeria's own UserNotAuthorizedException (relatedHTTPCode 403), which pyegeria raises as
  PyegeriaUnauthorizedException; its text carries `words` (an OPEN-METADATA-SECURITY-403-… sentence);
- `http_status(code, words)`: an HTTP error status on the wire: PyegeriaClientException.
"""
from __future__ import annotations

import httpx
from pyegeria.core._exceptions import PyegeriaClientException, PyegeriaUnauthorizedException

_REQ = httpx.Request("POST", "https://egeria.test:9443/x")

#: The live 2026-11-05 words of a refused token.
LIVE_401_TEXT = ("=> AUTHORIZATION_ERROR_401 => User not authorized received for user - `peterprofile` "
                 "requesting on behalf of `peterprofile`")


def refused_token(words: str = LIVE_401_TEXT, user: str = "peterprofile") -> PyegeriaUnauthorizedException:
    return PyegeriaUnauthorizedException(httpx.Response(401, request=_REQ, text=words), {},
                                         additional_info={"userid": user})


def not_permitted(words: str = "OPEN-METADATA-SECURITY-403-020 User peterprofile is not authorized to read element x",
                  user: str = "peterprofile") -> PyegeriaUnauthorizedException:
    msg_id = words.split(" ", 1)[0] if words.startswith("OPEN-METADATA-SECURITY-403") else ""
    body = {"relatedHTTPCode": 403, "exceptionClassName": "UserNotAuthorizedException",
            "exceptionErrorMessageId": msg_id, "exceptionErrorMessage": words}
    return PyegeriaUnauthorizedException(httpx.Response(200, request=_REQ, json=body), {},
                                         additional_info={**body, "userid": user})


def http_status(code: int, words: str = "refused") -> PyegeriaClientException:
    return PyegeriaClientException(httpx.Response(code, request=_REQ, text=words), {}, {"reason": words})
