"""Connect test for a database credential change, run BEFORE anything is saved.

Design session 2026-10-03 (credential change safety): a stored credential that
is wrong must be refused at the door, not discovered by the next survey. The
test connects to the database's OWN host/port/database_name from the registry
row with the supplied user and password, through the repo's existing
`database_connection` (no new driver call), with a short timeout.

The refusal wording distinguishes "wrong credential" (the server answered and
said no) from "cannot reach" (nothing answered), and never contains the
supplied password.
"""
from __future__ import annotations

CONNECT_TIMEOUT_SECONDS = 5


class CredentialCheckError(Exception):
    """The supplied credential was not saved. `kind` is 'credential' or
    'unreachable'; `str(exc)` is safe to show (password scrubbed)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def scrub(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def check_database_credential(db, user: str, password: str) -> None:
    """Raise CredentialCheckError unless (user, password) connects to `db`.

    Blocking: call off the event loop from async code.
    """
    from resource_explorer.surveyors.database.connection import database_connection

    where = f"{db.host}:{db.port}"
    if not user or not password:
        raise CredentialCheckError(
            "credential",
            f"Credential not saved: a user and a password are both required to test the connection to {where}.",
        )
    try:
        with database_connection(
            db, {"user": user, "password": password},
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
        ):
            return
    except Exception as exc:  # driver errors; classified below
        msg = " ".join(scrub(str(exc), password).split())
        # The server answered and refused: libpq reports those as FATAL.
        if "FATAL" in msg or "authentication" in msg.lower():
            raise CredentialCheckError(
                "credential",
                f"Credential not saved: the server at {where} refused this credential ({msg})",
            ) from None
        raise CredentialCheckError(
            "unreachable",
            f"Credential not saved: could not reach {where} ({msg})",
        ) from None


def probe_database_connection(db_type: str, host: str, port: int, database_name: str,
                              user: str, password: str) -> dict:
    """Test one database's connection BEFORE it is registered (parity G2,
    PI-015: test-then-register). Never raises, never stores anything.

    Returns ``{"status": "ok"|"error", "sentence": str}``. The sentence is the
    only thing shown: it names where and as whom the connection was made and
    how many tables that credential can read, or says why nothing was
    registered. The supplied password is scrubbed from every sentence and is
    never part of the returned dict.

    Blocking: call off the event loop from async code.
    """
    from resource_explorer.registry import DatabaseEntity
    from resource_explorer.surveyors.database import connection

    where = f"{host}:{port}/{database_name}"
    if not user or not password:
        return {"status": "error", "sentence":
                f"Not registered: a user and a password are both required to test the connection to {where}."}
    entity = DatabaseEntity(slug="_probe", display_name="_probe", db_type=db_type or "postgresql",
                            host=host, port=int(port), database_name=database_name)
    try:
        with connection.database_connection(
            entity, {"user": user, "password": password},
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
        ) as conn:
            who = (conn.execute_query("SELECT current_user AS who") or [{}])[0].get("who") or user
            n = (conn.execute_query(
                "SELECT count(*) AS n FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')") or [{}])[0].get("n")
        return {"status": "ok", "sentence": scrub(
            f"Connected to {where} as {who} · {n} table(s) readable with this credential.", password)}
    except Exception as exc:  # driver errors; classified below
        msg = " ".join(scrub(str(exc), password).split())
        if "FATAL" in msg or "authentication" in msg.lower():
            return {"status": "error", "sentence":
                    f"Not registered: the server at {host}:{port} refused this credential ({msg})"}
        return {"status": "error", "sentence":
                f"Not registered: could not reach {host}:{port} ({msg})"}
