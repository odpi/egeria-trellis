"""The guards two guarded registry scripts share: `scripts/clear_egeria_pointers_after_reset.py` (clear Egeria
pointers after a reset) and `resource_explorer.egeria_reattach` (re-attach them by qualifiedName).

Moved here, not copied (brief RE-REATTACH-BY-QN, 2026-11-12, §0): the clear script imports every name below into
its own namespace, so its behaviour, its messages and its tests are unchanged. A guard fixed here is fixed for both.

What a guarded run needs: the registry named ONLY through REGISTRY_DATABASE_URL (the database name and the schema
read from it, never the credentials), the open connection's `current_schema()` checked against that schema, a dry
run that writes a 0600 plan file with a hash, an apply that is refused unless the plan file is recent, unedited,
made for the same database and schema and identical to the plan recomputed now, no other RE writer active, a 0600
snapshot written before the first change, and one transaction per table group. No DDL here, and no ProjectRegistry
(opening one runs the schema ensure).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit


class Refused(Exception):
    """A guard stopped the run. Nothing was written (or, if a later group failed, the message says what was)."""


class ApplyFailed(Exception):
    """A table group failed. Names it and the groups that had already committed."""

    def __init__(self, group: str, committed: list, exc: Exception):
        super().__init__(f"{type(exc).__name__} in group {group}")
        self.group, self.committed, self.exc_type = group, committed, type(exc).__name__


# ── connection ────────────────────────────────────────────────────────────────────────────────────────────────

def registry_url() -> str:
    url = os.environ.get("REGISTRY_DATABASE_URL", "")
    if not url:
        raise Refused("REGISTRY_DATABASE_URL is not set; the script names its target only through it")
    return url


def database_name(url: str) -> str:
    """The NAME only, never the credentials."""
    if url.startswith("sqlite"):
        path = url.split("://", 1)[-1].split("?", 1)[0].lstrip("/")
        return path.rsplit("/", 1)[-1] or "?"
    return (urlsplit(url).path or "/").lstrip("/") or "?"


def target_schema(url: str) -> str:
    """The schema unqualified table names resolve in: the search_path named by the URL's options (the
    registry's own parser). SQLite is always 'main'. A Postgres URL with none is refused: the tables would
    resolve in `public`, which is not where RE keeps them."""
    if url.startswith("sqlite"):
        return "main"
    from urllib.parse import parse_qs, unquote
    tokens = " ".join(unquote(o) for o in parse_qs(urlsplit(url).query).get("options", [])).split()
    found = []
    for i, tok in enumerate(tokens):
        if tok.startswith("-csearch_path=") or tok.startswith("--search_path="):
            found.append(tok.split("=", 1)[1])
        elif tok == "-c" and i + 1 < len(tokens) and tokens[i + 1].startswith("search_path="):
            found.append(tokens[i + 1].split("=", 1)[1])
    if not found:
        raise Refused("REGISTRY_DATABASE_URL names no schema (no ?options=-csearch_path=<schema>); the script "
                      "will not run against whatever schema the server defaults to")
    if len(found) > 1:
        raise Refused("REGISTRY_DATABASE_URL names search_path more than once; libpq would use the last one and "
                      "this script will not guess which")
    schema = found[0].strip().strip('"')
    if "," in schema or not schema:
        raise Refused("the search_path in REGISTRY_DATABASE_URL must be exactly one schema (no comma list): "
                      "unqualified table names would resolve across the whole list")
    from resource_explorer.registry_label import _search_path
    if _search_path(urlsplit(url).query) != schema:
        raise Refused("the registry's own parser reads the search_path differently from this script; refusing")
    return schema


def check_live_schema(conn, url: str, schema: str) -> str:
    """The URL's search_path text is not evidence of where writes go; the open connection is. Postgres: ask it
    (`current_schema()`) and refuse unless it equals the schema the URL and --schema named. SQLite is 'main'."""
    if url.startswith("sqlite"):
        return "main"
    live = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
    if live != schema:
        raise Refused(f"the open connection's current_schema() is {live!r}, not {schema!r} (the schema named by "
                      "the URL and --schema); unqualified table names would resolve there. Nothing was written.")
    return live


def connect(url: str):
    import sqlite3

    from sqlalchemy import create_engine

    from resource_explorer.registry import ConnectionWrapper

    is_pg = url.startswith("postgresql")
    raw = create_engine(url, pool_pre_ping=True).raw_connection()
    if not is_pg:
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys=ON")
    return ConnectionWrapper(raw, is_pg)


def _missing_table(conn, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} WHERE 1 = 0").fetchall()
        return False
    except Exception as exc:
        conn.raw_conn.rollback()
        if getattr(exc, "pgcode", None) == "42P01" or "no such table" in str(exc).lower():
            return True
        raise Refused(f"could not check whether table {table} exists ({type(exc).__name__}); only an undefined-table "
                      "error counts as missing. Nothing was written.") from exc


def check_columns(conn, want: dict) -> None:
    """Every (table -> columns) named must be readable. A missing one stops the run, naming it."""
    for table in sorted(want):
        for col in sorted(want[table]):
            try:
                conn.execute(f"SELECT {col} FROM {table} WHERE 1 = 0").fetchall()
            except Exception as exc:
                conn.raw_conn.rollback()
                raise Refused(f"schema check failed: {table}.{col} is not readable "
                              f"({type(exc).__name__}); the script will not guess. Nothing was written.") from exc


# ── time ──────────────────────────────────────────────────────────────────────────────────────────────────────

_TS = re.compile(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d)(:\d\d)?(\.\d+)?Z?$")


def parse_utc(text: str, what: str) -> str:
    m = _TS.match((text or "").strip())
    if not m:
        raise Refused(f"{what} must be a UTC time like 2026-10-08T18:30Z, got {text!r}")
    return f"{m.group(1)}T{m.group(2)}{m.group(3) or ':00'}"


def utcnow() -> datetime:
    return datetime.utcnow()


def parse_who_when(token: str, now: datetime, flag: str = "--cleared-by") -> tuple[str, str]:
    """`<who>/<UTC>` for the apply's accountable person, within the last 24 hours."""
    who, sep, when = (token or "").partition("/")
    if not sep or not who.strip() or not when:
        raise Refused(f"{flag} must be <who>/<UTC time>, e.g. dwolfson/2026-10-08T19:05Z")
    stamp = parse_utc(when, f"{flag} time")
    at = datetime.fromisoformat(stamp)
    if at > now + timedelta(minutes=5) or at < now - timedelta(hours=24):
        raise Refused(f"{flag} time {stamp} is not within the last 24 hours (now {now:%Y-%m-%dT%H:%M:%S})")
    return who.strip(), stamp


