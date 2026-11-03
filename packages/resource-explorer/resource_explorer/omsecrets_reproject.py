"""Re-project RE's stored database credentials into the `.omsecrets` file.

Why this exists: RE writes a database's credential to two places when it is
registered or updated -- its own registry (`databases.db_user` /
`db_password`, encrypted at rest) and the secrets file Egeria's engine host
reads per survey action (`omsecrets_store.py`). A redeploy that recreates the
secrets directory (2026-09-29, again 2026-09-30) deletes the file while the
registry still holds every credential, and every Egeria-side survey then fails
with `FATAL: role "default" does not exist`. This module re-derives the file
from what the registry already holds, through RE's own decryption
(`ProjectRegistry.get_database` -> `credential_crypto.decrypt_db_password`,
the same path the local survey runner uses to connect).

Callers
-------
* `resource-explorer database reproject-secrets [<slug> | --all]` -- explicit,
  reconciles a collection whose value differs as well as filling a missing one.
* RE startup (`web/app.py` lifespan, worker startup) and each resync pass
  (`egeria_resync._loop`) via `heal_missing()` -- fills only collections that
  are absent, never rewrites one an operator may have edited by hand, never
  raises, never holds a lock and never makes a network call.

Rules (SECURITY): no credential is ever printed, logged, put in an activity
row or in an exception message here. A database with no stored credential is
skipped and the skip is said; it is never written as an empty or default
collection.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from resource_explorer import omsecrets_store

log = logging.getLogger(__name__)

OPERATION = "project_secrets"

WRITTEN = "written"
UNCHANGED = "unchanged"
SKIPPED = "skipped"
ERROR = "error"

_NOT_CONFIGURED_SAID = False
#: (slug, message) pairs already recorded as error rows by the automatic path,
#: so a persistent failure (read-only directory) is one row, not one per
#: 10-minute resync pass. Cleared for a slug when it next succeeds.
_reported_errors: dict[str, str] = {}


@dataclass
class Outcome:
    slug: str
    collection: str
    status: str  # WRITTEN | UNCHANGED | SKIPPED | ERROR
    message: str = ""
    display_name: str = ""


def _safe_error(exc: BaseException) -> str:
    """A message that cannot carry a credential. ValueError is RE's own fixed
    decrypt-failure text; OSError contributes only strerror; anything else is
    reduced to its class name."""
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc.strerror or 'I/O error'}"
    return type(exc).__name__


def _registered_slugs(registry) -> list[str]:
    # Slugs only, read raw: `list_databases()` decrypts every row and one bad
    # row would raise for all of them. Each database is decrypted on its own.
    with registry._conn() as conn:
        rows = conn.execute("SELECT slug FROM databases ORDER BY slug").fetchall()
    return [r[0] for r in rows]


def _plan(registry, slugs: list[str], existing: dict, only_missing: bool):
    """Decide per database. Returns (outcomes, to_write) where to_write maps
    collection name -> (user, password, outcome)."""
    outcomes: list[Outcome] = []
    to_write: dict[str, tuple[str, str, Outcome]] = {}
    for slug in slugs:
        collection = omsecrets_store.secrets_collection_name(slug)
        outcome = Outcome(slug=slug, collection=collection, status=UNCHANGED)
        outcomes.append(outcome)
        try:
            db = registry.get_database(slug)
            if db is None:
                outcome.status, outcome.message = ERROR, "database not found in the registry"
                continue
            outcome.display_name = getattr(db, "display_name", "") or ""
            user, password = db.db_user or "", db.db_password or ""
        except Exception as exc:  # decrypt failure, DB error: this one only
            outcome.status, outcome.message = ERROR, _safe_error(exc)
            continue
        if not password or not user:
            missing = "password" if not password else "user"
            outcome.status = SKIPPED
            outcome.message = f"no stored credential ({missing} is empty); nothing to project"
            continue
        current = (existing.get(collection) or {}).get("secrets") or {}
        if collection in existing:
            if only_missing or (current.get("userId") == user
                                and current.get("clearPassword") == password):
                continue  # already present (auto) / already matches (CLI)
        to_write[collection] = (user, password, outcome)
    return outcomes, to_write


def _write_all(path: str, to_write: dict) -> None:
    data = omsecrets_store._load(path)
    for collection, (user, password, _o) in to_write.items():
        data["secretsCollections"][collection] = {
            "displayName": collection,
            "refreshTimeInterval": 60,
            "secrets": {"userId": user, "clearPassword": password},
        }
    omsecrets_store._save(path, data)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_row(registry, o: Outcome, path: str) -> None:
    from resource_explorer.registry import ActivityEntry

    if o.status == WRITTEN:
        summary = f"Projected credentials into the Egeria secrets file ({o.collection})"
        detail = ""
    else:
        summary = f"Could not project credentials for {o.slug} into the Egeria secrets file"
        detail = o.message
    try:
        registry.write_activity(ActivityEntry(
            id=str(uuid.uuid4()), ts=_now(), operation=OPERATION,
            intent="enrichment", entity_type="database", entity_slug=o.slug,
            entity_name=o.display_name, entity_location=os.path.basename(path),
            status="ok" if o.status == WRITTEN else "error",
            summary=summary, detail=detail,
        ))
    except Exception as exc:
        log.warning("omsecrets_reproject: could not write activity row for %s: %s",
                    o.slug, _safe_error(exc))


def reproject(registry, slugs: list[str] | None = None, *, only_missing: bool = False,
              path: str | None = None, dedupe_errors: bool = False) -> list[Outcome]:
    """Project credentials for `slugs` (default: every registered database).

    One atomic file write for the whole pass; one activity row per collection
    actually written, one per error; none for skipped or already-present
    databases. Returns the outcomes. Raises nothing for per-database or write
    failures (they become ERROR outcomes); the caller decides how to react."""
    target = path if path is not None else omsecrets_store.local_path()
    if not target:
        return []
    if slugs is None:
        slugs = _registered_slugs(registry)
    unreadable = ""
    if os.path.exists(target):
        try:
            import yaml

            with open(target) as f:
                yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            # `_load` treats an unparseable file as empty; writing over it
            # would destroy every other collection in it. Refuse instead.
            unreadable = "existing secrets file is unreadable (" + type(exc).__name__ + "); not overwriting it"
    existing = omsecrets_store._load(target)["secretsCollections"]
    outcomes, to_write = _plan(registry, slugs, existing, only_missing)

    if to_write and unreadable:
        for _c, (_u, _p, o) in to_write.items():
            o.status, o.message = ERROR, unreadable
    elif to_write:
        try:
            _write_all(target, to_write)
            for _c, (_u, _p, o) in to_write.items():
                o.status = WRITTEN
        except Exception as exc:
            msg = _safe_error(exc)
            for _c, (_u, _p, o) in to_write.items():
                o.status, o.message = ERROR, msg

    for o in outcomes:
        if o.status == SKIPPED:
            log.info("omsecrets_reproject: skipped %s: %s", o.slug, o.message)
        elif o.status == WRITTEN:
            _reported_errors.pop(o.slug, None)
            log.info("omsecrets_reproject: wrote collection %s", o.collection)
            _log_row(registry, o, target)
        elif o.status == ERROR:
            log.error("omsecrets_reproject: %s: %s", o.slug, o.message)
            if dedupe_errors and _reported_errors.get(o.slug) == o.message:
                continue
            _reported_errors[o.slug] = o.message
            _log_row(registry, o, target)
        else:
            _reported_errors.pop(o.slug, None)
    return outcomes


def heal_missing(registry=None) -> list[Outcome]:
    """Automatic path (startup, resync pass). Fills collections missing from
    the configured file. Never raises, never makes a network call, holds no
    lock. Does nothing -- one debug line, once -- when no path is configured."""
    global _NOT_CONFIGURED_SAID
    try:
        path = omsecrets_store.local_path()
        if not path:
            if not _NOT_CONFIGURED_SAID:
                _NOT_CONFIGURED_SAID = True
                log.debug("omsecrets_reproject: EGERIA_SECRETS_STORE_LOCAL_PATH is "
                          "not configured; nothing to project to")
            return []
        if registry is None:
            from resource_explorer.registry import ProjectRegistry

            registry = ProjectRegistry()
        return reproject(registry, only_missing=True, path=path, dedupe_errors=True)
    except Exception as exc:
        log.error("omsecrets_reproject: automatic projection failed: %s", _safe_error(exc))
        return []
