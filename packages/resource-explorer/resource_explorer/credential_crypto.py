"""Encryption at rest for `databases.db_password` (registry.py).

Design context
---------------
`docs/design-notes/exchange/REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` §7
(the architecture session's ruling on the `ASK-DATABASE-CREDENTIAL-
CAPABILITY-VISIBILITY.md` question): pyegeria only exposes
`save_client_side_secret`/`delete_client_side_secret` — there is no read API
for secrets, so RE's local execution path cannot resolve credentials through
Egeria and `databases.db_password` cannot simply be dropped in favor of the
`.omsecrets` file. §7's actual recommendation is *encrypt it in place*, not
relocate it: "RE's own `databases.db_password` should stop being a
clear-text column and become RE's own store (encrypted at rest, or the OS
keychain)". This module is the "encrypted at rest" half of that — the
`.omsecrets` projection lives in `omsecrets_store.py`.

Why Fernet (`cryptography`, already a pyproject.toml dependency — no new
dependency needed)
-------------------------------------------------------------------------
`cryptography>=43.0.0` is already required by this package (see
pyproject.toml; pulled in via `packages/trellis-auth`'s JWT signing).
`cryptography.fernet.Fernet` is the smallest adequate primitive already on
hand: authenticated symmetric encryption (AES-128-CBC + HMAC-SHA256 under
the hood), a URL-safe base64 wire format that drops straight into a TEXT
column, and no new key-management surface beyond "one 32-byte key". A
password-hashing KDF (bcrypt/argon2) would be the wrong tool — those are
one-way, and this value must be recovered in plaintext to open a database
connection. Nothing heavier (envelope encryption, a KMS integration) is
justified for a single-host dev tool whose credential store already lives
in a shared Postgres/SQLite registry the operator controls.

Key management — mirrors `auth.jwt_secret()`
----------------------------------------------
Same two-env-var-then-derive shape as the app-JWT secret
(`resource_explorer/auth.py::jwt_secret()`, itself matching Egeria Advisor):
`RE_DB_CREDENTIAL_KEY` then `TRELLIS_DB_CREDENTIAL_KEY`, and if neither is
set, a stable per-host key derived from the hostname (logged once, loudly —
a real deployment is expected to set the variable; the derived key means
already-encrypted rows stay readable across a process restart but not a
migration to a different host). The key is never stored in the `databases`
table alongside the ciphertext it protects — it lives only in the process
environment (or the derived-from-hostname fallback), never in the registry.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field
from pydantic_settings import BaseSettings

from resource_explorer.config import _ENV_FILE_CONFIG

log = logging.getLogger(__name__)

#: Prefix marking a `db_password` column value as Fernet-encrypted. A
#: clear-text password stored before this shipped, or any value that
#: predates this convention, will not start with this prefix — that is
#: exactly the signal `decrypt_db_password` uses to detect the lazy-
#: migration case (see its docstring) rather than a version tag with any
#: other meaning.
ENCRYPTED_PREFIX = "enc:v1:"

_DERIVED_KEY_WARNED = False


class _CredentialKeyConfig(BaseSettings):
    """`.env`-aware source for the two credential-encryption key env vars.

    Same nested-BaseSettings idiom `auth._AuthSecretsConfig` uses and for the
    same reason: reading straight from `os.environ` bypasses `.env`, which
    bit this package twice already for the JWT secret (see that class's
    docstring). Two plain fields combined by `X or Y`, not one field with
    `AliasChoices`, to keep the RE-wins-over-TRELLIS precedence explicit
    rather than dependent on pydantic-settings' per-source alias resolution
    order.
    """

    re_db_credential_key: str = Field(default="", alias="RE_DB_CREDENTIAL_KEY")
    trellis_db_credential_key: str = Field(default="", alias="TRELLIS_DB_CREDENTIAL_KEY")

    model_config = _ENV_FILE_CONFIG


_key_config_cache: _CredentialKeyConfig | None = None


def _key_config() -> _CredentialKeyConfig:
    global _key_config_cache
    if _key_config_cache is None:
        _key_config_cache = _CredentialKeyConfig()
    return _key_config_cache


def reset_credential_key_cache() -> None:
    """Drop the cached key config — for tests that vary the environment."""
    global _key_config_cache, _DERIVED_KEY_WARNED
    _key_config_cache = None
    _DERIVED_KEY_WARNED = False


def _raw_secret() -> str:
    cfg = _key_config()
    secret = cfg.re_db_credential_key or cfg.trellis_db_credential_key
    if secret:
        return secret
    global _DERIVED_KEY_WARNED
    if not _DERIVED_KEY_WARNED:
        _DERIVED_KEY_WARNED = True
        log.warning(
            "credential_crypto: neither RE_DB_CREDENTIAL_KEY nor "
            "TRELLIS_DB_CREDENTIAL_KEY is set — deriving a per-host key. "
            "Encrypted db_password values will not survive a move to "
            "another machine. Set one of those env vars for a real deployment."
        )
    machine = os.environ.get("HOSTNAME", "resource-explorer-local")
    return f"resource-explorer-db-credential-{machine}"


def _fernet() -> Fernet:
    """Build the Fernet cipher from whatever secret `_raw_secret()` resolves.

    Fernet requires a 32-byte urlsafe-base64-encoded key; the configured or
    derived secret is an arbitrary string, so it is stretched to exactly 32
    bytes via SHA-256 first (the same "hash an arbitrary secret into a fixed-
    size key" step `auth.jwt_secret()`'s HS256 usage does implicitly).
    """
    digest = hashlib.sha256(_raw_secret().encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_db_password(plaintext: str) -> str:
    """Encrypt a `db_password` value for storage. `""` stays `""` —
    an empty stored password is a real, meaningful value (no credentials on
    file yet) and encrypting it would only add a decrypt round-trip that
    always yields "" back, for no confidentiality benefit."""
    if not plaintext:
        return ""
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return ENCRYPTED_PREFIX + token.decode("ascii")


def is_encrypted(stored: str) -> bool:
    return bool(stored) and stored.startswith(ENCRYPTED_PREFIX)


class CredentialUnreadableError(ValueError):
    """A stored credential exists but cannot be decrypted (wrong/rotated key).

    The message is fixed and names only the slug: the underlying decrypt
    error names the key env vars and is never carried here. Subclasses
    ValueError so existing `except ValueError` callers keep working.
    """

    def __init__(self, slug: str = ""):
        self.slug = slug
        super().__init__(
            f"credential unreadable: re-enter credentials for {slug}"
            if slug else "credential unreadable: re-enter credentials"
        )


#: `DatabaseEntity.credential_status` values.
CREDENTIAL_OK = "ok"
CREDENTIAL_NONE = "none"
CREDENTIAL_UNREADABLE = "unreadable"
#: Fixed, secret-free reason string shown beside an unreadable credential.
CREDENTIAL_UNREADABLE_REASON = "credential unreadable · re-enter credentials"


def decrypt_db_password(stored: str) -> str:
    """Recover the plaintext `db_password` from a stored column value.

    Lazy-migration path: a value that does not carry `ENCRYPTED_PREFIX` is
    treated as a pre-existing clear-text row (written before this shipped)
    and returned as-is — the caller (`registry.py::_row_to_database`) is
    responsible for re-encrypting and writing it back so the next read finds
    it already migrated. This module never touches the database itself; it
    only ever converts one string to another.

    A value that DOES carry the prefix but fails to decrypt (wrong/rotated
    key, corrupted row) raises rather than silently returning the ciphertext
    or an empty string — either of those would look like "no password set"
    or "the credential is a garbage string", both worse than a loud failure
    a caller can catch and turn into a clear "re-enter credentials" prompt.
    """
    if not stored:
        return ""
    if not is_encrypted(stored):
        return stored
    token = stored[len(ENCRYPTED_PREFIX):].encode("ascii")
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "db_password could not be decrypted — the configured "
            "RE_DB_CREDENTIAL_KEY/TRELLIS_DB_CREDENTIAL_KEY does not match "
            "the key this value was encrypted with (rotated, or a per-host "
            "derived key that changed hosts). Re-enter credentials for "
            "this database."
        ) from exc
