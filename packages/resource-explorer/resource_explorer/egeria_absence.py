"""Is this element absent from Egeria? One shared, strict answer.

Three outcomes, and only the first lets a caller create something:

  absent      Egeria said there is no such element: pyegeria's `NO_ELEMENTS_FOUND` / `NO_ELEMENT_FOUND`
              answer, a `PyegeriaNotFoundException`, or a `PyegeriaAPIException` whose `related_http_code` is
              404 — UNLESS Egeria's message id says the SERVER is not available or not known
              (`OMAG-MULTI-TENANT-404-*`): a stopped or misnamed view server answers every look-up with a
              typed 404 (live probe, 2026-10-13), and that is "could not ask", never "not there".
  present     a populated answer.
  unreadable  everything else: unauthorised (401/403, even though that class subclasses the API
              exception), transport and timeout failures, validation errors, any other exception, text we do
              not recognise, an empty or missing answer. NEVER a reason to create or to forget a cached GUID.

It deliberately does not read exception TEXT: a GUID containing "404" inside a timeout message, or a "user
not found" error, must not read as a missing element (a duplicate blueprint would follow).
"""
from __future__ import annotations

ABSENT = "absent"
PRESENT = "present"
UNREADABLE = "unreadable"

#: pyegeria.core._globals.NO_ELEMENTS_FOUND, repeated so this module imports without pyegeria.
NO_ELEMENTS_FOUND = "No elements found"
#: pyegeria.core._globals.NO_ELEMENT_FOUND: the singular form the by-GUID / by-unique-name reads return.
NO_ELEMENT_FOUND = "No element found"
MISS_ANSWERS = (NO_ELEMENTS_FOUND, NO_ELEMENT_FOUND)

#: Egeria's message ids for "the server is not available / not known" (OMAGServerPlatform multi-tenant
#: handler). They arrive as a typed 404, but they are about the server, not the element.
SERVER_UNAVAILABLE_MESSAGE_PREFIX = "OMAG-MULTI-TENANT-404"


def is_absent(result_or_exc) -> str:
    """'absent' | 'present' | 'unreadable' for a returned answer or a raised exception."""
    if isinstance(result_or_exc, BaseException):
        return _classify_exception(result_or_exc)
    if isinstance(result_or_exc, str):
        # Exactly pyegeria's sentence, as `native_survey_run.absent_answers` reads it: case-insensitive,
        # a trailing period stripped. Never a substring ("user not found" is a different sentence).
        said = result_or_exc.strip().rstrip(".").strip().lower()
        return ABSENT if said in {m.lower() for m in MISS_ANSWERS} else UNREADABLE
    if isinstance(result_or_exc, (dict, list, tuple)):
        return PRESENT if result_or_exc else UNREADABLE
    return UNREADABLE


def _classify_exception(exc: BaseException) -> str:
    try:
        from pyegeria.core._exceptions import (
            PyegeriaAPIException,
            PyegeriaNotFoundException,
            PyegeriaUnauthorizedException,
        )
    except ImportError:                                   # no pyegeria: nothing here can be a not-found
        return UNREADABLE
    if isinstance(exc, PyegeriaUnauthorizedException):    # before the API exception it subclasses
        return UNREADABLE
    if egeria_message_id(exc).startswith(SERVER_UNAVAILABLE_MESSAGE_PREFIX):   # the message id first
        return UNREADABLE
    if isinstance(exc, PyegeriaNotFoundException):
        return ABSENT
    if isinstance(exc, PyegeriaAPIException) and str(getattr(exc, "related_http_code", "")).strip() == "404":
        return ABSENT
    return UNREADABLE


def egeria_message_id(exc: BaseException) -> str:
    """Egeria's `exceptionErrorMessageId` carried by a pyegeria exception, or ''. Read from the fields
    pyegeria fills (`response_egeria_msg_id`, `additional_info`), never from the exception's text."""
    found = getattr(exc, "response_egeria_msg_id", "") or ""
    if not found:
        info = getattr(exc, "additional_info", None)
        if isinstance(info, dict):
            found = info.get("exceptionErrorMessageId") or ""
    return str(found).strip()


#: Egeria's message id for "this instance is not visible" (a SOFT-DELETED element the repository handler declines
#: to return). Observed 2026-10-16: RelationalDatabase read after delete -> HTTP 500 wrapping this id.
NOT_VISIBLE_MESSAGE_ID = "OMAG-REPOSITORY-HANDLER-400-010"


