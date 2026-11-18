"""
Report specs held in Egeria, for Egeria Advisor: the EGERIA tier (brief B3, D3).

EA used to read Egeria-held specs through pyegeria's "Phase-1" convention
(`load_egeria_report_specs`: QuestionSpec glossary terms in `ReportType::` collections). That
convention holds no data (0 collections, 2026-11-10) and is not where specs live now, so it is
retired. This module fills pyegeria's EGERIA registry tier instead, with
`pyegeria.view.egeria_spec_cache.sync_report_specs`, from EA's own consumer folder:

    ReportSpecFolder::Consumer::EgeriaAdvisor      (consumer-folders decision, 2026-11-10)

"Membership is publication": putting a ReportSpec in that folder publishes it to EA, removing it
unpublishes it.

Configuration
-------------
`ADVISOR_REPORT_SPEC_FOLDERS` (comma-separated CollectionFolder qualified names), else
`PYEGERIA_REPORT_SPEC_FOLDERS` (pyegeria's own variable, as the Portal uses it), else the default
above. Set either to an empty string to configure *no* folder; the catalog then says so.

When the sync runs
------------------
- At startup, in a daemon thread, retrying with backoff while Egeria comes up. **Startup never
  waits on Egeria.** The cache from the last sync is on disk, so the tier is usable before then.
- On demand: the admin "Sync report specs from Egeria" action.

Visibility (read this before B6)
--------------------------------
The sync reads as **EA's service account** (`resolve_egeria_credentials(None)`), never as a
signed-in user, and writes a **shared** cache (`shared=True`): every EA user sees what it holds.
That is right only for **public** specs. Two consequences:

- A spec the service account cannot see is simply absent here, even if a signed-in user could see
  it. The folder itself must stay public (consumer-folders D6): a zoned folder would make the
  whole consumer silently unpublish, which `folder_not_found` below reports as "not found, or not
  visible to <service account>".
- **Zone-restricted specs must never enter this shared cache** (EA brief §4.6 revised by D5).
  pyegeria (>= 6.2.33) enforces this itself: a sync with `shared=True` skips every zoned spec as
  "zoned: not synced into a shared cache" and prunes a cached copy. The status reports how many were
  skipped. Restricted specs are read per signed-in user, cached by credential identity (B6), never
  through this module.

Recursion
---------
Consumer-folders D5: sync follows sub-folders *within* a consumer folder (cycle-safe, depth-capped).
That is pyegeria phase-2 work. `_sync_kwargs` passes `recursive=True` as soon as the installed
`sync_report_specs` accepts it; until then the sync reads the folder's direct members only, and
the status says so.

Absence
-------
`egeria_held_status()` never returns a bare empty list. It always says why nothing is held:
no folder configured / not synced yet / the folder was not found or is not visible / the folder
holds no active specs / the sync failed.
"""
from __future__ import annotations

import inspect
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

DEFAULT_FOLDER = "ReportSpecFolder::Consumer::EgeriaAdvisor"
ADVISOR_FOLDERS_ENV = "ADVISOR_REPORT_SPEC_FOLDERS"
PYEGERIA_FOLDERS_ENV = "PYEGERIA_REPORT_SPEC_FOLDERS"
CACHE_DIR_ENV = "PYEGERIA_REPORT_SPEC_CACHE_DIR"

#: Startup retry delays (seconds) while Egeria is not reachable. About 15 minutes in all; after
#: that the admin action is the way to sync.
STARTUP_BACKOFF_S = (5, 15, 30, 60, 120, 300, 300)

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "state": "not_synced",   # not_synced | running | ok | failed
    "started_at": None,
    "finished_at": None,
    "error": None,
    "report": None,          # SyncReport.to_dict() of the last completed sync
    "identity": None,        # who the last sync read as
    "attempts": 0,
}


# ── Configuration ──────────────────────────────────────────────────────────────

