"""Keep one secret out of everything a run writes down.

Parity slice G2 (PI-016): a credential override for one run lives in memory
only. Two things can still carry it by accident, both of them somebody else's
error text: a driver exception that echoes the password it was handed, and the
log line written about that exception. This module is the two guards.

* `scrub(obj, secret)` returns a copy of any JSON-shaped structure with every
  occurrence of `secret` (and its encoded forms) inside a string replaced by
  `***`.
* `redacting_logs(secret)` is a context manager that, for the duration of a
  run, rewrites every log record that carries the secret AT CREATION, through
  the logging record factory. That covers every logger and every handler:
  loggers with their own handlers and propagate=False (uvicorn, Prefect),
  handlers added mid-run, and handlers attached to no root. Overlapping runs
  share one installed factory (a lock-guarded set of active secrets), so one
  run ending never unprotects another, and the original factory is restored
  when the last one ends.

Encoded forms covered: percent-encoding (a password inside a DSN), form
encoding, repr/escaped and JSON-escaped text. None of it stores, prints or
returns the secret. An empty secret is a no-op.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.parse
from contextlib import contextmanager

MASK = "***"


def variants(secret: str) -> list[str]:
    """The secret and its encoded spellings, longest first (so a long form is
    replaced before a shorter form inside it)."""
    if not secret or not isinstance(secret, str):
        return []
    forms = {
        secret,
        urllib.parse.quote(secret, safe=""),
        urllib.parse.quote(secret),
        urllib.parse.quote_plus(secret),
        repr(secret)[1:-1],
        secret.encode("unicode_escape").decode("ascii", "replace"),
        json.dumps(secret)[1:-1],
    }
    return sorted((f for f in forms if f), key=len, reverse=True)


def _mask(text: str, forms: list[str]) -> str:
    for f in forms:
        if f in text:
            text = text.replace(f, MASK)
    return text


# Pattern scrubbing for text whose secret is NOT known in advance (an upstream error body, an
# exception message): `scrub` below needs the secret itself. BEST-EFFORT and shape-based: it masks
# what credentials usually look like, it cannot recognise a secret by meaning (prose such as
# "the password is hunter2" is NOT caught). Run it BEFORE truncating, so a cut can't leave half a token.
#
# Rules (each masks the VALUE, keeps the name so the text stays readable):
#  * scheme://user:PASSWORD@host -- up to the LAST '@' of the token, so '@' or '/' inside the password
#    is covered (not when the part after ':' is digits then '/', which is host:port/path).
#  * Cookie:/Set-Cookie: -- the rest of the line.
#  * name = value or "name": value where the NAME contains password/passwd/pwd/passphrase/secret/token/
#    credential/authorization/apikey/api_key/access|private|signing|encryption|client|auth _key, ends in
#    _key or camelCase Key, or is auth/pass; plurals too. The value may be bare, 'single'/"double"
#    quoted (escaped quotes and spaces inside are covered, so is a JSON-escaped form), a [list], or
#    'Bearer|Basic|Token <x>' as a unit.
#  * a bare 'Bearer <token>'; 'Basic|Token <x>' only when x looks like a credential (has a digit or '=').
# Known false positives: any name ending _key / Key, e.g. foreign_key=id. A sentence with the word
# 'token' or 'key' and no ':'/'=' assignment is left alone.
_CONN_URL = re.compile(r"(\b[a-zA-Z][\w+.-]*://[^\s:/@\"']+:)(?!\d+/)[^\s\"']*@")
_COOKIE = re.compile(r"(?i)(\b(?:set-)?cookie\s*[:=]\s*)[^\r\n]+")
_KEYNAME = (r"(?:[\w.-]*(?:password|passwd|pwd|passphrase|secret|token|credential|authorization|apikey|api[_-]?key|"
            r"(?:access|private|signing|encryption|client|auth)[_-]?key|_key)s?[\w-]*|[\w.-]*(?-i:[a-z]Key)s?|auth|pass)")
_VALUE = (r"(?:\\\"(?:(?!\\\").)*(?:\\\"|$)|\"(?:[^\"\\\n]|\\.)*(?:\"|$)|'(?:[^'\\\n]|\\.)*(?:'|$)|\[[^\]\n]*\]|"
          r"(?:bearer|basic|token)\s+[^\s,;&}\"']+|[^\s,;&}\"']+)")
_NAMED = r"(?i)(?<![\w.-])(\\?[\"']?" + _KEYNAME + r"\\?[\"']?\s*[:=]\s*)"
_KEYED = re.compile(_NAMED + _VALUE, re.M)
_KEYED_OBJ = re.compile(_NAMED + r"(?=\{)")
# Postgres DETAIL: Key (api_key)=(sk-123) already exists.
_PG_KEY = re.compile(r"(?i)(\bKey\s*\([^)\n]*?(?:password|passwd|pwd|secret|token|credential|api[_-]?key|_key)[^)\n]*\)\s*=\s*)\([^)\n]*\)")
_BEARER = re.compile(r"(?i)\b(bearer)(\s+)[A-Za-z0-9._~+/=-]{6,}")
_BASIC_TOKEN = re.compile(r"(?i)\b(basic|token)(\s+)(?=[A-Za-z0-9._~+/=-]*[0-9=])[A-Za-z0-9._~+/=-]{8,}")
# Well-known bare credential shapes: AWS access key id, sk-... API keys, GitHub tokens.
_BARE = re.compile(r"\b(?:AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,})\b")
_OBJ_SCAN_LIMIT = 4000


def _mask_objects(text: str) -> str:
    """name: {...} -- the whole object is the value. Balanced braces, a bounded scan; unbalanced masks
    to the end of the line (a truncated body)."""
    out, pos = [], 0
    for m in _KEYED_OBJ.finditer(text):
        if m.start() < pos:
            continue
        i, depth = m.end(), 0
        end = min(len(text), i + _OBJ_SCAN_LIMIT)
        j = i
        while j < end:
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        else:
            nl = text.find("\n", i)
            j = len(text) if nl == -1 else nl
        out.append(text[pos:m.end()] + MASK)
        pos = j
    return "".join(out) + text[pos:] if out else text


def scrub_text(text: str) -> str:
    """Mask credentials in free text by SHAPE. BEST-EFFORT: see the rules above; it does not understand
    prose and will miss a secret that has no recognisable shape.

    Choices worth knowing: an UNQUOTED value ends at whitespace (password=a b leaves ' b'; quote it and the
    whole value goes); a quoted value that never closes (a truncated body) is masked to the end of the line;
    'Bearer' masks 6+ characters only, because a shorter word after it is usually prose ('Bearer auth failed');
    a base64 'Basic <x>' with no digit or '=' is not recognised."""
    if not text:
        return text
    text = _CONN_URL.sub(lambda m: m.group(1) + MASK + "@", text)
    text = _COOKIE.sub(lambda m: m.group(1) + MASK, text)
    text = _PG_KEY.sub(lambda m: m.group(1) + "(" + MASK + ")", text)
    text = _mask_objects(text)
    text = _KEYED.sub(lambda m: m.group(1) + MASK, text)
    text = _BEARER.sub(lambda m: m.group(1) + m.group(2) + MASK, text)
    text = _BASIC_TOKEN.sub(lambda m: m.group(1) + m.group(2) + MASK, text)
    return _BARE.sub(MASK, text)


def scrub(obj, secret: str):
    forms = variants(secret)
    return _scrub(obj, forms) if forms else obj


def _scrub(obj, forms):
    if isinstance(obj, str):
        return _mask(obj, forms)
    if isinstance(obj, dict):
        return {k: _scrub(v, forms) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v, forms) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_scrub(v, forms) for v in obj)
    return obj


_lock = threading.Lock()
_active: dict[str, int] = {}          # secret -> number of overlapping runs holding it
_forms: list[str] = []
_original_factory = None


def _recompute() -> None:
    global _forms
    allf = {f for s in _active for f in variants(s)}
    _forms = sorted(allf, key=len, reverse=True)


def _factory(*args, **kwargs):
    record = _original_factory(*args, **kwargs)
    forms = _forms
    if not forms:
        return record
    try:
        msg = record.getMessage()
    except (TypeError, ValueError):      # a malformed %-format: redact what is there
        msg = str(record.msg)
    if any(f in msg for f in forms):
        record.msg, record.args = _mask(msg, forms), ()
    if record.exc_info:
        text = logging.Formatter().formatException(record.exc_info)
        if any(f in text for f in forms):
            record.exc_text = _mask(text, forms)
            record.exc_info = None
    if record.exc_text and any(f in record.exc_text for f in forms):
        record.exc_text = _mask(record.exc_text, forms)
    if record.stack_info and any(f in record.stack_info for f in forms):
        record.stack_info = _mask(record.stack_info, forms)
    return record


@contextmanager
def redacting_logs(secret: str):
    global _original_factory
    if not secret or not isinstance(secret, str):
        yield
        return
    with _lock:
        if not _active:
            _original_factory = logging.getLogRecordFactory()
            logging.setLogRecordFactory(_factory)
        _active[secret] = _active.get(secret, 0) + 1
        _recompute()
    try:
        yield
    finally:
        with _lock:
            _active[secret] -= 1
            if _active[secret] <= 0:
                del _active[secret]
            _recompute()
            if not _active:
                logging.setLogRecordFactory(_original_factory)
                _original_factory = None