def is_not_visible(exc: BaseException) -> bool:
    """True when Egeria answered a read of ONE GUID with 400-010 ("not visible"): matched on the message id,
    never the sentence, and never for a sign-in or zone refusal (401/403 is not absence).

    This is NOT part of `is_absent`: 400-010 also fires for an instance this caller merely cannot see, so it
    must never license a create or a forgotten cache. It is only what `asset_exists` reads of a POINTER RE holds
    (architect ruling 2026-10-16 section 3): the pointer no longer leads anywhere usable."""
    try:
        from pyegeria.core._exceptions import PyegeriaUnauthorizedException
        if isinstance(exc, PyegeriaUnauthorizedException):
            return False
    except ImportError:
        pass
    return egeria_message_id(exc) == NOT_VISIBLE_MESSAGE_ID


def egeria_error_detail(exc: BaseException, limit: int = 1500) -> str:
    """Egeria's own words for a failed call, or '' when the exception carries no Egeria response.

    pyegeria's CLIENT_ERROR_400 text names only the endpoint (live 2026-10-14: a DataGrainAnnotation
    create refused for a Jackson type mismatch read "Client error occurred with status code `<url>`").
    The response body (FFDCResponseBase: relatedHTTPCode, exceptionClassName, exceptionErrorMessageId,
    exceptionErrorMessage, exceptionUserAction) is kept on the exception, as `additional_info['reason']`
    and as `response`; read from there, never parsed out of str(exc). Walks __cause__ / __context__."""
    import json

    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        info = getattr(cur, "additional_info", None)
        raw = info.get("reason") if isinstance(info, dict) else None
        response = getattr(cur, "response", None)
        if not raw and response is not None:
            raw = getattr(response, "text", None)
        if raw:
            raw = str(raw)
            status = getattr(cur, "http_status_code", None) or getattr(response, "status_code", None)
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            if isinstance(body, dict):
                status = body.get("relatedHTTPCode") or status
                parts = [f"HTTP {status}" if status else "", str(body.get("exceptionClassName") or "").rsplit(".", 1)[-1],
                         str(body.get("exceptionErrorMessageId") or "")]
                head = " ".join(p for p in parts if p)
                message = str(body.get("exceptionErrorMessage") or body.get("userMessage") or "").strip()
                action = str(body.get("exceptionUserAction") or "").strip()
                text = f"{head}: {message}" if message else (head or raw)
                if action:
                    text += f" (user action: {action})"
            else:
                text = f"HTTP {status}: {raw}" if status else raw
            return text[:limit]
        cur = cur.__cause__ or cur.__context__
    return ""


def lookup_could_not_be_made(name: str, cause) -> str:
    """The words for a by-name look-up whose outcome is 'unreadable', for a caller that was about to create.

    `cause` is the raised exception, or None for a call that gave no answer. A look-up that could not be made
    is never "not there": the create is not attempted, and these words say so, in one form every create path
    shares (surveyors, doc sources, the outbox, reference-data proposals)."""
    if cause is None:
        detail = "Egeria gave no answer"
    else:
        detail = f"{type(cause).__name__}: {' '.join(str(cause).split())[:200] or '(no message)'}"
    return (f"the Egeria look-up for {name!r} could not be made ({detail}), so nothing was created: "
            "RE cannot tell whether it already exists")


def lookup_exception_is_absent(exc: BaseException) -> bool:
    """True only when a RAISED look-up is Egeria's own 'no such element' (see `is_absent`)."""
    return is_absent(exc) == ABSENT


_GUID_SHAPE = None


def _is_guid(text) -> bool:
    global _GUID_SHAPE
    if _GUID_SHAPE is None:
        import re
        _GUID_SHAPE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
    return isinstance(text, str) and bool(_GUID_SHAPE.match(text.strip()))


def _guid_in(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("elementGUID") or item.get("guid")
                   or (item.get("elementHeader") or {}).get("guid") or "").strip()
    return ""


def guid_from_answer(answer, name: str, error: type = RuntimeError) -> str:
    """The ONE reader of a by-name look-up's answer, before a create: three outcomes.

    * found   a GUID-shaped string, or a dict / first list item carrying one: that GUID;
    * absent  pyegeria's own miss sentence (`is_absent` says ABSENT): "", the only answer that may create;
    * anything else (None, "", {}, [], a non-GUID string or item, another type): could not check, raised as
      `error(lookup_could_not_be_made(...))` in the caller's own vocabulary. It used to read as "" and create.
    """
    first = answer[0] if isinstance(answer, list) and answer else answer
    candidate = _guid_in(first)
    if _is_guid(candidate):
        return candidate
    if isinstance(answer, str) and is_absent(answer) == ABSENT:
        return ""
    cause = None if answer is None else ValueError(f"unexpected answer {answer!r:.80}")
    raise error(lookup_could_not_be_made(name, cause))