def configured_folders() -> List[str]:
    """EA's report spec folders. An env var set to "" means "none configured" (not the default)."""
    for var in (ADVISOR_FOLDERS_ENV, PYEGERIA_FOLDERS_ENV):
        raw = os.getenv(var)
        if raw is not None:
            return [p.strip() for p in raw.split(",") if p.strip()]
    return [DEFAULT_FOLDER]


def _folders_source() -> str:
    for var in (ADVISOR_FOLDERS_ENV, PYEGERIA_FOLDERS_ENV):
        if os.getenv(var) is not None:
            return var
    return "default"


def supports_recursion() -> bool:
    """True once the installed pyegeria's sync follows sub-folders (consumer-folders phase 2)."""
    try:
        from pyegeria.view.egeria_spec_cache import sync_report_specs
        return "recursive" in inspect.signature(sync_report_specs).parameters
    except Exception:  # noqa: BLE001
        return False


def _supports(param: str) -> bool:
    try:
        from pyegeria.view.egeria_spec_cache import sync_report_specs
        return param in inspect.signature(sync_report_specs).parameters
    except Exception:  # noqa: BLE001
        return False


def _sync_kwargs() -> Dict[str, Any]:
    """Keyword arguments for `sync_report_specs` beyond the folders.

    `shared=True`: one cache read by every EA user (see "Visibility" in the module docstring).
    `recursive=True`: only when pyegeria accepts it (consumer-folders phase 2).
    Zoned specs are kept out by pyegeria itself because the cache is shared (>= 6.2.33).
    """
    kwargs: Dict[str, Any] = {"shared": True}
    if _supports("recursive"):
        kwargs["recursive"] = True
    return kwargs


def _ensure_cache_dir(conn: Dict[str, Any]) -> None:
    """Make the tier read the cache the sync writes.

    `sync_report_specs` places its cache by the *client's* platform and view server; the registry's
    EGERIA tier finds it by pyegeria's *settings*. In EA those can differ (EA's connection comes from
    its MCP config), so a sync could fill a directory the tier never reads. Pin
    `PYEGERIA_REPORT_SPEC_CACHE_DIR` to the client's directory unless the deployment set it.
    """
    if os.getenv(CACHE_DIR_ENV):
        return
    try:
        from pyegeria.view.egeria_spec_cache import cache_dir
        os.environ[CACHE_DIR_ENV] = str(cache_dir(conn.get("platform_url"), conn.get("view_server")))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Report spec cache directory not pinned: {exc}")


# ── The client ─────────────────────────────────────────────────────────────────

def _service_connection() -> Dict[str, Any]:
    """EA's service-account connection. Deliberately NOT a signed-in user's: the cache is shared."""
    from advisor.report_pipeline import get_report_pipeline
    return get_report_pipeline()._read_pyegeria_connection(None)


def _build_client(conn: Dict[str, Any]):
    from pyegeria.egeria_tech_client import EgeriaTech
    from advisor.auth import apply_token
    client = EgeriaTech(view_server=conn["view_server"], platform_url=conn["platform_url"],
                        user_id=conn["user_id"], user_pwd=conn.get("user_pwd"))
    apply_token(client, conn.get("token"))
    return client


# ── The sync ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SyncBusy(RuntimeError):
    """A sync is already running (in this process, or another holding the cache lock)."""


SYNC_BUSY_MESSAGE = "A report spec sync is already running."


def _claim() -> None:
    """Mark a sync as running, or raise SyncBusy. Under the lock, so two starts never both run."""
    with _lock:
        if _state["state"] == "running":
            raise SyncBusy(SYNC_BUSY_MESSAGE)
        _state.update(state="running", started_at=_now(), finished_at=None, error=None)
        _state["attempts"] += 1


