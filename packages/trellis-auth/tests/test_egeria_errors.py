"""TR-22: Egeria auth errors by exception class and HTTP code, never by message text."""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")
exc_mod = pytest.importorskip("pyegeria.core._exceptions")

from trellis_auth.egeria_errors import NOT_PERMITTED, SIGN_IN_AGAIN, auth_error_status, is_auth_error  # noqa: E402

_REQ = httpx.Request("POST", "https://egeria.test:9443/x")


def refused_token(words="refused"):
    """A transport 401: what pyegeria raises for a refused or expired token."""
    return exc_mod.PyegeriaUnauthorizedException(httpx.Response(401, request=_REQ, text=words), {},
                                                 additional_info={"userid": "peterprofile"})


def not_permitted(words="peterprofile is not authorized"):
    """Egeria's own UserNotAuthorizedException (relatedHTTPCode 403), raised by pyegeria as Unauthorized."""
    body = {"relatedHTTPCode": 403, "exceptionClassName": "UserNotAuthorizedException",
            "exceptionErrorMessageId": "OPEN-METADATA-SECURITY-403-020", "exceptionErrorMessage": words}
    return exc_mod.PyegeriaUnauthorizedException(httpx.Response(200, request=_REQ, json=body), {},
                                                 additional_info={**body, "userid": "peterprofile"})


def delete_with_dependents():
    """OMAG-GENERIC-HANDLERS-403-005: relatedHTTPCode 403, an InvalidParameterException. Not an auth error."""
    body = {"relatedHTTPCode": 403, "exceptionClassName": "InvalidParameterException",
            "exceptionErrorMessageId": "OMAG-GENERIC-HANDLERS-403-005",
            "exceptionErrorMessage": "AUTHORIZATION_ERROR HTTP CODE: 403 element has dependents"}
    return exc_mod.PyegeriaAPIException(httpx.Response(200, request=_REQ, json=body), {},
                                        additional_info=body)


def transport(status):
    return exc_mod.PyegeriaClientException(httpx.Response(status, request=_REQ, text="no"), {}, {})


def test_a_refused_token_is_sign_in_again():
    assert auth_error_status(refused_token()) == SIGN_IN_AGAIN == 401


def test_egeria_refusing_a_signed_in_user_is_not_permitted():
    assert auth_error_status(not_permitted()) == NOT_PERMITTED == 403


def test_a_403_that_is_not_a_refusal_is_not_an_auth_error():
    """The case text matching got wrong: the message even says AUTHORIZATION_ERROR and HTTP CODE: 403."""
    assert auth_error_status(delete_with_dependents()) is None


@pytest.mark.parametrize("status,expected", [(401, 401), (403, 403), (404, None), (500, None)])
def test_a_transport_status_from_egeria(status, expected):
    assert auth_error_status(transport(status)) == expected


def test_text_alone_is_never_an_auth_error():
    for words in ("AUTHORIZATION_ERROR_401", "HTTP CODE: 403", "User not authorized",
                  "OPEN-METADATA-SECURITY-403-020", "401 Unauthorized"):
        assert auth_error_status(RuntimeError(words)) is None


def test_a_non_egeria_http_403_is_not_egerias():
    """GitHub, PyPI, OSV: a Trellis app talks to them too."""
    resp = httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/x"))
    assert auth_error_status(httpx.HTTPStatusError("forbidden", request=resp.request, response=resp)) is None


def test_the_cause_chain_is_followed_cause_first():
    try:
        try:
            raise not_permitted()
        except Exception as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert auth_error_status(outer) == 403
    try:
        try:
            raise refused_token()
        except Exception:
            raise ValueError("while handling")
    except ValueError as outer:
        assert auth_error_status(outer) == 401          # __context__ when there is no __cause__


def test_a_suppressed_context_is_not_followed():
    """`raise … from None` says the earlier error is not the reason (PO-9's advisory)."""
    try:
        try:
            raise refused_token()
        except Exception:
            raise ValueError("a different problem") from None
    except ValueError as outer:
        assert auth_error_status(outer) is None


def test_none_and_plain_errors():
    assert auth_error_status(None) is None
    assert is_auth_error(TimeoutError()) is False
    assert is_auth_error(refused_token()) is True