# ── plan ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _nonempty(col: str) -> str:
    return f"({col} IS NOT NULL AND {col} <> '')"


def _rows(conn, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def plan_hash(plan: dict) -> str:
    body = {k: v for k, v in plan.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def write_0600_json(path: Path, payload: dict, indent: int = 1) -> None:
    """Write `payload` to `path` with mode 0600 (also when the file already existed), fsynced, then the dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)            # a pre-existing .tmp keeps its old mode otherwise
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=indent, default=str, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def check_saved_plan(saved: dict, *, script_version: str, schema: str, live: str, name: str, plan_hash_arg: str,
                     now: datetime, max_age_minutes: int, what: str = "script") -> dict:
    """The saved dry-run plan may be applied only if it was made by this version, for this schema, connection
    schema and database, is unedited and is the one --plan-hash names, and is recent. Returns the saved plan."""
    sp = saved.get("plan") or {}
    if sp.get("script_version") != script_version:
        raise Refused(f"this plan was made by a different version of the {what} "
                      f"({sp.get('script_version') or 'none recorded'}, this is {script_version}); "
                      "run the dry run again")
    if sp.get("schema") != schema:
        raise Refused(f"the plan file was made for schema {sp.get('schema')!r}, not {schema!r}")
    if sp.get("current_schema") != live:
        raise Refused(f"the plan file was made under current_schema {sp.get('current_schema')!r}, the "
                      f"connection now reports {live!r}")
    if sp.get("database") != name:
        raise Refused(f"the plan file was made for database {sp.get('database')!r}, not {name!r}")
    if plan_hash(sp) != sp.get("hash") or sp.get("hash") != plan_hash_arg:
        raise Refused("the plan file's hash does not match --plan-hash (or the file was edited)")
    made = datetime.fromisoformat(saved.get("generated_at", "1970-01-01T00:00:00"))
    if now - made > timedelta(minutes=max_age_minutes) or made > now + timedelta(minutes=1):
        raise Refused(f"the dry run is older than {max_age_minutes} minutes; run it again")
    return sp


# ── the active-writer refusal ─────────────────────────────────────────────────────────────────────────────────

def activity_guard(conn, exclude_activity_ids: tuple = ()) -> list[str]:
    """Reasons the registry looks in use. Empty means nothing looks active.

    `exclude_activity_ids`: activity_log rows that belong to the caller itself (the Alignment apply's own row),
    which are not "another writer"."""
    from resource_explorer.run_reconciler import _is_alive, owner_of

    reasons = []
    for r in _rows(conn, "SELECT id, detail FROM activity_log WHERE status = 'running'"):
        if r["id"] in exclude_activity_ids:
            continue
        owner = owner_of(r.get("detail"))
        alive = _is_alive(owner) if owner else None
        if alive is not False:
            reasons.append(f"activity_log row {r['id']} is running ("
                           + ("its owner process is alive" if alive else "its owner cannot be judged") + ")")
    for r in _rows(conn, "SELECT id, state FROM runs WHERE state IN ('claimed', 'running')"):
        reasons.append(f"runs row {r['id']} is {r['state']}")
    n = _rows(conn, "SELECT COUNT(*) AS n FROM egeria_outbox WHERE status = 'running'")[0]["n"]
    if n:
        reasons.append(f"{n} egeria_outbox row(s) are running")
    return reasons


# ── apply ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _group(conn, fn):
    try:
        fn()
        conn.commit()
    except Exception:
        conn.raw_conn.rollback()
        raise


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