def sync_now(*, client_factory: Optional[Callable[[], Any]] = None, _claimed: bool = False) -> Dict[str, Any]:
    """Run one sync and return `egeria_held_status()` afterwards. Raises SyncBusy if one is running.

    Any other failure (Egeria unreachable, incomplete connection) is recorded, not raised: the
    status then says "sync failed: <why>" and the previous cache stays in force.
    """
    from pyegeria.view.egeria_spec_cache import SyncAlreadyRunning, sync_report_specs

    if not _claimed:
        _claim()
    folders = configured_folders()
    try:
        if client_factory is None:
            conn = _service_connection()
            from advisor.report_pipeline import _conn_is_complete
            if not _conn_is_complete(conn):
                raise RuntimeError("EA's Egeria connection is incomplete (view server, platform URL, "
                                   "service user and password are all needed)")
            _ensure_cache_dir(conn)
            client = _build_client(conn)
        else:
            client = client_factory()
        report = sync_report_specs(client, folders=folders, **_sync_kwargs())
    except SyncAlreadyRunning as exc:
        with _lock:
            _state.update(state="failed" if _state["report"] is None else "ok", finished_at=_now())
        raise SyncBusy(f"another process is syncing this cache: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 -- never let a sync failure escape to startup
        with _lock:
            _state.update(state="failed", finished_at=_now(), error=f"{type(exc).__name__}: {exc}")
        logger.info(f"Report spec sync from Egeria failed: {type(exc).__name__}: {exc}")
        return egeria_held_status()
    with _lock:
        _state.update(state="ok", finished_at=_now(), error=None, report=report.to_dict(),
                      identity=str(getattr(client, "user_id", "") or "?"))
    _after_sync()
    logger.info(f"Report spec sync from Egeria ({', '.join(folders) or 'no folders'}): "
                f"{len(report.added)} added, {len(report.updated)} updated, {len(report.unchanged)} unchanged, "
                f"{len(report.pruned)} pruned, {len(report.skipped)} skipped")
    return egeria_held_status()


def _after_sync() -> None:
    """The registry changed: question search and resolved dashboard sheets (their spec sources) must
    re-read it."""
    try:
        from advisor.report_pipeline import _question_index
        _question_index.invalidate()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"question index not invalidated after sync: {exc}")
    try:
        from advisor import egeria_dashboards
        egeria_dashboards.invalidate()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"dashboard cache not invalidated after sync: {exc}")


def start_sync(*, client_factory: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """Start one sync in a background thread and return at once (G2: the admin action no longer
    holds the request open while Egeria is read). Raises SyncBusy ("a sync is already running")
    when one is running; poll `sync_status()` for the outcome."""
    _claim()

    def _run():
        try:
            sync_now(client_factory=client_factory, _claimed=True)
        except SyncBusy as exc:  # another *process* holds the cache lock; recorded by sync_now
            logger.info(f"Report spec sync not run: {exc}")

    threading.Thread(target=_run, name="ea-report-spec-sync-admin", daemon=True).start()
    return sync_status()


def sync_status() -> Dict[str, Any]:
    """{"state": not_synced | running | ok | failed, "running": bool, "started_at", "finished_at",
    "error", "message", "egeria_held"}: what to poll after `start_sync`."""
    held = egeria_held_status()
    with _lock:
        st = dict(_state)
    running = st["state"] == "running"
    if running:
        message = f"Syncing report specs from Egeria (started {st['started_at']})."
    else:
        message = held.get("message") or (f"{held['count']} report spec(s) held in Egeria, from "
                                          f"{', '.join(held.get('folders') or [])}.")
    return {"state": st["state"], "running": running, "started_at": st["started_at"],
            "finished_at": st["finished_at"], "error": st["error"], "message": message, "egeria_held": held}


def start_background_sync(*, backoff: tuple = STARTUP_BACKOFF_S,
                          sleep: Callable[[float], None] = time.sleep,
                          client_factory: Optional[Callable[[], Any]] = None) -> threading.Thread:
    """Startup sync in a daemon thread. Retries while Egeria is unreachable; never blocks the caller."""

    def _run():
        delays = list(backoff)
        while True:
            try:
                status = sync_now(client_factory=client_factory)
            except SyncBusy:
                return  # someone else is syncing; their result stands
            if _state["state"] == "ok":
                return
            if not delays:
                logger.warning(f"Report spec startup sync gave up: {status.get('message')}. "
                               f"Use the admin 'Sync report specs from Egeria' action.")
                return
            sleep(delays.pop(0))

    t = threading.Thread(target=_run, name="ea-report-spec-sync", daemon=True)
    t.start()
    return t


# ── What the catalog shows ─────────────────────────────────────────────────────

def _tier_entries() -> Dict[str, Dict[str, Any]]:
    """label -> provenance for every spec now in the EGERIA tier (read from the cache on disk)."""
    try:
        from pyegeria.view.egeria_spec_cache import egeria_tier, egeria_tier_info
        return {label: (egeria_tier_info(label) or {}) for label in egeria_tier().keys()}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"EGERIA tier unreadable: {exc}")
        return {}


