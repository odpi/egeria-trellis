"""Keep database passwords out of everything Prefect persists.

Prefect stores a flow run's parameters in its own database and shows them in
its UI. A database password therefore must never be a flow-run parameter, a
task parameter or part of a persisted result. Two mechanisms, one per kind of
credential:

* STORED credentials (the ones on the registry's database row). Nothing is
  handed over at all: the code that runs inside the flow/task looks the row up
  in the registry at the moment it connects (`resolve_credentials`), in
  memory, and never logs or returns it.
* OVERRIDE credentials (a credential typed for one run, parity G2). They are
  never stored anywhere, so the only place they can live is RE's own process
  memory. `put()` keeps them there, Fernet-encrypted under a key generated at
  import and never written down, behind a random opaque reference with a
  short TTL; the flow gets the reference only. That is valid ONLY for a flow
  that executes in the same process (an in-process flow call) -- a Prefect
  worker is a different process and cannot see this store, which is why a
  step dispatched to a worker never receives an override (see
  PREFECT-CREDENTIALS-IMPLEMENTED.md for the DDL that would change that).

`assert_no_credentials` is the fail-loud tripwire at every crossing into
Prefect: a parameter key that names a secret, a DSN carrying a password, or a
value equal to a known password raises, so a regression cannot ship quietly.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from typing import Any, Iterable

from cryptography.fernet import Fernet

#: How long an override reference stays valid if the run never revokes it.
DEFAULT_TTL_SECONDS = 3600.0

_KEY = Fernet(Fernet.generate_key())      # per process, never persisted
_lock = threading.Lock()
_store: dict[str, tuple[float, bytes]] = {}

#: Parameter KEY names that denote a secret. `credential_ref` is deliberately
#: not matched: it is the opaque reference, not a secret.
_SECRET_KEY = re.compile(r"(passw(or)?d|passwd|(^|_)pwd($|_)|secret|token|api_?key)", re.I)
_CREDENTIAL_KEYS = frozenset({"db_user", "db_pwd", "db_password"})
_DSN_WITH_PASSWORD = re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@", re.I)


class CredentialLeakError(RuntimeError):
    """A credential was about to cross into Prefect. Never carries the value."""


class CredentialHandoffExpired(RuntimeError):
    """The one-run credential reference is unknown or past its TTL."""


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def put(user: str, password: str, *, ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock=time.monotonic) -> str:
    """Keep (user, password) in this process's memory; return an opaque reference."""
    ref = "cred-" + secrets.token_urlsafe(24)
    blob = _KEY.encrypt(f"{user}\x00{password}".encode())
    with _lock:
        _sweep_locked(clock())
        _store[ref] = (clock() + ttl_seconds, blob)
    return ref


def get(ref: str, *, clock=time.monotonic) -> tuple[str, str]:
    """(user, password) for `ref`. Raises CredentialHandoffExpired, loudly."""
    with _lock:
        entry = _store.get(ref)
        if entry is None or entry[0] < clock():
            _store.pop(ref, None)
            raise CredentialHandoffExpired(
                "this run's credential is no longer available in this process")
        blob = entry[1]
    user, _, pwd = _KEY.decrypt(blob).decode().partition("\x00")
    return user, pwd


def revoke(ref: str) -> None:
    with _lock:
        _store.pop(ref, None)


def _sweep_locked(now: float) -> None:
    for r in [r for r, (exp, _) in _store.items() if exp < now]:
        del _store[r]


def active_references() -> int:
    with _lock:
        return len(_store)


def split_credentials(entity: Any, runner_kwargs: dict, credential_scope: str = ""
                      ) -> tuple[dict, tuple[str, str] | None]:
    """(kwargs with no db_user/db_pwd, override (user, pwd) or None).

    A credential is an OVERRIDE when the caller says so (`credential_scope`)
    or when it differs from the entity's stored one; a stored credential is
    simply dropped, because the worker resolves it from the registry.
    """
    kw = dict(runner_kwargs or {})
    user, pwd = _text(kw.pop("db_user", "")), _text(kw.pop("db_pwd", ""))
    if not (user or pwd):
        return kw, None
    stored = (_text(getattr(entity, "db_user", "")), _text(getattr(entity, "db_password", "")))
    if not credential_scope and (user, pwd) == stored:
        return kw, None
    return kw, (user, pwd)


def resolve_credentials(entity: Any, credential_ref: str = "") -> tuple[str, str]:
    """Credentials a step connects with, resolved where the step runs.

    The override behind `credential_ref` if one is given, else the entity's
    stored ones. In memory only; callers must not log or return the result.
    """
    if credential_ref:
        return get(credential_ref)
    return (_text(getattr(entity, "db_user", "")), _text(getattr(entity, "db_password", "")))


def assert_no_credentials(params: Any, known_secrets: Iterable[str] = (), where: str = "Prefect") -> None:
    """Raise CredentialLeakError if `params` looks like it carries a credential.

    The message names the offending KEY only, never a value.
    """
    secrets_ = [s for s in known_secrets if isinstance(s, str) and s]
    _walk(params, secrets_, "", where)


def _walk(obj: Any, secrets_: list[str], path: str, where: str) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            here = f"{path}.{ks}" if path else ks
            if ks.lower() in _CREDENTIAL_KEYS or _SECRET_KEY.search(ks):
                raise CredentialLeakError(
                    f"refusing to send parameter '{here}' to {where}: it names a credential")
            _walk(v, secrets_, here, where)
    elif isinstance(obj, (list, tuple, set)):
        for i, v in enumerate(obj):
            _walk(v, secrets_, f"{path}[{i}]", where)
    elif isinstance(obj, str):
        if _DSN_WITH_PASSWORD.search(obj):
            raise CredentialLeakError(
                f"refusing to send '{path}' to {where}: its value is a URL carrying a password")
        for s in secrets_:
            if s in obj:
                raise CredentialLeakError(
                    f"refusing to send '{path}' to {where}: its value contains a known password")