def egeria_held_status() -> Dict[str, Any]:
    """What EA holds from Egeria, and, when that is nothing, why. Shape:

    `{"count", "specs": [label…], "message": str|None, "reason": str|None, "state", "folders",
      "folders_source", "recursive", "folder_results": [{"folder", "result", "detail", "count"}],
      "skipped": [[who, why]…], "synced_at", "identity", "error", "visibility"}`

    `message` is None only when at least one spec is held and nothing needs saying. `reason` is a
    stable code: no_folders | not_synced | sync_failed | folder_not_found | folder_unreadable |
    folder_empty | ok.
    """
    with _lock:
        st = dict(_state)
    folders = configured_folders()
    entries = _tier_entries()
    report = st.get("report") or {}
    skipped = [list(s) for s in report.get("skipped", [])]
    skipped_by_who: Dict[str, str] = {who: why for who, why in skipped}
    identity = st.get("identity") or "the service account"

    folder_results = []
    for qn in folders:
        held = sorted(l for l, info in entries.items() if qn in (info.get("folders") or []))
        if st.get("report") is None:
            result, detail = "not_synced", None
        elif qn in skipped_by_who and "not found" in skipped_by_who[qn]:
            result, detail = "not_found", skipped_by_who[qn]
        elif qn in skipped_by_who:
            result, detail = "unreadable", skipped_by_who[qn]
        elif not held:
            result, detail = "empty", None
        else:
            result, detail = "ok", None
        folder_results.append({"folder": qn, "result": result, "detail": detail, "count": len(held)})

    labels = sorted(entries)
    out: Dict[str, Any] = {
        "count": len(labels), "specs": labels, "state": st["state"], "folders": folders,
        "folders_source": _folders_source(), "recursive": supports_recursion(),
        "folder_results": folder_results, "skipped": skipped,
        "synced_at": st.get("finished_at") if st.get("report") is not None else None,
        "identity": st.get("identity"), "error": st.get("error"),
        "visibility": ("public specs only, read as EA's service account into a cache shared by every "
                       "EA user; zone-restricted specs are not read here"),
        # B0: pyegeria (>= 6.2.33) skips zoned specs from a shared cache; say how many, from the report.
        "zone_filter": _zone_filter_words(report, skipped),
    }
    out["reason"], out["message"] = _explain(out, st, identity)
    return out


def _zone_filter_words(report: Dict[str, Any], skipped: List[List[str]]) -> str:
    """How many zoned specs the last sync kept out of the shared cache. Never "0" before a sync ran."""
    if not report:
        return "zoned specs are skipped from the shared cache; no sync has run yet"
    n = sum(1 for _who, why in skipped if str(why).startswith("zoned"))
    return f"zoned specs skipped from the shared cache: {n}"


def _explain(out: Dict[str, Any], st: Dict[str, Any], identity: str):
    folders = out["folders"]
    count = out["count"]
    scope = "" if out["recursive"] else " (direct members only; sub-folders are read once pyegeria supports it)"
    if not folders:
        return "no_folders", (f"None held in Egeria: no report spec folder is configured for Egeria Advisor "
                              f"({ADVISOR_FOLDERS_ENV} is set but empty).")
    if st["state"] == "failed":
        held = f"; showing the last synced copy ({count} spec{'s' if count != 1 else ''})" if count else ""
        return "sync_failed", f"Sync from Egeria failed: {st.get('error')}{held}."
    if st.get("report") is None:
        if count:
            return "not_synced", None if st["state"] == "running" else (
                f"Not yet synced in this run; showing the copy cached by an earlier sync ({count}).")
        doing = "syncing now" if st["state"] == "running" else "Egeria has not been reached yet"
        return "not_synced", f"None held in Egeria yet: {doing}."
    if count:
        problems = [r for r in out["folder_results"] if r["result"] in ("not_found", "unreadable")]
        if problems:
            return "ok", "Some folders were not read: " + "; ".join(
                f"{r['folder']}: {r['detail']}" for r in problems) + "."
        return "ok", None
    # Nothing held: say why, folder by folder.
    results = out["folder_results"]
    not_found = [r["folder"] for r in results if r["result"] == "not_found"]
    unreadable = [r for r in results if r["result"] == "unreadable"]
    if not_found and len(not_found) == len(results):
        names = ", ".join(not_found)
        return "folder_not_found", (f"None held in Egeria: the folder {names} was not found in Egeria, "
                                    f"or is not visible to {identity}.")
    if unreadable and len(unreadable) == len(results):
        return "folder_unreadable", ("None held in Egeria: the folder could not be read: " +
                                     "; ".join(f"{r['folder']}: {r['detail']}" for r in unreadable) + ".")
    member_skips = [s for s in out["skipped"] if s[0] not in folders]
    why = ""
    if member_skips:
        why = f" {len(member_skips)} member(s) were skipped: " + "; ".join(f"{w}: {r}" for w, r in member_skips[:5])
    found = [r["folder"] for r in results if r["result"] == "empty"]
    parts = [f"the folder {', '.join(found)} holds no active report specs{scope}.{why}"] if found else []
    if not_found:
        parts.append(f"{', '.join(not_found)} was not found, or is not visible to {identity}.")
    if unreadable:
        parts.append("; ".join(f"{r['folder']} could not be read: {r['detail']}" for r in unreadable) + ".")
    return "folder_empty", "None held in Egeria: " + " ".join(parts)


def spec_sources() -> Dict[str, Dict[str, Any]]:
    """name -> {"source", "shadows", "shadowed_by"} for every registered spec, shadowed copies included.

    `source` is builtin | egeria | local | user. pyegeria's "local" tier covers both its CONFIG
    specs (the user report specs directory) and the RUNTIME specs EA registers from its own
    document store; EA shows the latter as "user" (`advisor.report_spec_parser.ea_registered_specs`).
    """
    try:
        from pyegeria.view.base_report_formats import list_report_specs
        rows = list_report_specs(with_status=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"spec sources unavailable: {exc}")
        return {}
    try:
        from advisor.report_spec_parser import ea_registered_specs
        mine = ea_registered_specs()
    except Exception:  # noqa: BLE001
        mine = set()
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        source = row.get("source") or "local"
        if source == "local" and row["name"] in mine:
            source = "user"
        out[row["name"]] = {"source": source, "shadows": row.get("shadows"),
                            "shadowed_by": row.get("shadowed_by")}
    return out


def _reset_for_tests() -> None:
    with _lock:
        _state.update(state="not_synced", started_at=None, finished_at=None, error=None, report=None,
                      identity=None, attempts=0)
